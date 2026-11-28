"""WP5 Pi 发布工具族测试 —— 注册契约、只读边界、同意红线。

红线断言：工具族不提供 publish/link 能力（发布/链接签发是作者显式动作）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_publication_tools_registered():
    """工具经 init_tools 注册进 registry（schema 自动生成）。"""
    from app.tools.registry import ToolRegistry

    from app.tools.publication_tools import register_publication_tools

    registry = ToolRegistry()
    register_publication_tools(registry)
    for name in (
        "publication_inspect",
        "publication_prepare",
        "publication_draft",
        "publication_validate",
    ):
        assert registry.get(name) is not None, name


def test_tool_family_has_no_publish_or_link_capability():
    """同意红线：不允许出现 publication_publish / publication_link 类工具。"""
    from app.tools.registry import ToolRegistry

    from app.tools.publication_tools import register_publication_tools

    registry = ToolRegistry()
    register_publication_tools(registry)
    assert registry.get("publication_publish") is None
    assert registry.get("publication_link") is None
    assert registry.get("publication_revoke") is None


def test_draft_tool_declared_artifact_creation_only():
    """draft 工具是 artifact_creation（私有）；不得声明对外可见副作用。"""
    from app.tools.registry import ToolRegistry

    from app.tools.publication_tools import register_publication_tools

    registry = ToolRegistry()
    register_publication_tools(registry)
    draft = registry.get("publication_draft")
    meta = getattr(draft, "meta", None) or getattr(draft, "_tool_meta", None) or {}
    side_effect = meta.get("side_effect") if isinstance(meta, dict) else None
    assert side_effect in (None, "artifact_creation")


@pytest.mark.asyncio
async def test_inspect_denies_unverified_session(monkeypatch):
    """session 归属校验 fail-closed（无运行时上下文 → 拒绝）。"""
    from app.tools.publication_tools import register_publication_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_publication_tools(registry)
    tool = registry.get("publication_inspect")

    async def _no_ctx():
        return None

    import app.tools.upload_tools as upload_tools

    monkeypatch.setattr(
        upload_tools, "current_runtime_context", lambda: type("C", (), {"session_id": None})()
    )
    handler = getattr(tool, "handler", None) or getattr(tool, "func", None) or tool
    result = handler(session_id="sess-any-1234")
    import inspect

    if inspect.isawaitable(result):
        result = await result
    assert result.get("code") == "session_forbidden"
