"""V9 契约基石：分页契约测试（ADR-0138 / P4）。

- clamp_pagination 边界：0 / 负值 / 超上限 / 非法类型的钳制行为；
- Page[T] 信封字段齐备：items/total/limit/offset/has_more；
- 列表端点分页元数据 additive 完备（chat/sessions 的 has_more）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_clamp_pagination_boundaries():
    from app.schemas.pagination import MAX_PAGE_SIZE, clamp_pagination

    # 0 / 负值 → 下界钳制
    assert clamp_pagination(0, 0)[0] >= 1
    limit, offset = clamp_pagination(-5, -10)
    assert limit >= 1 and offset == 0
    # 超上限 → 钳到 MAX_PAGE_SIZE
    assert clamp_pagination(MAX_PAGE_SIZE * 100, 0)[0] == MAX_PAGE_SIZE
    # 正常值原样
    assert clamp_pagination(10, 30) == (10, 30)


def test_page_envelope_fields():
    from app.schemas.pagination import Page

    page = Page[int](items=[1, 2, 3], total=10, limit=3, offset=0, has_more=True)
    dumped = page.model_dump()
    for field in ("items", "total", "limit", "offset", "has_more"):
        assert field in dumped, f"Page 信封缺 {field}"
    assert page.has_more is True
    empty = Page[int](items=[], total=0, limit=50, offset=0, has_more=False)
    assert empty.has_more is False and empty.items == []


def test_chat_sessions_response_has_pagination_meta():
    """P4 additive：chat/sessions 响应模型携带完整分页元数据（A5 + has_more）。"""
    from app.schemas.chat_schema import SessionListResponse

    model = SessionListResponse(total=7, limit=50, offset=0, has_more=False, sessions=[])
    dumped = model.model_dump()
    assert {"total", "limit", "offset", "has_more", "sessions"} <= set(dumped)
