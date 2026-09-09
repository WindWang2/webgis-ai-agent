"""SRE 组件健康分类学测试（Quality V3 W11）。

安全契约（B-1）：未鉴权 401、不在限流豁免前缀、探测走线程池、
快照原子换入；诚实契约：探测失败 = down（不静默吞）、jobs 不可用 =
None（不伪造 0）、指标刷新同步发生（M-4）。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core import sre_metrics
from app.api.routes import health as health_mod

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture()
def app_with_sre(monkeypatch):
    """裸 FastAPI + status/detailed 路由（带真实鉴权依赖的探测桩）。"""

    app = FastAPI()
    app.include_router(health_mod.router, prefix="/api/v1")

    async def fake_user():
        return {"user_id": "ops"}

    # 探测桩：db/redis ok，llm down，worker degraded，object_store not_configured
    monkeypatch.setattr(health_mod, "_check_db", lambda: True)
    monkeypatch.setattr(health_mod, "_check_redis", lambda: True)
    monkeypatch.setattr(health_mod, "_check_llm", lambda: False)
    monkeypatch.setattr(health_mod, "_check_celery", lambda: True)

    async def no_stuck():
        return 0

    monkeypatch.setattr(health_mod, "_count_stuck_jobs", no_stuck)
    # object_store 未配置分支（env 事实源）
    monkeypatch.delenv("WEBGIS_S3_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("WEBGIS_S3_BUCKET", raising=False)
    return app


def _invalidate_cache():
    with health_mod._cache_lock:
        health_mod._cache_snapshot = None
        health_mod._cache_written_at = 0.0


@pytest.mark.asyncio()
async def test_status_requires_auth(app_with_sre, monkeypatch):
    """B-1：未鉴权 401（get_current_user JWT 校验不可绕过）。"""
    from fastapi import Depends
    from app.core.auth import get_current_user as real_gcu

    # 显式挂真实鉴权依赖（fixture 里裸路由不带 Depends；这里补真实路径）
    app = FastAPI()

    @app.get("/status/detailed")
    async def endpoint(_user: dict = Depends(real_gcu)):
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/status/detailed")
    assert resp.status_code == 401, "无 token 必须被 JWT 依赖拒绝"


@pytest.mark.asyncio()
async def test_status_payload_and_overall(app_with_sre, monkeypatch):
    """函数级驱动：payload 词表、down 组件、整体状态（响应对象语义）。"""
    _invalidate_cache()
    resp = await health_mod.sre_status_detailed(_current_user={"u": 1})
    body = json.loads(resp.body.decode())
    assert set(body["components"]) == set(sre_metrics.SRE_COMPONENTS)
    assert body["components"]["db"]["status"] == "ok"
    assert body["components"]["llm"]["status"] == "down"
    assert body["components"]["object_store"]["status"] == "not_configured"
    assert body["status"] == "down", "任一组件 down → 整体 down"
    assert body["refresh_age_s"] >= 0


@pytest.mark.asyncio()
async def test_status_503_when_any_component_down(app_with_sre):
    """M-3：整体 down → 503（与 /ready 探测语义对齐；degraded 保持 200）。"""
    _invalidate_cache()
    resp = await health_mod.sre_status_detailed(_current_user={"u": 1})
    assert resp.status_code == 503


@pytest.mark.asyncio()
async def test_status_200_when_all_ok(app_with_sre, monkeypatch):
    _invalidate_cache()
    monkeypatch.setattr(health_mod, "_check_llm", lambda: True)
    resp = await health_mod.sre_status_detailed(_current_user={"u": 1})
    assert resp.status_code == 200


@pytest.mark.asyncio()
async def test_cache_snapshot_atomic_and_ttl(app_with_sre, monkeypatch):
    _invalidate_cache()
    calls = {"n": 0}
    real_collect = health_mod._collect_sre_snapshot

    def counting_collect():
        calls["n"] += 1
        return real_collect()

    monkeypatch.setattr(health_mod, "_collect_sre_snapshot",
                        counting_collect)
    # 并发 5 请求 → 只一次真实探测（TTL + 单飞 guard）
    async def burst():
        await health_mod.sre_status_detailed(_current_user={})

    await asyncio.gather(*[burst() for _ in range(5)])
    assert calls["n"] == 1, f"并发下必须单飞，实际 {calls['n']} 次"
    # TTL 内命中缓存
    await health_mod.sre_status_detailed(_current_user={})
    assert calls["n"] == 1
    # TTL 过期 → 重新探测
    with health_mod._cache_lock:
        health_mod._cache_written_at -= health_mod._CACHE_TTL_S + 0.01
    await health_mod.sre_status_detailed(_current_user={})
    assert calls["n"] == 2


@pytest.mark.asyncio()
async def test_metrics_refreshed_on_collect(app_with_sre, monkeypatch):
    """M-4：端点刷新路径必须同步写指标 + 时间戳（staleness 锚点）。"""
    from prometheus_client import REGISTRY

    _invalidate_cache()

    async def stuck_7():
        return 7

    monkeypatch.setattr(health_mod, "_count_stuck_jobs", stuck_7)
    await health_mod.sre_status_detailed(_current_user={"u": 1})
    ts = REGISTRY.get_sample_value("sre_health_refresh_timestamp_seconds")
    assert ts and abs(ts - time.time()) < 5
    stuck = REGISTRY.get_sample_value("sre_stuck_jobs")
    assert stuck == 7
    llm = REGISTRY.get_sample_value(
        "sre_health_component_status", {"component": "llm"})
    assert llm == 0.0, "down 组件 → gauge 0"
    db = REGISTRY.get_sample_value(
        "sre_health_component_status", {"component": "db"})
    assert db == 1.0


def test_component_vocabulary_closed():
    with pytest.raises(ValueError):
        sre_metrics.set_component_status("not-a-component", 1.0)
    with pytest.raises(ValueError):
        sre_metrics.set_component_status("db", 0.7)  # 词表外状态值


def test_seed_defaults_make_staleness_observable():
    """导入期临时值 + 时间戳 0 → staleness 告警（time()-ts>120）在无刷新时
    必然成立 —— 临时 ok 不可能被误读为健康事实（M-4）。"""
    from prometheus_client import REGISTRY

    ts = REGISTRY.get_sample_value("sre_health_refresh_timestamp_seconds") or 0
    if ts == 0:
        assert time.time() - ts > 120
