"""Pagination callbacks that plug into ``HttpOperator.pagination_function``."""

from http_pagination.collect import collect, records_from_pages, s3_body
from http_pagination.factories import (
    cursor_pagination,
    next_url_pagination,
    offset_pagination,
    page_pagination,
)

__all__ = [
    "collect",
    "cursor_pagination",
    "next_url_pagination",
    "offset_pagination",
    "page_pagination",
    "records_from_pages",
    "s3_body",
]
