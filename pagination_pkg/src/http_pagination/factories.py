"""Factories that return an ``HttpOperator`` pagination callback.

``HttpOperator`` already pages, but only if you pass
``pagination_function(response) -> dict | None``. The dict may contain
``endpoint`` and ``data``. ``data`` is merged into the next request's query
parameters. Returning ``None`` stops.

Every callback here is **stateless**: the next page is derived only from the
response it is given (its body and the query string of ``response.url``).
In deferrable mode ``HttpOperator`` is rebuilt after every defer, so any
counter kept on the callable would reset and the walk would repeat page 2.

There is no page cap here on purpose. Capping is the caller's job
(``collect(max_pages=...)`` in this repo, ``HttpOperator(max_pages=...)``
upstream), so it also protects hand-written callbacks.

Limitation: the current offset/page is read from the URL query, so these
helpers target GET listings. A POST body cursor needs a custom callback.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlparse

logger = logging.getLogger(__name__)

PageCallback = Callable[[Any], "dict[str, Any] | None"]


def offset_pagination(
    *,
    offset_param: str = "offset",
    limit_param: str = "limit",
    page_size: int = 100,
    records_key: str = "",
    has_more_key: str = "",
) -> PageCallback:
    """Advance ``offset_param`` by ``page_size`` until a short page or ``has_more_key`` is false."""

    def next_page(response: Any) -> dict[str, Any] | None:
        payload = response.json()
        records = _records(payload, records_key)
        if _short_page(payload, records, page_size=page_size, has_more_key=has_more_key):
            return None
        current = _int_query(response.url, offset_param, default=0)
        return {"data": {offset_param: current + page_size, limit_param: page_size}}

    return next_page


def page_number_pagination(
    *,
    page_param: str = "page",
    limit_param: str = "page_size",
    page_size: int = 100,
    records_key: str = "",
    has_more_key: str = "",
    first_page: int = 1,
) -> PageCallback:
    """Advance a page number until a short page or ``has_more_key`` is false."""

    def next_page(response: Any) -> dict[str, Any] | None:
        payload = response.json()
        records = _records(payload, records_key)
        if _short_page(payload, records, page_size=page_size, has_more_key=has_more_key):
            return None
        current = _int_query(response.url, page_param, default=first_page)
        return {"data": {page_param: current + 1, limit_param: page_size}}

    return next_page


def cursor_pagination(
    *,
    param: str,
    field: str,
    limit_param: str = "limit",
    page_size: int = 100,
    records_key: str = "",
    has_more_key: str = "",
    numeric: bool = False,
) -> PageCallback:
    """Set ``param`` to the max of ``field`` across the page.

    ``numeric=True`` compares values as integers. Plain string ``max()`` ranks
    ``"999"`` above ``"1000"`` and the cursor stops moving. Stops when the
    cursor does not advance, the page is short, or ``has_more_key`` is false.
    An empty cursor is never sent (``?after=`` often means "no filter").
    """

    def next_page(response: Any) -> dict[str, Any] | None:
        payload = response.json()
        records = _records(payload, records_key)
        if _short_page(payload, records, page_size=page_size, has_more_key=has_more_key):
            return None
        nxt = _max_field(records, field, numeric=numeric)
        if nxt is None or str(nxt) == "":
            return None
        if _query_value(response.url, param) == str(nxt):
            return None
        return {"data": {param: nxt, limit_param: page_size}}

    return next_page


def next_link_pagination(*, path: str, base_path: str = "", allow_other_hosts: bool = False) -> PageCallback:
    """Follow a next-page URL stored in the body at a dotted ``path``.

    Returns ``{"endpoint": "<path>?<query>"}`` because ``HttpHook`` joins the
    endpoint onto the connection's base URL. If that base URL already ends in
    a path (``https://api.example.com/v2``), pass ``base_path="/v2"`` so it is
    stripped and not doubled. A link on another host is refused unless
    ``allow_other_hosts`` is set, so connection credentials stay on one host.
    """
    prefix = "/" + base_path.strip("/") if base_path.strip("/") else ""

    def next_page(response: Any) -> dict[str, Any] | None:
        nxt = _dig(response.json(), path)
        if not isinstance(nxt, str) or not nxt.strip():
            return None
        resolved = urljoin(response.url, nxt.strip())
        if resolved == response.url:
            return None
        target = urlparse(resolved)
        if target.netloc != urlparse(response.url).netloc and not allow_other_hosts:
            logger.warning("Ignoring next-page URL on a different host: %s", resolved)
            return None
        endpoint = target.path
        if prefix and (endpoint == prefix or endpoint.startswith(prefix + "/")):
            endpoint = endpoint[len(prefix) :] or "/"
        if target.query:
            endpoint = f"{endpoint}?{target.query}"
        return {"endpoint": endpoint}

    return next_page


def _records(payload: Any, records_key: str) -> list[Any]:
    if records_key:
        rows = payload.get(records_key, []) if isinstance(payload, dict) else []
        return rows if isinstance(rows, list) else []
    return payload if isinstance(payload, list) else []


def _short_page(payload: Any, records: list[Any], *, page_size: int, has_more_key: str) -> bool:
    if has_more_key and isinstance(payload, dict) and not bool(payload.get(has_more_key, True)):
        return True
    return len(records) < page_size


def _dig(payload: Any, path: str) -> Any:
    node = payload
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _max_field(records: list[Any], field: str, *, numeric: bool) -> Any:
    values: list[Any] = []
    for item in records:
        if not isinstance(item, dict) or item.get(field) is None:
            continue
        value = item[field]
        if numeric:
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        values.append(value)
    return max(values) if values else None


def _query_value(url: str, name: str) -> str | None:
    for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True):
        if key == name:
            return value
    return None


def _int_query(url: str, name: str, *, default: int) -> int:
    raw = _query_value(url, name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default
