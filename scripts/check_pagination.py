"""Run every pagination scenario against an in-process fake API. No Airflow."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pagination_pkg" / "src"))
sys.path.insert(0, str(ROOT / "fake_api"))

from http.server import ThreadingHTTPServer  # noqa: E402

from http_pagination.scenarios import format_report, run  # noqa: E402
from server import Handler  # noqa: E402


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        results = run(f"http://{host}:{port}")
    finally:
        server.shutdown()
    print(format_report(results))
    return 0 if all(row["ok"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
