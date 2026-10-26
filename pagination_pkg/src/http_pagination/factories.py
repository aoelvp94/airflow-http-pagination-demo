"""Factories that return an ``HttpOperator`` pagination callback.

``HttpOperator`` already pages, but only if you pass
``pagination_function(response) -> dict | None``. The dict may contain
``endpoint`` and ``data``. ``data`` is merged into the next request's query
parameters. Returning ``None`` stops.

These factories cover the four shapes that show up constantly. Each one is a
callable class so it can be stored on an operator and imported from a package.

The callback is only asked for the *next* page. The first request is whatever
``endpoint`` and ``data`` you set on the operator. Empty values are never
written into ``data``: ``?after=`` is how a missing cursor becomes "no filter".
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlparse

logger = logging.getLogger(__name__)

PageCallback = Callable[[Any], dict[str, Any] | None]


def _payload(response: Any) -> Any:
    return response.json()


def _records(payload: Any, records_key: str) -> list[Any]:
    if records_key:
        if not isinstance(payload, dict):
            return []
        rows = payload.get(records_key, [])
        return rows if isinstance(rows, list) else []
    if isinstance(payload, list):
        return payload
    return []


def _short_page(payload: Any, records: list[Any], *, page_size: int, has_more_key: str) -> bool:
    if has_more_key and isinstance(payload, dict) and not bool(payload.get(has_more_key, True)):
        return True
    return len(records) < page_size


def _drop_empty(data: dict[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key, value in data.items():
        if value is None or (isinstance(value, str) and value == ""):
            continue
        kept[key] = value
    return kept


def _dig(payload: Any, path: str) -> Any:
    node = payload
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


class _Bounded:
    def __init__(self, max_pages: int) -> None:
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        self.max_pages = max_pages
        self.responses_seen = 0

    def room_for_another_request(self) -> bool:
        """True when another request is still inside ``max_pages`` total responses."""
        self.responses_seen += 1
        return self.responses_seen < self.max_pages


class offset_pagination(_Bounded):
    """Advance ``offset`` by ``page_size`` until a short page or ``has_more_key`` is false.

    The first request must send the starting offset itself, usually ``0``.
    """

    def __init__(
        self,
        *,
        offset_param: str = "offset",
        limit_param: str = "limit",
        page_size: int = 100,
        records_key: str = "",
        has_more_key: str = "",
        max_pages: int = 500,
    ) -> None:
        super().__init__(max_pages)
        self.offset_param = offset_param
        self.limit_param = limit_param
        self.page_size = page_size
        self.records_key = records_key
        self.has_more_key = has_more_key
        self._offset = 0

    def __call__(self, response: Any) -> dict[str, Any] | None:
        if not self.room_for_another_request():
            return None
        payload = _payload(response)
        records = _records(payload, self.records_key)
        if _short_page(payload, records, page_size=self.page_size, has_more_key=self.has_more_key):
            return None
        self._offset += self.page_size
        return {"data": _drop_empty({self.offset_param: self._offset, self.limit_param: self.page_size})}


class page_pagination(_Bounded):
    """Advance a 1-based page number until a short page or ``has_more_key`` is false.

    The first request must send ``page=1`` itself.
    """

    def __init__(
        self,
        *,
        page_param: str = "page",
        limit_param: str = "page_size",
        page_size: int = 100,
        records_key: str = "",
        has_more_key: str = "",
        max_pages: int = 500,
    ) -> None:
        super().__init__(max_pages)
        self.page_param = page_param
        self.limit_param = limit_param
        self.page_size = page_size
        self.records_key = records_key
        self.has_more_key = has_more_key
        self._page = 1

    def __call__(self, response: Any) -> dict[str, Any] | None:
        if not self.room_for_another_request():
            return None
        payload = _payload(response)
        records = _records(payload, self.records_key)
        if _short_page(payload, records, page_size=self.page_size, has_more_key=self.has_more_key):
            return None
        self._page += 1
        return {"data": _drop_empty({self.page_param: self._page, self.limit_param: self.page_size})}


class cursor_pagination(_Bounded):
    """Set ``param`` to the max of ``field`` across the page.

    ``numeric=True`` compares values as integers. Plain string ``max()`` treats
    ``"999"`` as greater than ``"1000"`` and the cursor stops moving, so the
    client never asks for the rest of the listing.

    Stops when the cursor does not advance, the page is short, or ``has_more_key``
    is false. An empty cursor is never sent.
    """

    def __init__(
        self,
        *,
        param: str,
        field: str,
        limit_param: str = "limit",
        page_size: int = 100,
        records_key: str = "",
        has_more_key: str = "",
        numeric: bool = False,
        max_pages: int = 500,
    ) -> None:
        super().__init__(max_pages)
        self.param = param
        self.field = field
        self.limit_param = limit_param
        self.page_size = page_size
        self.records_key = records_key
        self.has_more_key = has_more_key
        self.numeric = numeric

    def __call__(self, response: Any) -> dict[str, Any] | None:
        if not self.room_for_another_request():
            return None
        payload = _payload(response)
        records = _records(payload, self.records_key)
        if _short_page(payload, records, page_size=self.page_size, has_more_key=self.has_more_key):
            return None
        nxt = _max_field(records, self.field, numeric=self.numeric)
        if nxt is None:
            return None
        sent = _query_value(response.url, self.param)
        if sent is not None and str(nxt) == sent:
            return None
        return {"data": _drop_empty({self.param: nxt, self.limit_param: self.page_size})}


class next_url_pagination(_Bounded):
    """Follow a next-page URL stored in the body at a dotted ``path``.

    A short page does not stop the walk. A missing pointer does. The callback
    returns the path and query only, because ``HttpHook`` joins ``endpoint``
    onto the connection host. An absolute URL on a different host is ignored
    so the connection's credentials are not sent there.
    """

    def __init__(self, *, path: str, max_pages: int = 500) -> None:
        super().__init__(max_pages)
        self.path = path

    def __call__(self, response: Any) -> dict[str, Any] | None:
        if not self.room_for_another_request():
            return None
        nxt = _dig(_payload(response), self.path)
        if not isinstance(nxt, str) or not nxt.strip():
            return None
        resolved = urljoin(response.url, nxt.strip())
        current = urlparse(response.url)
        target = urlparse(resolved)
        if target.netloc != current.netloc:
            logger.warning("Ignoring next-page URL on a different host: %s", resolved)
            return None
        if resolved == response.url:
            return None
        endpoint = target.path
        if target.query:
            endpoint = f"{endpoint}?{target.query}"
        return {"endpoint": endpoint}


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
    if not values:
        return None
    return max(values)


def _query_value(url: str, name: str) -> str | None:
    for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True):
        if key == name:
            return value
    return None
