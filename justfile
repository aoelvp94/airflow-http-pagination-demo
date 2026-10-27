set shell := ["bash", "-cu"]

default:
    @just --list

# Run the nine scenarios against an in-process fake API. No Docker.
check:
    python3 scripts/check_pagination.py

# pytest + requests_mock unit tests for the factories. No Docker.
test:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -x .venv/bin/python ]; then
        python3 -m venv .venv
        .venv/bin/pip install -q -e "pagination_pkg[test]"
    fi
    .venv/bin/python -m pytest -q tests

# Airflow 3 plus the fake API container
up:
    astro dev start --no-browser

# Run the DAG task. The report is the same one `just check` prints.
task:
    #!/usr/bin/env bash
    set -euo pipefail
    scheduler=$(docker ps -qf name=scheduler | head -1)
    [ -n "$scheduler" ] || { echo "scheduler is not running — run 'just up' first"; exit 1; }
    docker exec -e FAKE_API_BASE=http://fakeapi:8088 "$scheduler" \
        airflow tasks test pagination_demo run_scenarios 2026-01-01

e2e: up task

down:
    astro dev stop

clean: down
    astro dev kill || true
