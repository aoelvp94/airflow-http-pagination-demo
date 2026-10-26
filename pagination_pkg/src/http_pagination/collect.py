"""Perform the same request loop ``HttpOperator.paginate_sync`` does.

Kept free of Airflow and ``requests`` so the reproduction runs on the stdlib.
A ``requests.Response`` works too: the loop only needs ``.json()`` and ``.url``.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen


class Page:
    """Minimal response: ``json()``, ``url``, ``text``, and ``content``."""

    def __init__(self, url: str, status: int, body: bytes) -> None:
        self.url = url
        self.status = status
        self.content = body
        self.text = body.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status} for {self.url}: {self.text[:200]}")


def fetch(url: str, params: dict[str, Any] | None = None, *, timeout: float = 10) -> Page:
    query = _drop_empty(params or {})
    target = url
    if query and "?" not in url:
        target = f"{url}?{urlencode(query)}"
    request = Request(target, headers={"Accept": "application/json", "User-Agent": "http-pagination-demo/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return Page(response.geturl(), response.status, response.read())


def collect(url: str, params: dict[str, Any], pager: Any, *, timeout: float = 10, hard_cap: int = 50) -> list[Page]:
    """GET ``url`` and follow ``pager`` until it returns ``None``.

    ``pager`` is an ``HttpOperator`` pagination callback: a dict with ``data``
    and/or ``endpoint``, or ``None`` to stop. Empty query values are dropped.
    """
    pages: list[Page] = []
    current_url = url
    current_params = _drop_empty(params)
    while True:
        page = fetch(current_url, current_params, timeout=timeout)
        page.raise_for_status()
        pages.append(page)
        if len(pages) > hard_cap:
            raise RuntimeError(f"stopped after {hard_cap} pages; the pager never returned None")
        nxt = pager(page)
        if not nxt:
            return pages
        endpoint = nxt.get("endpoint")
        data = nxt.get("data") if isinstance(nxt.get("data"), dict) else None
        if endpoint:
            current_url = _resolve_endpoint(page.url, endpoint)
            # An endpoint that already carries its query is the whole next request.
            current_params = {} if "?" in current_url else _drop_empty(data or {})
        else:
            merged = dict(params)
            if data:
                merged.update(data)
            current_params = _drop_empty(merged)
            current_url = url


def records_from_pages(pages: list[Page], records_key: str) -> list[Any]:
    rows: list[Any] = []
    for page in pages:
        payload = page.json()
        chunk = payload.get(records_key, []) if records_key and isinstance(payload, dict) else payload
        if not isinstance(chunk, list):
            raise RuntimeError(f"{records_key!r} is not a list in {page.url}")
        rows.extend(chunk)
    return rows


def s3_body(pages: list[Page], records_key: str | None) -> bytes:
    """Bytes an HTTP-to-S3 transfer would upload.

    ``records_key is None`` is today's ``HttpToS3Operator``: the raw body of
    the single response. A key means one JSON array of that field across every
    page, which is what a paginated transfer should write.
    """
    if records_key is None:
        if len(pages) != 1:
            raise RuntimeError("the single-response upload only applies to one page")
        return pages[0].content
    return json.dumps(records_from_pages(pages, records_key)).encode("utf-8")


def _resolve_endpoint(current_url: str, endpoint: str) -> str:
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    parsed = urlparse(current_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if endpoint.startswith("/"):
        return origin + endpoint
    return urljoin(current_url, endpoint)


def _drop_empty(params: dict[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or (isinstance(value, str) and value == ""):
            continue
        kept[key] = value
    return kept
