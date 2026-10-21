"""PERF-03（issue #1683）：data_fabric manager 调用不得汇入单一共享 event loop。

修复前：全部 manager 协程经 ``run_coroutine_threadsafe`` 汇入进程级常驻
``df-manager-loop``，协程内的同步 SQLAlchemy ORM（``_authorize_catalog_item``
的 ``db.query``）是该 loop 上的阻塞段 ——
  (a) 头阻塞：40 并发请求的 wall time ≈ 40 × 每请求延迟（随并发数线性增长），
      一条慢查询 stall 全部并发 catalog 请求；
  (b) 连接池放大：每个在飞请求的 SessionLocal 从提交到完成的整个 loop 排队
      期间都持有连接，~30 并发即耗尽 pool_size=10 + max_overflow=20。

修复后：每个 manager 调用跑在**自己线程的自己 loop**上 + 请求级 session ——
  (a) wall time 由线程池并发度决定，而非请求数线性增长；
  (b) 连接 checkout 峰值 ≤ 线程池并发度（排队中的请求不占线程、不占连接），
      请求结束后连接全部归还（checkedout 归零，无泄漏/驻留）。

延迟注入点在 DB 层（``before_cursor_execute``）而非桩函数里 sleep：这样
同步 ORM 真的持有一根连接在阻塞段上，两个症状（头阻塞 / 连接驻留）都被
真实复现，桩只负责去掉远端 fetch（不改变 loop/连接行为）。
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.api.routes import data_fabric as df_routes
from app.core.database import Base
from app.models.data_fabric import CatalogItemModel, DataSourceModel
from app.models.db_model import User
from app.schemas.data_fabric_schema import QueryResult
from app.services.data_fabric.manager import DataFabricManager

_ITEM_ID = "cat_perf_loop_layer"
_SOURCE_ID = "ds_perf_loop_src"
_OWNER_ID = "perf-loop-owner"

_N_REQUESTS = 40
# 每请求的 _authorize_catalog_item 注入 100ms 延迟：该函数跑 2 条 SQL
# （CatalogItemModel + DataSourceModel 查询），故每条语句 50ms。
_PER_STATEMENT_DELAY_S = 0.05
_WALL_BUDGET_S = 4.0


def _default_executor_lanes() -> int:
    """``asyncio.to_thread`` 默认执行器的并发度（ThreadPoolExecutor 默认公式）。

    修复后的并发上界：只有拿到执行器线程的请求才真正在跑，排队中的请求不占
    线程也不占连接 —— 这正是断言 (b) 的参照系。
    """
    cpu_count = getattr(os, "process_cpu_count", os.cpu_count)()
    return min(32, (cpu_count or 1) + 4)


@pytest.fixture()
async def manager_loop_env(tmp_path, monkeypatch):
    """真实 ORM 鉴权 + DB 层 100ms/请求延迟 + 大池专用引擎。

    ``df_routes.SessionLocal`` 指向本测试引擎（pool_size 远大于并发度，排除
    「池太小」与「连接驻留」两种混淆因）；checkout/checkin 事件精确记录连接
    占用峰值（与 ``Engine.pool.checkedout()`` 同一量）。
    """
    eng = create_engine(
        f"sqlite:///{tmp_path / 'perf-loop.db'}",
        connect_args={"check_same_thread": False},
        pool_size=64,
        max_overflow=0,
        pool_timeout=30,
    )
    Base.metadata.create_all(bind=eng)
    with sessionmaker(bind=eng)() as db:
        if db.get(User, _OWNER_ID) is None:
            db.add(User(
                id=_OWNER_ID,
                username="perf-loop-owner",
                email="perf-loop-owner@example.com",
                password_hash="scrypt$16384$8$1$00$00",
                role="editor",
                is_active=True,
                token_version=0,
                created_at=datetime.now(timezone.utc),
            ))
            db.commit()
        db.query(CatalogItemModel).filter(CatalogItemModel.id == _ITEM_ID).delete()
        db.query(DataSourceModel).filter(DataSourceModel.id == _SOURCE_ID).delete()
        db.add(DataSourceModel(
            id=_SOURCE_ID, org_id=None, owner_id=_OWNER_ID,
            name="perf loop source", source_type="postgis",
            endpoint_url="postgresql://example/db",
            connection_profile={}, capabilities_json=[],
        ))
        db.add(CatalogItemModel(
            id=_ITEM_ID, source_id=_SOURCE_ID, name="public.perf_loop_layer",
            title="perf loop layer", geometry_type="Point", feature_type="vector",
            crs="EPSG:4326", bbox_json=None, tags_json=[],
            descriptor_json={}, meta_profile_json={},
            fingerprint="fp-perf-loop-1", availability="available",
        ))
        db.commit()

    # 延迟注入： seeding 之后才挂，避免拖慢建表/播种。
    event.listen(
        eng, "before_cursor_execute",
        lambda *a, **k: time.sleep(_PER_STATEMENT_DELAY_S),
    )

    checkout = {"cur": 0, "peak": 0}
    lock = threading.Lock()

    def _on_checkout(*_a, **_k):
        with lock:
            checkout["cur"] += 1
            checkout["peak"] = max(checkout["peak"], checkout["cur"])

    def _on_checkin(*_a, **_k):
        with lock:
            checkout["cur"] -= 1

    event.listen(eng, "checkout", _on_checkout)
    event.listen(eng, "checkin", _on_checkin)

    # 远端 fetch 与本次议题无关：桩掉，保留 loop/session 行为原样。
    # #1753：patch 类而非单例实例 —— 对实例的 monkeypatch 在 undo 时会把
    # 继承来的原方法写回实例 __dict__（常驻阴影），之后任何对同属性的类级
    # patch 都被静默击败（test_event_loop_offload_425 的 preview 用例因此
    # 走到真实 OGC 网络路径，全量串行套件永久挂起）。classmethod 包装保持
    # 调用签名（实例访问时自动绑定 cls）。
    async def _fake_query(cls, session, item_id, spec):
        return QueryResult(dataset_id=item_id, features=[], returned_count=0, total_count=0)

    monkeypatch.setattr(
        DataFabricManager, "query_catalog_item_async", classmethod(_fake_query)
    )
    monkeypatch.setattr(df_routes, "SessionLocal", sessionmaker(bind=eng))

    application = FastAPI()
    application.include_router(df_routes.router, prefix="/api/v1")
    application.dependency_overrides[df_routes.get_current_user] = (
        lambda: {"user_id": _OWNER_ID}
    )
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, eng, checkout


async def _hammer(client, method: str, url: str, **kwargs):
    """N 个并发请求同时打出，返回 (responses, wall_seconds)。"""
    started = time.perf_counter()
    responses = await asyncio.gather(*(client.request(method, url, **kwargs) for _ in range(_N_REQUESTS)))
    return responses, time.perf_counter() - started


@pytest.mark.asyncio
async def test_preview_requests_are_not_serialized_by_one_shared_loop(manager_loop_env):
    """(a) 头阻塞消失 + (b) 连接驻留有界：40 并发 /preview。"""
    client, eng, checkout = manager_loop_env
    lanes = _default_executor_lanes()

    responses, wall = await _hammer(
        client, "GET", f"/api/v1/data-fabric/catalog/{_ITEM_ID}/preview", params={"limit": 5}
    )

    assert all(r.status_code == 200 for r in responses), (
        f"并发 preview 必须全部成功：{[r.status_code for r in responses]}"
    )
    # (a) 修复前 = 单 loop 串行 ≈ 40 × 100ms = 4s 起且随 N 线性增长。
    assert wall < _WALL_BUDGET_S, (
        f"{_N_REQUESTS} 并发 preview wall={wall:.2f}s —— 疑似仍被单一 event loop 串行化"
    )
    # (b) checkout 峰值 ≤ 线程池并发度，而非请求数。
    assert checkout["peak"] <= lanes, (
        f"连接 checkout 峰值 {checkout['peak']} > 线程池并发度 {lanes}"
    )
    if lanes < _N_REQUESTS:
        assert checkout["peak"] < _N_REQUESTS, (
            f"连接 checkout 峰值 {checkout['peak']} 达到请求数 {_N_REQUESTS}"
            " —— 排队中的请求也在持有连接（连接池放大）"
        )
    # 请求级 session：收尾后连接全部归还，无跨请求驻留/泄漏。
    assert eng.pool.checkedout() == 0, "请求结束后连接必须全部归还池"
    assert checkout["cur"] == 0

    print(
        f"[perf] df manager loop: n={_N_REQUESTS} wall={wall:.2f}s "
        f"lanes={lanes} peak_checkedout={checkout['peak']}"
    )


@pytest.mark.asyncio
async def test_cancellable_manager_path_is_not_serialized_by_one_shared_loop(manager_loop_env):
    """可取消路径（/features 分页 → _run_async_manager_cancellable）同样
    不得汇入单一 loop：每请求一条自己的 worker loop。"""
    client, eng, checkout = manager_loop_env

    responses, wall = await _hammer(
        client, "GET", f"/api/v1/data-fabric/catalog/{_ITEM_ID}/features", params={"limit": 5}
    )

    assert all(r.status_code == 200 for r in responses), (
        f"并发 features 分页必须全部成功：{[r.status_code for r in responses]}"
    )
    assert wall < _WALL_BUDGET_S, (
        f"{_N_REQUESTS} 并发 features wall={wall:.2f}s —— 可取消路径疑似仍被单一 loop 串行化"
    )
    assert checkout["peak"] <= _default_executor_lanes()
    assert eng.pool.checkedout() == 0, "请求结束后连接必须全部归还池"

    print(
        f"[perf] df manager loop (cancellable): n={_N_REQUESTS} wall={wall:.2f}s "
        f"peak_checkedout={checkout['peak']}"
    )
