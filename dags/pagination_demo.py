"""Run the pagination scenarios from an Airflow task, against the compose API."""

from __future__ import annotations

import os

from airflow.sdk import dag, task


@dag(dag_id="pagination_demo", schedule=None, catchup=False, tags=["pagination", "demo"])
def pagination_demo():
    @task
    def run_scenarios() -> list[dict]:
        from http_pagination.scenarios import format_report, run

        base = os.environ.get("FAKE_API_BASE", "http://fakeapi:8088")
        results = run(base)
        print(format_report(results))
        failed = [row["name"] for row in results if not row["ok"]]
        if failed:
            raise RuntimeError(f"pagination scenarios failed: {failed}")
        return results

    run_scenarios()


pagination_demo()
