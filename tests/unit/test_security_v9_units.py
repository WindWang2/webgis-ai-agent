"""V9 安全与多租户单元测试集（ADR-0139 P2–P7）。

覆盖：
- tenancy：effective org 解析（匿名 → default 桶）、scoped_query fail-loud；
- scopes：封闭词表、角色映射、claim 回退/降级、require_scope 词表外拒绝；
- password policy：长度/字符类/常见表 + 渐进延迟阶梯；
- org quota：默认值、per-org 覆盖、并发越限 429（QUOTA 分类）、审计落库；
- audit：record/query、词表外 action 归类、traceparent 解析；
- metrics/health 门禁：/metrics 无 token 401、/healthz 公开、/health 管理员；
- refresh rotation：轮换前移 current_jti、重放 → 家族全失效。
"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-v9-units-32-chars-ok")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.integration


# ══ tenancy ══════════════════════════════════════════════════════════════

async def _fresh_db(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.db_model import Base

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v9.db",
        connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    return engine, maker


async def test_effective_org_anonymous_falls_to_default_bucket(tmp_path):
    from sqlalchemy import select

    from app.core import tenancy
    from app.models.db_model import Organization

    engine, maker = await _fresh_db(tmp_path)
    try:
        async with maker() as db:
            org = await tenancy.effective_org_id({"user_id": "anonymous",
                                                  "role": "anonymous"}, db)
            assert org.isdigit()  # default org 已创建且为 organizations.id
            slug = (await db.execute(
                select(Organization.slug).where(
                    Organization.id == int(org))
            )).scalar_one()
            assert slug == tenancy.DEFAULT_ORG_SLUG
            # 二次解析命中缓存且稳定
            assert await tenancy.effective_org_id(None, db) == org
    finally:
        await engine.dispose()


async def test_effective_org_jwt_claim_wins(tmp_path):
    from app.core import tenancy

    engine, maker = await _fresh_db(tmp_path)
    try:
        async with maker() as db:
            org = await tenancy.effective_org_id(
                {"user_id": "u1", "role": "editor", "org_id": 42}, db)
            assert org == "42"
    finally:
        await engine.dispose()


def test_scoped_query_rejects_model_without_org():
    from sqlalchemy import literal_column, select

    from app.core.tenancy import scoped_query

    class _NoOrg:  # 无 org_id 列的模型（控制面表形态）
        pass

    with pytest.raises(TypeError):
        scoped_query(select(literal_column("1")), _NoOrg, "1")


# ══ scopes ═══════════════════════════════════════════════════════════════

def test_scope_vocabulary_shape():
    from app.core.scopes import SCOPES

    assert all(":" in s for s in SCOPES)
    # 任务书示例词全部在词汇表
    for s in ("gis:read", "gis:write", "projects:read", "lakehouse:read",
              "geocompute:submit", "admin:read", "admin:write"):
        assert s in SCOPES


def test_role_scopes_monotonic():
    from app.core.scopes import ROLE_SCOPES

    assert ROLE_SCOPES["viewer"] < ROLE_SCOPES["editor"] < ROLE_SCOPES["admin"]
    anon = {"public:read", "session:read", "session:write"}
    assert anon <= ROLE_SCOPES["viewer"]


def test_parse_scopes_fallback_and_demotion():
    from app.core.scopes import parse_scopes, scopes_for_role

    # claim 缺席 → 角色默认
    assert parse_scopes(None, "editor") == scopes_for_role("editor")
    # 未知词丢弃；越权词即使进了 claim 也被角色基线钳制
    granted = parse_scopes("gis:read admin:write bogus:all", "viewer")
    assert "gis:read" in granted and "admin:write" not in granted \
        and "bogus:all" not in granted


def test_require_scope_unknown_is_programming_error():
    from app.core.scopes import require_scope

    with pytest.raises(ValueError):
        require_scope("nope:nothing")


# ══ password policy ══════════════════════════════════════════════════════

def test_password_policy_rules():
    from app.core.password_policy import (
        PasswordPolicyError,
        validate_password_strength,
    )

    assert validate_password_strength("Str0ng!pass") is None
    assert validate_password_strength("longpassphrase-only") is None  # ≥12 放行
    with pytest.raises(PasswordPolicyError):
        validate_password_strength("short1!")  # 太短
    with pytest.raises(PasswordPolicyError):
        validate_password_strength("password")  # 常见表
    with pytest.raises(PasswordPolicyError):
        validate_password_strength("abcdefgh")  # 单一字符类且 <12


def test_progressive_delay_ladder():
    from app.core.password_policy import (
        pending_failure_count,
        record_login_failure,
        reset_login_failures,
    )

    ident = "delay-probe-v9@example.com"
    reset_login_failures(ident)
    delays = [record_login_failure(ident) for _ in range(6)]
    assert delays[:3] == [0.0, 0.0, 0.0]  # 前 3 次无延迟
    assert delays[3] > 0 and delays[5] > delays[3]  # 指数递增
    assert pending_failure_count(ident) == 6
    reset_login_failures(ident)
    assert pending_failure_count(ident) == 0


# ══ org quota + audit（异步 DB 面）════════════════════════════════════════

async def test_quota_defaults_and_concurrency_exceed(tmp_path, monkeypatch):
    from app.core.errors import ErrorCategory, PlatformError
    from app.models.db_model import GeoComputeClusterRun, Organization
    from app.services import org_quota

    monkeypatch.setattr(org_quota, "DEFAULT_MAX_CONCURRENT_TASKS", 1)
    engine, maker = await _fresh_db(tmp_path)
    try:
        async with maker() as db:
            db.add(Organization(id=7, name="Q", slug="q-v9"))
            await db.flush()
            db.add(GeoComputeClusterRun(
                run_id="q-run-1", owner_scope="u:q", status="running",
                plan_fingerprint="f" * 32, plan_snapshot={},
                org_id="7",
            ))
            await db.commit()
            # 未达限：放行
            await org_quota.check_concurrency(db, "8")  # 其他 org
            # 达限：QUOTA 429 + PlatformError 语义
            with pytest.raises(org_quota.QuotaExceededError) as ei:
                await org_quota.check_concurrency(db, "7")
            assert ei.value.category == ErrorCategory.QUOTA
            assert ei.value.http_status == 429
            assert isinstance(ei.value, PlatformError)
    finally:
        await engine.dispose()


async def test_audit_record_query_and_unknown_action(tmp_path):
    from app.services import audit

    engine, maker = await _fresh_db(tmp_path)
    try:
        async with maker() as db:
            await audit.record_audit(
                db, action="admin.quota.set", actor_id="root-v9",
                org_id="7", target_type="org_quota", target_id="7",
                detail={"k": "v"}, trace_id="a" * 32)
            await audit.record_audit(
                db, action="weird.unlisted", actor_id="root-v9", org_id="7")
            rows = await audit.query_audit(db, org_id="7")
            actions = {r["action"] for r in rows}
            assert "admin.quota.set" in actions
            assert "uncategorized.weird.unlisted" in actions
            assert all(r["actor_id"] == "root-v9" for r in rows)
    finally:
        await engine.dispose()


def test_trace_id_from_header():
    from app.services.audit import trace_id_from_header

    tid = "1" * 32
    assert trace_id_from_header(f"00-{tid}-{'2' * 16}-01") == tid
    assert trace_id_from_header("garbage") is None
    assert trace_id_from_header(None) is None


# ══ metrics / health 门禁 ════════════════════════════════════════════════

def test_metrics_gate_and_health_layering(monkeypatch):
    # main.py 在 import 期捕获门禁配置 —— 测试直接钉住模块级配置
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "_METRICS_AUTH_DISABLED", False)
    monkeypatch.setattr(main_mod, "_METRICS_TOKEN", "")
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    # 未配 token → fail-closed 401（分类学错误体）
    resp = client.get("/metrics")
    assert resp.status_code == 401
    assert "code" in resp.text

    # /healthz 公开极简；/health 需要管理员
    assert client.get("/healthz").status_code == 200
    assert client.get("/health").status_code in (401, 403)


def test_metrics_gate_with_token(monkeypatch):
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "_METRICS_AUTH_DISABLED", False)
    monkeypatch.setattr(main_mod, "_METRICS_TOKEN", "v9-metrics-secret")
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/metrics").status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer v9-metrics-secret"})
    assert ok.status_code == 200
    bad = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 401


# ══ refresh rotation + 重放检测 ══════════════════════════════════════════

async def test_refresh_rotation_and_replay_detection(tmp_path, monkeypatch):
    """轮换前移 current_jti；旧 token 重放 → 家族失效（新旧一起死）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.auth import verify_token
    from app.core.database import get_async_db
    from app.models.db_model import Base, RefreshTokenFamily, User
    from app.services.audit import ACTION_AUTH_REFRESH_REUSE
    from app.core import rate_limiter as rl_mod
    from fastapi import FastAPI
    from app.api.routes import auth as auth_routes

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/refresh.db",
        connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with maker() as db:
        db.add(User(id="rot-user-v9", username="rot", email="rot@x.test",
                    password_hash="x", role="editor", is_active=True,
                    token_version=0))
        await db.commit()

    async def override_db():
        async with maker() as s:
            yield s

    class _NoOpLimiter:
        async def is_allowed(self, key, max_requests, window_seconds):
            return True

        async def record(self, key, window_seconds):
            return True

    async def _stub():
        return _NoOpLimiter()

    monkeypatch.setattr(rl_mod, "get_rate_limiter", _stub)
    monkeypatch.setattr(auth_routes, "get_rate_limiter", _stub)

    app = FastAPI()
    app.include_router(auth_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override_db

    # 直接以服务层函数签发首对（与 login 同路径）
    async with maker() as db:
        user = (await db.execute(
            select(User).where(User.id == "rot-user-v9")
        )).scalar_one()
        pair = await auth_routes._new_pair_with_family(db, user)
    old_refresh = pair.refresh_token
    fam = verify_token(old_refresh)["fam"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # 正常轮换：200 + 新 refresh（fam 不变）
        r1 = await c.post("/api/v1/auth/refresh",
                          json={"refresh_token": old_refresh})
        assert r1.status_code == 200, r1.text
        new_refresh = r1.json()["refresh_token"]
        assert verify_token(new_refresh)["fam"] == fam
        assert verify_token(new_refresh)["jti"] != verify_token(old_refresh)["jti"]

        # 重放旧 refresh：401 + 家族被标 reuse_detected
        r2 = await c.post("/api/v1/auth/refresh",
                          json={"refresh_token": old_refresh})
        assert r2.status_code == 401
        async with maker() as db:
            row = (await db.execute(
                select(RefreshTokenFamily).where(
                    RefreshTokenFamily.family_id == fam)
            )).scalar_one()
            assert row.reuse_detected is True

        # 家族已死：最新 refresh 也无法再刷（家族全失效语义）
        r3 = await c.post("/api/v1/auth/refresh",
                          json={"refresh_token": new_refresh})
        assert r3.status_code == 401

        # 审计留痕（fail-open 写入的查询验证）
        from app.services import audit

        async with maker() as db:
            rows = await audit.query_audit(
                db, action=ACTION_AUTH_REFRESH_REUSE)
            assert len(rows) == 1 and rows[0]["target_id"] == fam

    await engine.dispose()
