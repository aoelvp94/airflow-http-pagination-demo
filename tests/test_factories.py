"""Unit tests in the style the Airflow HTTP provider uses: pytest + requests_mock."""

from __future__ import annotations

import logging

import pytest
import requests

from http_pagination.factories import (
    cursor_pagination,
    next_link_pagination,
    offset_pagination,
    page_number_pagination,
)

BASE = "https://api.example.com"


def get(url: str) -> requests.Response:
    return requests.get(url, timeout=5)


def walk(first_url: str, pager, *, max_pages: int = 50) -> list[requests.Response]:
    """Mimic HttpOperator.paginate_sync: merge ``data`` into the query or follow ``endpoint``."""
    responses = [get(first_url)]
    while len(responses) < max_pages:
        nxt = pager(responses[-1])
        if not nxt:
            break
        if "endpoint" in nxt:
            url = BASE + nxt["endpoint"]
        else:
            url = requests.Request("GET", first_url.split("?")[0], params=nxt["data"]).prepare().url
        responses.append(get(url))
    return responses


class TestOffset:
    def test_walks_until_has_more_false(self, requests_mock):
        requests_mock.get(f"{BASE}/orders?offset=0&limit=2", json={"orders": [1, 2], "has_more": True})
        requests_mock.get(f"{BASE}/orders?offset=2&limit=2", json={"orders": [3, 4], "has_more": True})
        requests_mock.get(f"{BASE}/orders?offset=4&limit=2", json={"orders": [5], "has_more": False})
        pager = offset_pagination(page_size=2, records_key="orders", has_more_key="has_more")
        rows = [r for resp in walk(f"{BASE}/orders?offset=0&limit=2", pager) for r in resp.json()["orders"]]
        assert rows == [1, 2, 3, 4, 5]

    def test_is_stateless(self, requests_mock):
        """Same response in, same next page out: safe across deferrable resumes."""
        requests_mock.get(f"{BASE}/orders?offset=4&limit=2", json={"orders": [5, 6]})
        response = get(f"{BASE}/orders?offset=4&limit=2")
        assert offset_pagination(page_size=2, records_key="orders")(response) == {"data": {"offset": 6, "limit": 2}}
        assert offset_pagination(page_size=2, records_key="orders")(response) == {"data": {"offset": 6, "limit": 2}}

    def test_short_page_stops(self, requests_mock):
        requests_mock.get(f"{BASE}/orders?offset=0&limit=3", json={"orders": [1]})
        assert offset_pagination(page_size=3, records_key="orders")(get(f"{BASE}/orders?offset=0&limit=3")) is None


class TestPageNumber:
    def test_next_page_from_url(self, requests_mock):
        requests_mock.get(f"{BASE}/customers?page=3&page_size=2", json={"customers": [1, 2]})
        pager = page_number_pagination(page_size=2, records_key="customers")
        assert pager(get(f"{BASE}/customers?page=3&page_size=2")) == {"data": {"page": 4, "page_size": 2}}

    def test_missing_page_param_uses_first_page(self, requests_mock):
        requests_mock.get(f"{BASE}/customers", json=[1, 2])
        assert page_number_pagination(page_size=2)(get(f"{BASE}/customers"))["data"]["page"] == 2


class TestCursor:
    @pytest.mark.parametrize(("numeric", "expected"), [(True, 1000), (False, "999")])
    def test_numeric_vs_string_max(self, requests_mock, numeric, expected):
        requests_mock.get(f"{BASE}/events", json={"events": [{"id": "999"}, {"id": "1000"}]})
        pager = cursor_pagination(param="after", field="id", page_size=2, records_key="events", numeric=numeric)
        assert pager(get(f"{BASE}/events"))["data"]["after"] == expected

    def test_stops_when_cursor_does_not_move(self, requests_mock):
        requests_mock.get(f"{BASE}/events?after=1000", json={"events": [{"id": "999"}, {"id": "1000"}]})
        pager = cursor_pagination(param="after", field="id", page_size=2, records_key="events", numeric=True)
        assert pager(get(f"{BASE}/events?after=1000")) is None

    def test_never_sends_empty_cursor(self, requests_mock):
        requests_mock.get(f"{BASE}/events", json={"events": [{"id": ""}, {"id": ""}]})
        pager = cursor_pagination(param="after", field="id", page_size=2, records_key="events")
        assert pager(get(f"{BASE}/events")) is None


class TestNextLink:
    def test_relative_link(self, requests_mock):
        requests_mock.get(f"{BASE}/feeds", json={"page": {"next": "/feeds?cursor=2"}})
        assert next_link_pagination(path="page.next")(get(f"{BASE}/feeds")) == {"endpoint": "/feeds?cursor=2"}

    def test_other_host_refused(self, requests_mock, caplog):
        requests_mock.get(f"{BASE}/feeds", json={"page": {"next": "https://evil.invalid/steal"}})
        with caplog.at_level(logging.WARNING):
            assert next_link_pagination(path="page.next")(get(f"{BASE}/feeds")) is None
        assert "different host" in caplog.text

    def test_other_host_allowed_when_opted_in(self, requests_mock):
        requests_mock.get(f"{BASE}/feeds", json={"page": {"next": "https://cdn.example.com/feeds?c=2"}})
        pager = next_link_pagination(path="page.next", allow_other_hosts=True)
        assert pager(get(f"{BASE}/feeds")) == {"endpoint": "/feeds?c=2"}

    def test_base_path_not_doubled(self, requests_mock):
        """Connection host https://api.example.com/v2 + endpoint must not become /v2/v2/feeds."""
        requests_mock.get(f"{BASE}/v2/feeds", json={"page": {"next": "/v2/feeds?cursor=2"}})
        pager = next_link_pagination(path="page.next", base_path="/v2")
        assert pager(get(f"{BASE}/v2/feeds")) == {"endpoint": "/feeds?cursor=2"}

    def test_self_link_stops(self, requests_mock):
        requests_mock.get(f"{BASE}/feeds?cursor=2", json={"page": {"next": "/feeds?cursor=2"}})
        assert next_link_pagination(path="page.next")(get(f"{BASE}/feeds?cursor=2")) is None


def test_cap_is_the_callers_job(requests_mock):
    """A pager that never stops is bounded by the loop, not by the pager."""
    requests_mock.get(f"{BASE}/loop", json={"page": {"next": "/loop?x=1"}})
    requests_mock.get(f"{BASE}/loop?x=1", json={"page": {"next": "/loop"}})
    assert len(walk(f"{BASE}/loop", next_link_pagination(path="page.next"), max_pages=4)) == 4
