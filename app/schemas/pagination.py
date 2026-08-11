"""
Pagination & summary/detail DTOs for high-volume REST list endpoints.

Goal: high-growth list endpoints (projects, datasets, artifacts, workflows,
runs, data-fabric sources, catalog) must:
  1. paginate at the database (limit/offset) — never load all rows
  2. emit a SLIM summary DTO for list views (id + name + status + timestamps +
     a few headline fields)
  3. keep the full detail DTO on a per-item detail endpoint

The contract is intentionally minimal: ``Page[T]`` and ``paginate(...)`` are
the only things every route needs to import. Existing clients that still
expect a bare list will get one (we mirror the list as ``items`` while
also exposing ``total`` and ``limit``/``offset`` for new callers).
"""
from __future__ import annotations

from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageMeta(BaseModel):
    """Pagination metadata. ``total`` is the real DB count; the per-page
    list is in ``items``."""

    total: int = Field(..., ge=0, description="Total matching rows (real count)")
    limit: int = Field(..., ge=1, description="Page size applied (after clamping)")
    offset: int = Field(..., ge=0, description="Row offset applied")
    has_more: bool = Field(..., description="True if offset+limit < total")


class Page(BaseModel, Generic[T]):
    """A paginated page of items with metadata."""

    items: List[T] = Field(default_factory=list, description="Items on this page")
    total: int = Field(..., ge=0, description="Total matching rows")
    limit: int = Field(..., ge=1, description="Page size")
    offset: int = Field(..., ge=0, description="Row offset")
    has_more: bool = Field(..., description="True if more pages exist")


# Default + hard-cap for list endpoints. These match the audit's
# recommendation: a small default keeps cards/list pages fast, the cap
# blocks accidental DoS via page_size=1000000.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def clamp_pagination(limit: Optional[int], offset: Optional[int]) -> tuple[int, int]:
    """Coerce incoming query params to safe (limit, offset).

    - limit is clamped to [1, MAX_PAGE_SIZE], default DEFAULT_PAGE_SIZE
    - offset is clamped to [0, +inf), default 0
    - negative or None values fall back to the default
    """
    if limit is None or limit < 1:
        limit = DEFAULT_PAGE_SIZE
    if limit > MAX_PAGE_SIZE:
        limit = MAX_PAGE_SIZE
    if offset is None or offset < 0:
        offset = 0
    return limit, offset
