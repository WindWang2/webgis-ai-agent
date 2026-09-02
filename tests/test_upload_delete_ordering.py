"""Test: delete_upload must commit DB deletion BEFORE removing files.

审计 T3：之前用 AST 源码检查验证 shutil.rmtree 在 async with 块之后。
改写为行为测试：注入 DB commit 失败 + 真 tmp 文件，验证文件保留。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.api.routes import upload as upload_module


@pytest.mark.asyncio
async def test_delete_upload_preserves_file_when_db_fails(tmp_path, monkeypatch):
    """DB commit 失败时，物理文件不应被删除（顺序正确性）。"""
    monkeypatch.setattr(upload_module.settings, "DATA_DIR", str(tmp_path))
    upload_dir = tmp_path / "uploads" / "upload-1"
    upload_dir.mkdir(parents=True)
    fake_file = upload_dir / "data.geojson"
    fake_file.write_text('{"type":"FeatureCollection"}')

    fake_record = MagicMock()
    fake_record.id = 1
    fake_record.session_id = None
    fake_record.filename = str(fake_file)

    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = fake_record

    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=fake_result)
    fake_db.delete = AsyncMock(side_effect=RuntimeError("DB commit failed"))

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_async_db_session():
        yield fake_db

    monkeypatch.setattr(upload_module, "async_db_session", fake_async_db_session)

    async def noop_verify(db, sid, uid, owner_token=None):
        return None

    monkeypatch.setattr(upload_module, "_verify_session_owner", noop_verify)

    with pytest.raises(RuntimeError, match="DB commit failed"):
        await upload_module.delete_upload(1, {"user_id": "test", "role": "viewer"})

    assert fake_file.exists(), (
        "DB 删除失败时文件被删了 -- delete_upload 的 DB-then-file 顺序错误"
    )
    assert upload_dir.exists(), "upload 目录也不应被清理"


@pytest.mark.asyncio
async def test_delete_upload_removes_file_when_db_succeeds(tmp_path, monkeypatch):
    """DB 成功时，物理文件应被删除。"""
    monkeypatch.setattr(upload_module.settings, "DATA_DIR", str(tmp_path))
    upload_dir = tmp_path / "uploads" / "upload-2"
    upload_dir.mkdir(parents=True)
    fake_file = upload_dir / "data.geojson"
    fake_file.write_text('{"type":"FeatureCollection"}')

    fake_record = MagicMock()
    fake_record.id = 2
    fake_record.session_id = None
    fake_record.filename = str(fake_file)

    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = fake_record

    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=fake_result)
    fake_db.delete = AsyncMock()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_async_db_session():
        yield fake_db

    monkeypatch.setattr(upload_module, "async_db_session", fake_async_db_session)

    async def noop_verify(db, sid, uid, owner_token=None):
        return None

    monkeypatch.setattr(upload_module, "_verify_session_owner", noop_verify)

    result = await upload_module.delete_upload(2, {"user_id": "test", "role": "viewer"})

    assert not fake_file.exists(), "DB 成功后文件应被删除"
    assert not upload_dir.exists(), "upload 目录应被清理"
    assert result["success"] is True
