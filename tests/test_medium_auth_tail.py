"""PR A - Medium auth tail 的回归测试。

覆盖：
- S43: manage_analysis_asset tier=3 + session_id 所有权校验
- S47: get_engine/get_registry 返回 503 而非 RuntimeError 500
- S49: get_upload_geojson 文件大小限制
"""
import os
import pytest
from fastapi import HTTPException

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-medium-auth-tail-32c")
os.environ.setdefault("ENV", "development")


# ── S47: 启动窗口 503 ────────────────────────────────────────────────────


def test_s47_get_engine_returns_503_before_lifespan(monkeypatch):
    """S47：lifespan 完成前调 get_engine 应返回 503，不是 RuntimeError 500。"""
    from app.api.routes import chat

    monkeypatch.setattr(chat, 'engine', None)
    with pytest.raises(HTTPException) as exc_info:
        chat.get_engine()
    assert exc_info.value.status_code == 503
    # 不应包含内部模块名（防信息泄漏）
    assert "ChatEngine" not in str(exc_info.value.detail)


def test_s47_get_registry_returns_503_before_lifespan(monkeypatch):
    """S47：同上，registry 也是 503。"""
    from app.api.routes import chat

    monkeypatch.setattr(chat, 'registry', None)
    with pytest.raises(HTTPException) as exc_info:
        chat.get_registry()
    assert exc_info.value.status_code == 503


# ── S43: manage_analysis_asset tier=3 + session 校验 ────────────────────


def test_s43_manage_analysis_asset_is_tier_3():
    """S43：manage_analysis_asset 必须是 tier=3（destructive），让 PR 2 的
    /chat/tools/execute 强制要求 confirm_destructive=true。"""
    from app.tools.nature_resources import register_nature_resource_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_nature_resource_tools(registry)
    meta = registry.metadata("manage_analysis_asset")
    assert meta.get("tier") == 3, f"期望 tier=3，实际 tier={meta.get('tier')}"


def test_s43_manage_analysis_asset_rejects_cross_session(monkeypatch):
    """S43：asset.session_id 与传入 session_id 不匹配时拒绝操作。"""
    from app.tools.nature_resources import register_nature_resource_tools
    from app.tools.registry import ToolRegistry
    from app.models.upload import UploadRecord
    from unittest.mock import MagicMock

    registry = ToolRegistry()
    register_nature_resource_tools(registry)
    fn = registry._tools["manage_analysis_asset"]

    # mock db_session 返回一个 record，其 session_id 是 "session-A"
    fake_record = MagicMock()
    fake_record.session_id = "session-A"
    fake_record.id = 1
    fake_record.original_name = "ndvi.tif"

    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = fake_record

    import app.tools.nature_resources as nr_mod
    original_db_session = nr_mod.db_session
    from contextlib import contextmanager

    @contextmanager
    def fake_db_session():
        yield fake_db

    monkeypatch.setattr(nr_mod, "db_session", fake_db_session)

    # 用 session-B 调用 -> 应拒绝
    result = fn(asset_id=1, action="rename", new_name="foo", session_id="session-B")
    assert isinstance(result, dict)
    assert "error" in result
    assert "不属于当前会话" in result["error"]

    # 用 session-A 调用 -> 允许（进入 rename 分支）
    result = fn(asset_id=1, action="rename", new_name="foo", session_id="session-A")
    assert result.get("success") is True


# ── S49: get_upload_geojson 文件大小限制 ─────────────────────────────────


@pytest.mark.asyncio
async def test_s49_get_upload_geojson_rejects_oversized(monkeypatch, tmp_path):
    """S49：GeoJSON 文件超过 MAX_VECTOR_SIZE 应返回 413。"""
    from app.api.routes import upload as upload_mod
    from app.models.upload import UploadRecord
    from unittest.mock import AsyncMock, MagicMock

    # 构造一个大文件（超过 MAX_VECTOR_SIZE = 50MB）-- 用 stat 模拟大小
    fake_record = MagicMock()
    fake_record.id = 1
    fake_record.session_id = None  # 匿名会话，跳过所有权校验
    fake_record.file_type = "vector"
    fake_record.filename = str(tmp_path / "big.geojson")

    # 写一个空文件但 mock stat 返回 60MB
    (tmp_path / "big.geojson").write_text("{}")

    fake_path = MagicMock()
    fake_path.exists.return_value = True
    fake_path.resolve.return_value = tmp_path / "big.geojson"
    fake_path.stat.return_value = MagicMock(st_size=60 * 1024 * 1024)  # 60MB

    monkeypatch.setattr(upload_mod, "Path", lambda x: fake_path)
    monkeypatch.setattr(upload_mod, "_verify_session_owner", AsyncMock())

    # mock DB 查询返回 fake_record
    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = fake_record
    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=fake_result)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_async_db_session():
        yield fake_db

    monkeypatch.setattr(upload_mod, "async_db_session", fake_async_db_session)

    _mock_user = {"user_id": "test-user", "role": "viewer"}
    with pytest.raises(HTTPException) as exc_info:
        await upload_mod.get_upload_geojson(1, _mock_user)
    assert exc_info.value.status_code == 413
    assert "过大" in exc_info.value.detail
