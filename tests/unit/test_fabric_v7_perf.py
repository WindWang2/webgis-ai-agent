"""V7 结构性性能/混沌测试（ADR-0119 W16，Epic §16）。

性能成功不能只用 wall-clock —— 断言维度全部是**结构性预算**：
- 远端请求数：页数/请求次数硬上界（扫描页 + 缓存命中 = 0 远端请求）；
- 物化行数：budget.max_rows fail-fast；
- 缓存：命中省去全部远端请求；fingerprint 失效后回源；
- 混沌：源行数骤变（估计偏差 ≥4×）不改变结果语义（bushy replan 安全网）；
- 结果缓存端到端：catalog 指纹解析 → 命中披露段。
"""

import pytest

from tests.unit.test_fabric_security_differential import (
    _FakeSrc,
    _four_source_data,
    _pt,
    _run_4source,
    _sorted_rows,
)


@pytest.fixture(autouse=True)
def _clean_engine_breaker():
    """隔离进程级 V6 熔断：前序用例崩溃记账不得污染本文件 engine=v6 断言。"""
    from app.services.data_fabric.fabric.engine_breaker import reset_engine_breaker

    reset_engine_breaker()
    yield
    reset_engine_breaker()



class _CountingSrc(_FakeSrc):
    """请求计数假 provider（结构性 counter 的事实源）。"""

    def __init__(self, data):
        super().__init__(data)
        self.requests = 0

    def query(self, dataset_id, spec):
        self.requests += 1
        return super().query(dataset_id, spec)


def test_remote_request_budget_bounded():
    """请求数预算：每源扫描请求数 ≤ 页数上界（limit/页大小）+1。"""
    data = _four_source_data()
    adapters = {
        sid: _CountingSrc(data) for sid in ("cities", "regions", "gov", "audit")
    }
    result = _run_4source(adapters, engine="v6")
    assert result["status"] == "success"
    total_requests = sum(a.requests for a in adapters.values())
    # 4 源，每源 limit=10_000 默认、假源单页返回 → 严格 ≤ 4 次远端请求
    assert total_requests <= 4
    assert result["row_count"] == 3


def test_row_budget_fail_fast():
    """budget.max_rows fail-fast：物化行数硬界。"""
    from app.services.data_fabric.errors import QueryBudgetExceededError
    from app.services.data_fabric.query.models import ExecutionBudget
    from app.services.data_fabric.query.federation import (
        ChainJoin,
        ChainSource,
        FederatedChainRequest,
        FederatedExecutor,
    )

    data = _four_source_data()
    data["cities"] = [
        _pt(104.0 + i * 0.01, 30.0, city=f"C{i}", region="R1", pop=1)
        for i in range(2000)
    ]
    adapters = {sid: _FakeSrc(data) for sid in ("cities", "regions", "gov", "audit")}
    req = FederatedChainRequest(
        sources=[ChainSource(source_id=s, dataset_id=d) for s, d in
                 (("cities", "cities"), ("regions", "regions"), ("gov", "gov"),
                  ("audit", "audit"))],
        joins=[
            ChainJoin(kind="attribute_join", join_field_left="region",
                      join_field_right="region", left_source_id="cities",
                      right_source_id="regions"),
            ChainJoin(kind="attribute_join", join_field_left="gov",
                      join_field_right="gov", left_source_id="regions",
                      right_source_id="gov"),
            ChainJoin(kind="attribute_join", join_field_left="audit_id",
                      join_field_right="audit_id", left_source_id="gov",
                      right_source_id="audit"),
        ],
        limit=10_000, engine="v6",
        budget=ExecutionBudget(max_rows=500, max_bytes=10**9, max_vertices=10**9,
                               deadline_s=60.0),
    )
    executor = FederatedExecutor(lambda sid: adapters.get(sid))
    with pytest.raises(QueryBudgetExceededError):
        executor.execute_chain(req)


def test_cache_hit_eliminates_remote_requests():
    """缓存命中 = 0 远端请求（结构性省费，非 wall-clock）。"""
    from app.services.data_fabric.spatial_catalog import spatial_catalog_service
    from app.schemas.data_fabric_schema import DatasetDescriptor

    data = _four_source_data()
    adapters = {
        sid: _CountingSrc(data) for sid in ("cities", "regions", "gov", "audit")
    }
    # catalog 登记（owner 过滤路径）→ 缓存键完整
    owner = "perf-user"
    for sid, did in (("cities", "cities"), ("regions", "regions"),
                     ("gov", "gov"), ("audit", "audit")):
        spatial_catalog_service.register_dataset(
            DatasetDescriptor(id=did, title=did, source_type="generic",
                              geometry_type="Point", srs="EPSG:4326",
                              bbox=[0, 0, 1, 1], feature_count=1, fields=[]),
            profile_id=f"p_{sid}", owner=owner,
        )
    first = _run_4source(adapters, engine="v6", session_owner=owner)
    assert first["status"] == "success"
    requests_after_first = sum(a.requests for a in adapters.values())
    second = _run_4source(adapters, engine="v6", session_owner=owner)
    requests_after_second = sum(a.requests for a in adapters.values())
    assert requests_after_second == requests_after_first  # 0 新远端请求
    assert second.get("result_cache", {}).get("hit") is True
    assert _sorted_rows(first["rows"]) == _sorted_rows(second["rows"])


def test_chaos_source_growth_preserves_semantics():
    """混沌：源行数骤变 ×100 —— fabric on（replan 可触发）与 given（禁用）
    结果语义一致（优化的书挡：绝不因重排改语义）。"""
    data = _four_source_data()
    data["cities"] = [
        _pt(104.0 + i * 0.001, 30.0, city=f"C{i}", region="R1", pop=1)
        for i in range(300)
    ]
    adapters_a = {sid: _FakeSrc(data) for sid in ("cities", "regions", "gov", "audit")}
    adapters_b = {sid: _FakeSrc(data) for sid in ("cities", "regions", "gov", "audit")}
    r_adaptive = _run_4source(adapters_a, engine="v6", order_strategy="cost")
    r_given = _run_4source(adapters_b, engine="v6", order_strategy="given")
    assert _sorted_rows(r_adaptive["rows"]) == _sorted_rows(r_given["rows"])
