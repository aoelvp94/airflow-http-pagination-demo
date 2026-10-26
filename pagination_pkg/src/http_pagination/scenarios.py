"""Assertions against the fake API. Shared by ``just check`` and the DAG."""

from __future__ import annotations

import json
import logging
from typing import Any

from http_pagination.collect import collect, fetch, records_from_pages, s3_body
from http_pagination.factories import (
    cursor_pagination,
    next_url_pagination,
    offset_pagination,
    page_pagination,
)

ORDERS = 7
CUSTOMERS = 5
EVENT_IDS = ["997", "998", "999", "1000", "1001", "1002"]


def run(base_url: str) -> list[dict[str, Any]]:
    """Return one result dict per scenario. ``ok`` is the assertion."""
    base = base_url.rstrip("/")
    checks = [
        _offset(base),
        _page(base),
        _cursor_numeric(base),
        _cursor_string_stalls(base),
        _blank_cursor_replays(base),
        _next_url(base),
        _next_url_foreign_host(base),
        _max_pages(base),
        _s3_one_page_versus_all(base),
    ]
    return checks


def format_report(results: list[dict[str, Any]]) -> str:
    width = max(len(row["name"]) for row in results)
    lines = []
    for row in results:
        mark = "pass" if row["ok"] else "FAIL"
        lines.append(f"  {row['name']:<{width}}  {mark}  {row['detail']}")
    failed = [row["name"] for row in results if not row["ok"]]
    lines.append("")
    if failed:
        lines.append(f"{len(failed)} failed: {', '.join(failed)}")
    else:
        lines.append(f"all {len(results)} scenarios passed")
    return "\n".join(lines)


def _ids(rows: list[dict[str, Any]], field: str = "id") -> list[Any]:
    return [row[field] for row in rows]


def _distinct(values: list[Any]) -> list[Any]:
    seen: list[Any] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _offset(base: str) -> dict[str, Any]:
    pages = collect(
        f"{base}/orders",
        {"offset": 0, "limit": 3},
        offset_pagination(page_size=3, records_key="orders", has_more_key="has_more", limit_param="limit"),
    )
    ids = _ids(records_from_pages(pages, "orders"))
    ok = ids == list(range(1, ORDERS + 1)) and len(pages) == 3
    return {"name": "offset", "ok": ok, "detail": f"{len(pages)} pages, ids={ids}"}


def _page(base: str) -> dict[str, Any]:
    pages = collect(
        f"{base}/customers",
        {"page": 1, "page_size": 2},
        page_pagination(page_size=2, records_key="customers", limit_param="page_size"),
    )
    ids = _ids(records_from_pages(pages, "customers"))
    ok = ids == list(range(1, CUSTOMERS + 1)) and len(pages) == 3
    return {"name": "page number", "ok": ok, "detail": f"{len(pages)} pages, ids={ids}"}


def _cursor_numeric(base: str) -> dict[str, Any]:
    pages = collect(
        f"{base}/events",
        {"limit": 2},
        cursor_pagination(param="after", field="id", page_size=2, records_key="events", numeric=True),
    )
    ids = _distinct(_ids(records_from_pages(pages, "events")))
    ok = ids == EVENT_IDS
    return {"name": "cursor numeric", "ok": ok, "detail": f"{len(pages)} pages, distinct ids={ids}"}


def _cursor_string_stalls(base: str) -> dict[str, Any]:
    """String ``max()`` treats ``"999"`` as greater than ``"1000"`` and stops."""
    pages = collect(
        f"{base}/events",
        {"limit": 2},
        cursor_pagination(param="after", field="id", page_size=2, records_key="events", numeric=False),
    )
    ids = _distinct(_ids(records_from_pages(pages, "events")))
    ok = ids == ["997", "998", "999", "1000"] and "1001" not in ids
    return {
        "name": "cursor string max",
        "ok": ok,
        "detail": f"stops early, distinct ids={ids} (1001 and 1002 never requested)",
    }


def _blank_cursor_replays(base: str) -> dict[str, Any]:
    """A handwritten callback that sends ``after=`` gets the unfiltered listing."""
    page = fetch(f"{base}/events?after=&limit=2")
    page.raise_for_status()
    payload = page.json()
    ids = _ids(payload["events"])
    ok = payload.get("unfiltered") is True and ids == EVENT_IDS
    return {
        "name": "blank cursor",
        "ok": ok,
        "detail": f"after= replayed {len(ids)} events, unfiltered={payload.get('unfiltered')}",
    }


def _next_url(base: str) -> dict[str, Any]:
    pages = collect(
        f"{base}/feeds",
        {"limit": 2},
        next_url_pagination(path="page.next"),
    )
    ids = _ids(records_from_pages(pages, "items"))
    ok = ids == ["f1", "f2", "f3", "f4"] and len(pages) == 2
    return {"name": "next url", "ok": ok, "detail": f"{len(pages)} pages, ids={ids}"}


def _next_url_foreign_host(base: str) -> dict[str, Any]:
    logs: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: logs.append(record.getMessage())  # type: ignore[method-assign]
    logger = logging.getLogger("http_pagination.factories")
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        pages = collect(f"{base}/feeds/trap", {}, next_url_pagination(path="page.next"))
    finally:
        logger.removeHandler(handler)
    ids = _ids(records_from_pages(pages, "items"))
    warned = any("different host" in message for message in logs)
    ok = len(pages) == 1 and ids == ["t1", "t2"] and warned
    return {
        "name": "next url other host",
        "ok": ok,
        "detail": f"stopped after {ids}, warned={warned}",
    }


def _max_pages(base: str) -> dict[str, Any]:
    pages = collect(
        f"{base}/orders",
        {"offset": 0, "limit": 2},
        offset_pagination(page_size=2, records_key="orders", has_more_key="has_more", max_pages=2),
    )
    ids = _ids(records_from_pages(pages, "orders"))
    ok = len(pages) == 2 and ids == [1, 2, 3, 4]
    return {"name": "max pages", "ok": ok, "detail": f"capped at {len(pages)} pages, ids={ids}"}


def _s3_one_page_versus_all(base: str) -> dict[str, Any]:
    one = collect(
        f"{base}/orders",
        {"offset": 0, "limit": 3},
        offset_pagination(page_size=3, records_key="orders", has_more_key="has_more", max_pages=1),
    )
    all_pages = collect(
        f"{base}/orders",
        {"offset": 0, "limit": 3},
        offset_pagination(page_size=3, records_key="orders", has_more_key="has_more"),
    )
    stock = json.loads(s3_body(one, None))
    proposed = json.loads(s3_body(all_pages, "orders"))
    stock_ids = _ids(stock["orders"])
    proposed_ids = _ids(proposed)
    ok = stock_ids == [1, 2, 3] and proposed_ids == list(range(1, ORDERS + 1))
    return {
        "name": "http to s3",
        "ok": ok,
        "detail": f"one response stores {stock_ids}; paginated array stores {proposed_ids}",
    }
