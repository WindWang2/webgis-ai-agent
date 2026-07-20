"""Test: delete_upload must commit DB deletion BEFORE removing files.

审计 T3：之前用 AST 源码检查验证 shutil.rmtree 在 async with 块之后。
改写为行为测试：注入 DB commit 失败 + 真 tmp 文件，验证文件保留。
"""
import pytest
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from app.api.routes import upload as upload_module


@pytest.mark.asyncio
async def test_delete_upload_preserves_file_when_db_fails(tmp_path, monkeypatch):
    """DB commit 失败时，物理文件不应被删除（顺序正确性）。"""
    # 准备一个真实的上传目录和文件
    upload_dir = tmp_path / "upload-1"
    upload_dir.mkdir()
    fake_file = upload_dir / "data.geojson"
    fake_file.write_text('{"type":"FeatureCollection"}')

    # mock UploadRecord
    fake_record = MagicMock()
    fake_record.id = 1
    fake_record.session_id = None  # 匿名会话，跳过所有权校验
    fake_record.filename = str(fake_file)

    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = fake_record

    # mock DB session -- 让 db.delete 抛错（模拟 commit 失败）
    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=fake_result)
    fake_db.delete = AsyncMock(side_effect=RuntimeError("DB commit failed"))

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_async_db_session():
        yield fake_db

    monkeypatch.setattr(upload_module, "async_db_session", fake_async_db_session)
    # _verify_session_owner 在 session_id=None 时直接 return
    async def noop_verify(db, sid, uid):
        return None
    monkeypatch.setattr(upload_module, "_verify_session_owner", noop_verify)

    # 调用 delete_upload -- 应抛 RuntimeError（DB 失败）
    with pytest.raises(RuntimeError, match="DB commit failed"):
        await upload_module.delete_upload(1, {"user_id": "test", "role": "viewer"})

    # 关键断言：DB 失败时文件必须保留
    assert fake_file.exists(), (
        "DB 删除失败时文件被删了 -- delete_upload 的 DB-then-file 顺序错误"
    )
    assert upload_dir.exists(), "upload 目录也不应被清理"


@pytest.mark.asyncio
async def test_delete_upload_removes_file_when_db_succeeds(tmp_path, monkeypatch):
    """DB 成功时，物理文件应被删除。"""
    upload_dir = tmp_path / "upload-2"
    upload_dir.mkdir()
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
    fake_db.delete = AsyncMock()  # 成功

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_async_db_session():
        yield fake_db

    monkeypatch.setattr(upload_module, "async_db_session", fake_async_db_session)
    async def noop_verify(db, sid, uid):
        return None
    monkeypatch.setattr(upload_module, "_verify_session_owner", noop_verify)

    result = await upload_module.delete_upload(2, {"user_id": "test", "role": "viewer"})

    # 文件应被删除
    assert not fake_file.exists(), "DB 成功后文件应被删除"
    assert not upload_dir.exists(), "upload 目录应被清理"
    assert result["success"] is True
