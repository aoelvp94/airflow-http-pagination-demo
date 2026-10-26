# HTTP pagination, and HTTP to object storage

A local reproduction of two gaps in the Airflow providers:

1. `HttpOperator` pages only if you hand it a `pagination_function`. There is no
   offset, page-number, cursor, or next-URL helper, and the loop has no page cap.
2. `HttpToS3Operator` stores one response body at one key. It does not accept
   that callback, so a listing that spans pages never becomes one object.

Everything in `just check` is local: a fake JSON API in-process, no Airflow, no
cloud. `just e2e` runs the same assertions as an Airflow task against that API
in Docker.

```
hand-written callback                         factories in this repo

you read the body                             offset_pagination(...)
you decide the next query                     page_pagination(...)
you remember to stop                          cursor_pagination(..., numeric=True)
you hope the cursor moves                     next_url_pagination(path="page.next")
```

## What the fake API does

| endpoint | shape |
|---|---|
| `GET /orders?offset=&limit=` | offset, `has_more` |
| `GET /customers?page=&page_size=` | 1-based page number |
| `GET /events?after=&limit=` | inclusive cursor. An empty `after=` returns the whole history |
| `GET /feeds?limit=` | next page is a URL in `page.next` |
| `GET /feeds/trap` | `page.next` points at `http://evil.invalid/steal` |

The event ids are the strings `"997"` … `"1002"`. String `max()` ranks `"999"`
above `"1000"`, so a cursor that does not compare them as integers stops and
never requests `1001` and `1002`.

## Reproduce

```bash
just check
```

That needs Python 3.10+ and nothing else. Sample output:

```
  offset               pass  3 pages, ids=[1, 2, 3, 4, 5, 6, 7]
  page number          pass  3 pages, ids=[1, 2, 3, 4, 5]
  cursor numeric       pass  6 pages, distinct ids=['997', '998', '999', '1000', '1001', '1002']
  cursor string max    pass  stops early, distinct ids=['997', '998', '999', '1000'] (1001 and 1002 never requested)
  blank cursor         pass  after= replayed 6 events, unfiltered=True
  next url             pass  2 pages, ids=['f1', 'f2', 'f3', 'f4']
  next url other host  pass  stopped after ['t1', 't2'], warned=True
  max pages            pass  capped at 2 pages, ids=[1, 2, 3, 4]
  http to s3           pass  one response stores [1, 2, 3]; paginated array stores [1, 2, 3, 4, 5, 6, 7]

all 9 scenarios passed
```

The numeric cursor takes more requests than the row count because the fake API
is inclusive: the boundary id is returned again on the next page. Distinct ids
are the whole listing. The string cursor stops on the page that contains both
`"999"` and `"1000"`.

`http to s3` is the storage difference. One response, which is what
`HttpToS3Operator` uploads today, holds the first page only (`[1, 2, 3]`).
Walking the pages and uploading one JSON array holds `[1, 2, 3, 4, 5, 6, 7]`.

## The same checks inside Airflow

Requires [Docker](https://docs.docker.com/get-docker/), the
[Astro CLI](https://www.astronomer.io/docs/astro/cli/install-cli), and
[just](https://github.com/casey/just).

```bash
just e2e
```

`astro dev start` brings up Airflow 3 and the fake API. The task
`pagination_demo.run_scenarios` imports the same `run()` the local check uses
and prints the same report. The UI is at http://localhost:8080. The API is at
http://localhost:8088/health.

`just down` stops it. `just clean` deletes the local Astro state.

## Where this would land upstream

- Factories: a module next to
  [`HttpOperator`](https://github.com/apache/airflow/blob/main/providers/http/src/airflow/providers/http/operators/http.py),
  returning the callback `paginate_sync` already calls.
- Transfer: [`HttpToS3Operator.execute`](https://github.com/apache/airflow/blob/main/providers/amazon/src/airflow/providers/amazon/aws/transfers/http_to_s3.py)
  gains the same `pagination_function`. With `records_key` set, it uploads one
  JSON array. With the argument unset, it keeps uploading the single raw body.

The code here is the reproduction, not a provider patch. `pagination_pkg/` is
what a reviewer would read.

## Layout

```
pagination_pkg/     the factories, the request loop, and the assertions
fake_api/server.py  the JSON API
scripts/check_pagination.py
dags/pagination_demo.py
docker-compose.override.yml
```
