"""Fake JSON API used by the pagination reproduction.

Four list styles, all deterministic, nothing fetched from anywhere else:

- ``GET /orders?offset=&limit=`` offset, plus ``has_more``
- ``GET /customers?page=&page_size=`` 1-based page number
- ``GET /events?after=&limit=`` inclusive cursor. ``after=`` with an empty
  value returns the whole history (``unfiltered: true``)
- ``GET /feeds?limit=`` next page URL in ``page.next``
- ``GET /feeds/trap`` ``page.next`` points at a different host
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ORDERS = [{"id": index, "sku": f"sku-{index}"} for index in range(1, 8)]
CUSTOMERS = [{"id": index, "name": f"customer-{index}"} for index in range(1, 6)]
EVENTS = [{"id": event_id, "name": f"event-{event_id}"} for event_id in ("997", "998", "999", "1000", "1001", "1002")]
FEEDS = [{"id": f"f{index}", "title": f"feed-{index}"} for index in range(1, 5)]


def _int_arg(query: dict[str, list[str]], name: str, default: int) -> int:
    raw = query.get(name, [None])[0]
    if raw is None or raw == "":
        return default
    return int(raw)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == "/health":
            self._send({"ok": True})
            return
        if parsed.path == "/orders":
            self._send(self._orders(query))
            return
        if parsed.path == "/customers":
            self._send(self._customers(query))
            return
        if parsed.path == "/events":
            self._send(self._events(query))
            return
        if parsed.path == "/feeds":
            self._send(self._feeds(query))
            return
        if parsed.path == "/feeds/trap":
            self._send(self._trap())
            return
        self._send({"error": "not found"}, status=404)

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _orders(self, query: dict[str, list[str]]) -> dict[str, object]:
        offset = _int_arg(query, "offset", 0)
        limit = _int_arg(query, "limit", 3)
        window = ORDERS[offset : offset + limit]
        return {"orders": window, "has_more": offset + limit < len(ORDERS)}

    def _customers(self, query: dict[str, list[str]]) -> dict[str, object]:
        page = _int_arg(query, "page", 1)
        page_size = _int_arg(query, "page_size", 2)
        start = (page - 1) * page_size
        return {"customers": CUSTOMERS[start : start + page_size]}

    def _events(self, query: dict[str, list[str]]) -> dict[str, object]:
        limit = _int_arg(query, "limit", 2)
        if "after" in query and query["after"][0] == "":
            return {"events": EVENTS, "unfiltered": True}
        if "after" in query and query["after"][0] != "":
            cursor = int(query["after"][0])
            matched = [row for row in EVENTS if int(row["id"]) >= cursor]
        else:
            matched = list(EVENTS)
        return {"events": matched[:limit], "unfiltered": False}

    def _feeds(self, query: dict[str, list[str]]) -> dict[str, object]:
        limit = _int_arg(query, "limit", 2)
        cursor = query.get("cursor", ["0"])[0]
        start = int(cursor)
        window = FEEDS[start : start + limit]
        nxt = start + limit
        next_url = None if nxt >= len(FEEDS) else f"/feeds?cursor={nxt}&limit={limit}"
        return {"items": window, "page": {"next": next_url}}

    def _trap(self) -> dict[str, object]:
        return {
            "items": [{"id": "t1"}, {"id": "t2"}],
            "page": {"next": "http://evil.invalid/steal"},
        }

    def _send(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(host: str = "127.0.0.1", port: int = 8088) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"fake api listening on http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    serve(host=os.environ.get("FAKE_API_HOST", "0.0.0.0"), port=int(os.environ.get("FAKE_API_PORT", "8088")))
