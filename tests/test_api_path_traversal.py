"""Security regression: report download path traversal (audit Wave 15, gap #1).

历史问题：本文件曾在测试内**复写** ``report.py:_validate_file_path`` 的逻辑并
对副本断言 —— 生产 helper 回归（如 ``realpath`` 被改成 ``abspath``）时本文件
依然全绿（伪覆盖）。现改为：

1. 直接 import 路由模块里的真实 ``_validate_file_path``，打真实攻击向量
   （``..``、绝对路径、前缀兄弟目录、planted symlink）；
2. 端到端走真实 ``GET /reports/{id}/download`` 路由（真实所有权守卫 +
   真实 validator 调用点），证明路由确实经过 validator，而不是绕过它。
"""
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes.report import _validate_file_path  # 真实实现，非测试副本


# ── 真实 validator 契约 ───────────────────────────────────────────────────


class TestRealValidatorContract:
    """直接打 ``app.api.routes.report._validate_file_path``（realpath 前缀门）。"""

    def test_rejects_absolute_path_outside_root(self, tmp_path):
        assert _validate_file_path("/etc/passwd", str(tmp_path)) is False

    def test_rejects_dotdot_traversal(self, tmp_path):
        attack = str(tmp_path / ".." / ".." / "etc" / "passwd")
        assert _validate_file_path(attack, str(tmp_path)) is False

    def test_rejects_parent_relative_outside_root(self, tmp_path):
        assert _validate_file_path("../secret.db", str(tmp_path)) is False

    def test_rejects_sibling_prefix_dir(self, tmp_path):
        # 前缀兄弟目录攻击：/data/reportsevil 不得匹配 /data/reports
        sibling = tmp_path.parent / (tmp_path.name + "evil") / "x.pdf"
        assert _validate_file_path(str(sibling), str(tmp_path)) is False

    def test_rejects_planted_symlink_escaping_root(self, tmp_path):
        # realpath 语义：root 内的 symlink 指向外部 → 解析后在外 → 拒绝
        planted = tmp_path / "planted.pdf"
        os.symlink("/etc/passwd", planted)
        assert _validate_file_path(str(planted), str(tmp_path)) is False

    def test_rejects_symlinked_root_component(self, tmp_path):
        # root 自身经 symlink 绕行也必须按解析后路径比对（双侧 realpath）
        decoy = tmp_path / "decoy"
        decoy.mkdir()
        link_dir = tmp_path / "link"
        os.symlink(decoy, link_dir)
        real_outside = tmp_path / "outside.txt"
        real_outside.write_text("x")
        os.symlink(real_outside, decoy / "f.pdf")
        # 通过 link 目录访问最终解析到 tmp_path/outside.txt —— 仍在 root 内
        assert _validate_file_path(str(link_dir / "f.pdf"), str(tmp_path)) is True

    def test_accepts_real_file_inside_root(self, tmp_path):
        f = tmp_path / "report.pdf"
        f.write_text("%PDF-1.4")
        assert _validate_file_path(str(f), str(tmp_path)) is True

    def test_accepts_subdirectory_file(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        f = sub / "report.pdf"
        f.write_text("x")
        assert _validate_file_path(str(f), str(tmp_path)) is True

    def test_accepts_root_itself(self, tmp_path):
        assert _validate_file_path(str(tmp_path), str(tmp_path)) is True

    def test_percent_encoding_is_treated_as_literal_filename(self, tmp_path):
        # 契约 pin：validator 是 raw-path 判定，不做 URL 解码 —— 解码发生在
        # 框架层（Starlette 在路由参数里已解码）。 encoded 形式在这里只是
        # root 内一个（怪异的）文件名，不是遍历；含解码后 ``..`` 的输入
        # 会被上面 test_rejects_dotdot_traversal 抓住。
        assert _validate_file_path(str(tmp_path / "..%2F..%2Fetc%2Fpasswd"), str(tmp_path)) is True


# ── 端到端：真实 download 路由必须经过 validator ─────────────────────────


@pytest_asyncio.fixture
async def report_env(tmp_path, monkeypatch):
    """隔离 aiosqlite + report 路由 + 指到 tmp 的 REPORT_DIR。"""
    from app.models.db_model import Base, Conversation
    import app.models.report  # noqa: F401  # 确保 reports 表注册进 metadata
    from app.core.database import get_async_db
    from app.api.routes import report as report_routes

    db_file = tmp_path / "reports-traversal.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(bind=sync_engine)
    sync_engine.dispose()

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def override_get_async_db():
        async with session_factory() as db:
            yield db

    # REPORT_DIR 钉到 tmp：合法路径用例不写仓库 data/ 目录
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    monkeypatch.setattr(report_routes, "REPORT_DIR", str(report_dir))

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(report_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_get_async_db
    from app.core.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: {"user_id": "owner-1"}

    # 所有权链前置数据：会话属于 owner-1（download 路由先过 verify_session_owner）
    async with session_factory() as db:
        db.add(Conversation(id="sess-1", user_id="owner-1"))
        await db.commit()

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")

    async def seed_report(report_id: str, file_path: str) -> None:
        from app.models.report import Report

        async with session_factory() as db:
            db.add(Report(
                id=report_id,
                session_id="sess-1",
                title="t",
                format="pdf",
                status="completed",
                file_path=file_path,
            ))
            await db.commit()

    yield client, seed_report, report_dir
    await client.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_download_rejects_absolute_path_outside_report_dir(report_env):
    client, seed, _ = report_env
    await seed("r-abs", "/etc/passwd")
    resp = await client.get("/api/v1/reports/r-abs/download")
    assert resp.status_code == 400
    assert "非法文件路径" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_download_rejects_dotdot_traversal_path(report_env):
    client, seed, report_dir = report_env
    outside = report_dir.parent / "secret.pdf"
    outside.write_text("secret")
    traversal = str(report_dir / ".." / outside.name)
    await seed("r-dotdot", traversal)
    resp = await client.get("/api/v1/reports/r-dotdot/download")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_download_rejects_symlink_escape_planted_in_report_dir(report_env):
    client, seed, report_dir = report_env
    planted = report_dir / "planted.pdf"
    os.symlink("/etc/passwd", planted)
    await seed("r-sym", str(planted))
    resp = await client.get("/api/v1/reports/r-sym/download")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_download_serves_legitimate_file_inside_report_dir(report_env):
    # 正向对照：合法文件确实能下载（证明上面的 400 不是路由坏了）
    client, seed, report_dir = report_env
    legit = report_dir / "ok.pdf"
    legit.write_text("%PDF-1.4 legit")
    await seed("r-ok", str(legit))
    resp = await client.get("/api/v1/reports/r-ok/download")
    assert resp.status_code == 200
    assert b"legit" in resp.content
