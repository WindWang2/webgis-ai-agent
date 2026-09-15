"""Specialist Agents Pack — Data Scout & GeoCompute（agent-swarm/04，ADR-0188）。

覆盖规格（docs/dev/data-compute-agents-spec.md §8 测试矩阵 T1–T16）：

- **Data Scout（数据猎手）**：主源成功 / 断路器熔断 → 自动遍历 ADS 声明式
  回退链（D3 降级留痕 + degraded 诚实披露）/ 全链失败诚实失败 / 异构
  字段实体对齐；
- **RBAC 工具白名单**：白名单存在性 fail-closed 校验、data_scout 越权调
  计算算子拦截、geocompute 越权调制图/接数工具拦截、dispatch 包装面
  结构化 TOOL_NOT_ALLOWLISTED；
- **GeoCompute（空间计算专家）**：体积三级估算、UTM 投影防御注入、
  任务图 validate_plan 合法、Celery durable 提交（stub submitter）、
  ref_id 提货券合法性、8KB Zero-Big-Data 硬闸（截断 + typed 失败）；
- **基类纪律**：心跳观测、墙钟熔断、注册表完备与幂等、委派路径携带
  角色与专属提示词。

确定性：全部 stub 注入（source_registry / adapter_factory / submitter /
clock），不打 LLM、不打 DB、不打 Redis、不打真实数据源。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

_STUB_PATH = Path("stub/sources")

from app.services.data_fabric import fallback as fb_mod
from app.services.data_fabric.circuit_breaker import CircuitState
from app.services.data_fabric.contracts import D1DatasetDescriptor
from app.services.data_fabric.source_registry import (
    SourceDefinition,
    SourceRegistryError,
)
from app.services.geocompute.graph import validate_plan
from app.services.geocompute.plan import ExecutionPolicyKind, NodeCategory
from app.services.subagent import SubagentResult
from app.services.subagent_roles import (
    SUBAGENT_ROLES,
    get_subagent_role,
    validate_subagent_role_registry,
)

# 被测模块（TDD：先红后绿 —— 实现落地后本文件全绿）
from app.services.agent_swarm.base import (
    SpecialistTimeoutError,
    SpecialistToolDeniedError,
)
from app.services.agent_swarm.contracts import (
    SERIALIZATION_BUDGET_BYTES,
    SpatialProfileTooLargeError,
    SpatialProfileRef,
)
from app.services.agent_swarm.data_scout import DataScoutAgent
from app.services.agent_swarm.geocompute import GeoComputeAgent
from app.services.agent_swarm.registry import (
    SPECIALIST_REGISTRY,
    ensure_subagent_roles_registered,
    get_specialist,
)


# ─────────────────────────── 测试基建 ───────────────────────────


@pytest.fixture(autouse=True)
def _clean_breakers():
    """熔断器隔离（与 test_data_fabric_fallback 同门）：每用例全新注册表。"""
    fb_mod.breaker_registry = fb_mod.CircuitBreakerRegistry()
    yield
    fb_mod.breaker_registry = fb_mod.CircuitBreakerRegistry()


class FakeClock:
    """可控单调时钟（心跳/墙钟熔断确定性测试）。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class StubRegistry:
    """source_registry_service 替身：.get() 鸭子类型即可（resolve_chain 注入）。"""

    def __init__(self, *definitions: SourceDefinition) -> None:
        self._defs = {d.source_id: d for d in definitions}

    def get(self, source_id: str) -> SourceDefinition:
        if source_id not in self._defs:
            raise SourceRegistryError(
                _STUB_PATH, f"unknown source: {source_id}",
            )
        return self._defs[source_id]

    def list_sources(self):
        return list(self._defs.values())


def _source(
    source_id: str,
    *,
    fallbacks: Optional[List[Any]] = None,
    protocol: str = "stats_api",
    options: Optional[Dict[str, Any]] = None,
    verified: bool = True,
) -> SourceDefinition:
    return SourceDefinition(
        source_id=source_id,
        name=f"源 {source_id}",
        protocol=protocol,
        endpoint="https://stub.example",
        options=dict(options or {}),
        verified=verified,
        fallbacks=list(fallbacks or []),
    )


class StubAdapter:
    """适配器替身：preview 返回预设载荷 / 抛预设异常（模拟断路器前的源故障）。"""

    def __init__(
        self,
        payload: Optional[Dict[str, Any]] = None,
        exc: Optional[BaseException] = None,
    ) -> None:
        self.payload = payload or {"features": [], "metadata": {}}
        self.exc = exc
        self.calls: List[str] = []

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        self.calls.append(dataset_id)
        if self.exc is not None:
            raise self.exc
        return dict(self.payload)


def _scout(primary: str = "stub_primary", **kw) -> DataScoutAgent:
    return DataScoutAgent(**kw)


# ──────────────────── Data Scout：回退链与诚实披露 ────────────────────


def test_scout_primary_success_no_degradation():
    """T1：主源成功 → descriptor 填充、零降级决策、fact=success。"""
    registry = StubRegistry(_source("stub_primary", options={"crs": "EPSG:4490"}))
    adapter = StubAdapter(payload={
        "features": [{"名称": "西单", "经度": 116.37, "纬度": 39.9}],
        "metadata": {"bytes": 256},
    })
    agent = _scout(
        source_registry=registry,
        adapter_factory=lambda definition: adapter,
        clock=FakeClock(),
    )
    report = agent.scout("stub_primary/datasets/population")

    assert report.ok is True
    assert report.source_used == "stub_primary"
    assert report.decisions == []
    assert report.fact is not None and report.fact.outcome == "success"
    assert report.fact.degraded is False
    desc = report.descriptor
    assert isinstance(desc, D1DatasetDescriptor)
    assert desc.source_id == "stub_primary"
    assert desc.quality_signals.declared_crs == "EPSG:4490"
    assert desc.quality_signals.verified is True
    assert desc.feature_count == 1


def test_scout_circuit_open_walks_declared_fallback_chain():
    """T2：主源熔断 OPEN → 自动遍历声明式回退链，D3 留痕 + degraded 披露。"""
    registry = StubRegistry(
        _source("stub_primary", fallbacks=["stub_backup"]),
        _source("stub_backup", protocol="local_file"),
    )
    # 熔断主源：连续失败达到阈值（5）→ OPEN
    for _ in range(5):
        fb_mod.breaker_registry.record_failure("stub_primary", RuntimeError("503"))
    assert fb_mod.breaker_registry.state("stub_primary") is CircuitState.OPEN

    adapters = {
        "stub_primary": StubAdapter(exc=RuntimeError("HTTP 503 bad gateway")),
        "stub_backup": StubAdapter(payload={
            "features": [{"id": 1}], "metadata": {"bytes": 64},
        }),
    }
    agent = _scout(
        source_registry=registry,
        adapter_factory=lambda definition: adapters[definition.source_id],
        clock=FakeClock(),
    )
    report = agent.scout("stub_primary/census")

    assert report.ok is True
    assert report.source_used == "stub_backup"
    assert len(report.decisions) >= 1
    triggers = [d.trigger for d in report.decisions]
    assert "circuit_open" in triggers
    assert all(not d.comparable for d in report.decisions)  # 保守默认：换源不可比
    assert report.fact is not None and report.fact.outcome == "degraded"
    assert report.descriptor is not None
    assert report.descriptor.source_id == "stub_backup"


def test_scout_all_sources_failed_is_honest_failure():
    """T3：全链失败 → ok=False 诚实失败，绝不虚构 descriptor。"""
    registry = StubRegistry(
        _source("stub_primary", fallbacks=["stub_backup"]),
        _source("stub_backup"),
    )
    adapters = {
        "stub_primary": StubAdapter(exc=RuntimeError("HTTP 503 bad gateway")),
        "stub_backup": StubAdapter(exc=RuntimeError("429 too many requests")),
    }
    agent = _scout(
        source_registry=registry,
        adapter_factory=lambda definition: adapters[definition.source_id],
        clock=FakeClock(),
    )
    report = agent.scout("stub_primary/census")

    assert report.ok is False
    assert report.descriptor is None
    assert report.source_used is None
    assert report.error
    assert report.fact is not None and report.fact.outcome == "failed"
    # 两次失败都留了 D3 溯源
    assert len(report.decisions) == 1
    assert report.decisions[0].trigger == "5xx"


def test_scout_unregistered_source_is_honest_not_raising():
    registry = StubRegistry()
    agent = _scout(source_registry=registry, clock=FakeClock())
    report = agent.scout("ghost_source/ds")
    assert report.ok is False
    assert report.descriptor is None
    assert "ghost_source" in (report.error or "")


def test_scout_aligns_heterogeneous_fields():
    """实体对齐：异构字段名映射到标准字段词典（有证据才映射）。"""
    registry = StubRegistry(_source("stub_primary"))
    adapter = StubAdapter(payload={
        "features": [{"名称": "西单", "经度": 116.37, "纬度": 39.9, "未知列": 1}],
        "metadata": {"bytes": 128},
    })
    agent = _scout(
        source_registry=registry,
        adapter_factory=lambda definition: adapter,
        clock=FakeClock(),
    )
    report = agent.scout("stub_primary/poi")
    assert report.aligned_fields == {"名称": "name", "经度": "lon", "纬度": "lat"}


# ─────────────────────────── RBAC 工具白名单 ───────────────────────────


def test_allowlist_names_must_exist_in_registry_fail_closed():
    """T4：白名单引用不存在的工具名 → 构造期 ValueError（防白名单腐烂）。"""
    from app.tools.registry import ToolRegistry

    tr = ToolRegistry()
    tr.register(
        name="buffer_analysis", description="x", func=lambda t: {"ok": True}, tier=2,
    )
    with pytest.raises(ValueError, match="geocompute"):
        GeoComputeAgent(registry=tr, clock=FakeClock())


def test_data_scout_denied_compute_operators():
    """T5：Data Scout 调计算算子 → typed 拦截。"""
    agent = _scout(clock=FakeClock())
    for tool in ("execute_execution_plan", "buffer_analysis", "h3_binning"):
        with pytest.raises(SpecialistToolDeniedError, match=tool):
            agent.authorize_tool(tool)
    # 但 ADS/Fetch 面放行
    agent.authorize_tool("query_dataset")
    agent.authorize_tool("search_datasets")


def test_geocompute_denied_ingest_and_cartography():
    """T6：GeoCompute 调数据源接入 / 制图工具 → typed 拦截。"""
    agent = GeoComputeAgent(clock=FakeClock())
    for tool in (
        "connect_data_source",
        "apply_layer_style",
        "create_thematic_map",
        "webgis_layer_remove",
    ):
        with pytest.raises(SpecialistToolDeniedError, match=tool):
            agent.authorize_tool(tool)
    # 计算面放行
    agent.authorize_tool("execute_execution_plan")
    agent.authorize_tool("buffer_analysis")


async def test_guarded_registry_blocks_dispatch_boundary():
    """T7：dispatch 包装面 → 结构化 TOOL_NOT_ALLOWLISTED，不执行。"""
    from app.tools.registry import ToolRegistry

    executed: List[str] = []
    tr = ToolRegistry()
    for name in GeoComputeAgent.TOOL_ALLOWLIST:
        tr.register(
            name=name,
            description="stub",
            func=lambda tool_call=None: executed.append("ran") or {"ok": True},
            tier=2,
        )
    agent = GeoComputeAgent(registry=tr, clock=FakeClock())
    guarded = agent.guarded_registry()
    result = guarded.dispatch("connect_data_source", {})
    assert result["success"] is False
    assert result["code"] == "TOOL_NOT_ALLOWLISTED"
    assert executed == []  # 绝不执行
    # 白名单内的工具正常穿透执行（返回值透传）
    allowed = await guarded.dispatch("buffer_analysis", {})
    assert allowed == {"ok": True}
    assert "ran" in executed


# ────────────────────── GeoCompute：估算 / 防御 / 编排 ──────────────────────


def test_estimate_volume_three_level_degradation():
    """T8：cost_hint → rows_hint → bbox 密度 → 全未知（诚实 None）。"""
    agent = GeoComputeAgent(clock=FakeClock())

    d1 = D1DatasetDescriptor(
        id="d", cost_hint={"rows": 500_000},
    )
    est = agent.estimate_volume(descriptor=d1, bbox=[100.0, 20.0, 110.0, 30.0])
    assert est.rows == 500_000 and est.method == "cost_hint"

    est2 = agent.estimate_volume(bbox=[100.0, 20.0, 110.0, 30.0], rows_hint=42)
    assert est2.rows == 42 and est2.method == "rows_hint"

    est3 = agent.estimate_volume(bbox=[100.0, 20.0, 110.0, 30.0])
    assert est3.rows and est3.rows > 0 and est3.method == "bbox_density"
    assert est3.area_km2 == pytest.approx(1_110_000.0, rel=0.05)

    est4 = agent.estimate_volume()
    assert est4.rows is None and est4.method == "unknown"


def test_infer_utm_crs_zone_math():
    agent = GeoComputeAgent(clock=FakeClock())
    assert agent.infer_utm_crs([100.0, 20.0, 110.0, 30.0]) == "EPSG:32648"
    assert agent.infer_utm_crs([114.0, 38.0, 118.0, 40.5]) == "EPSG:32650"
    assert agent.infer_utm_crs([170.0, -48.0, 174.0, -46.0]) == "EPSG:32759"
    assert agent.infer_utm_crs(None) is None
    assert agent.infer_utm_crs([1.0, 2.0]) is None  # 长度非法 → 诚实 None
    assert agent.infer_utm_crs(["a", "b", "c", "d"]) is None


class RecordingSubmitter:
    """submit_durable_job 替身：捕获提交参数，返回工具面同形 dict。"""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "status": "submitted",
            "job_id": 101,
            "task_id": "celery-stub-1",
            "message": "ok",
            "idempotent_reuse": False,
        }


def _big_request() -> Dict[str, Any]:
    return {
        "operation": "buffer_analysis",
        "dataset_ref": "ref-src-001",
        "operation_params": {"distance_m": 500},
        "bbox": [100.0, 20.0, 110.0, 30.0],
        "rows_hint": 300_000,
        "session_id": "sess-specialist-1",
    }


def test_plan_computation_injects_auto_utm_defense_and_valid_plan():
    """T9+T10：大 bbox → 图头注入 reproject 防御节点；任务图 validate_plan 合法。"""
    agent = GeoComputeAgent(submitter=RecordingSubmitter(), clock=FakeClock())
    sub = agent.plan_computation(_big_request())

    plan = sub.plan_built
    validate_plan(plan)  # 合法执行图（结构/契约/CRS/预算）
    assert all(
        n.policy is ExecutionPolicyKind.DURABLE_JOB for n in plan.nodes
    )  # Celery First：全部节点 durable
    first = plan.nodes[0]
    assert first.category is NodeCategory.REPROJECT
    assert first.parameters.get("defense") == "auto_utm"
    assert first.crs is not None and first.crs.output_crs == "EPSG:32648"
    main = plan.nodes[-1]
    assert main.inputs == ["reproject_auto_utm"]
    assert main.operation == "buffer_analysis"


def test_plan_computation_small_area_no_defense_node():
    agent = GeoComputeAgent(submitter=RecordingSubmitter(), clock=FakeClock())
    req = _big_request()
    req["bbox"] = [116.30, 39.85, 116.45, 39.95]  # 城区尺度，无需防御
    req["rows_hint"] = 1_000
    sub = agent.plan_computation(req)
    assert len(sub.plan_built.nodes) == 1
    assert sub.plan_built.nodes[0].category is not NodeCategory.REPROJECT
    assert sub.ref.crs_defense is None


def test_plan_computation_submits_celery_durable_job():
    """T11：Celery First 提交 —— durable 任务 + input handoff，编排器零内联执行。"""
    submitter = RecordingSubmitter()
    agent = GeoComputeAgent(submitter=submitter, clock=FakeClock())
    sub = agent.plan_computation(_big_request())

    assert sub.job_id == 101
    assert sub.node_count == len(sub.plan_built.nodes)
    call = submitter.calls[0]
    assert call["task_type"] == "geocompute_specialist"
    assert call["queue"] == "geocompute"
    task_kwargs = call["task_kwargs"]
    # input handoff：上游 ref 经 input_refs 交接（不携带原始几何/要素数据）
    assert task_kwargs["input_refs"] == {"input": "ref-src-001"}
    dumped = json.dumps(task_kwargs, default=str)
    assert "coordinates" not in dumped and '"geometry"' not in dumped
    assert "features" not in task_kwargs
    # 提交的是尾节点（durable 任务体消费单个 ExecutionNode）
    assert task_kwargs["node"]["node_id"] == sub.plan_built.nodes[-1].node_id


def test_result_ref_ticket_is_well_formed():
    """T12：ref_id 提货券合法（确定性取货位），payload 严格 < 8KB。"""
    submitter = RecordingSubmitter()
    agent = GeoComputeAgent(submitter=submitter, clock=FakeClock())
    sub = agent.plan_computation(_big_request())

    ref = sub.ref
    assert isinstance(ref, SpatialProfileRef)
    assert ref.status == "submitted"
    assert ref.job_id == 101
    assert ref.plan_id == sub.plan_id
    final_fp = sub.plan_built.nodes[-1].semantic_fingerprint()
    assert ref.ref_id == f"gc-{sub.plan_id}-{final_fp}"
    assert len(ref.ref_id) <= 128
    assert ref.crs_defense == "auto_utm"
    assert ref.volume_estimate is not None and ref.volume_estimate.rows == 300_000
    payload = ref.to_json_bytes()
    assert len(payload) < SERIALIZATION_BUDGET_BYTES  # Zero Big Data in Context
    assert json.loads(payload)["ref_id"] == ref.ref_id


def test_ref_budget_truncates_metadata_honestly():
    """T13a：超大 metadata → 逐键截断 + truncated=True 诚实标注，绝不超 8KB。"""
    agent = GeoComputeAgent(clock=FakeClock())
    big_blob = "x" * (SERIALIZATION_BUDGET_BYTES * 2)
    ref = agent.build_result_ref(
        plan_id="p1", plan_digest="d" * 16,
        metadata={"blob": big_blob, "small": "ok"},
        volume=None,
    )
    assert ref.truncated is True
    assert "blob" not in ref.metadata
    assert ref.metadata.get("small") == "ok"
    assert len(ref.to_json_bytes()) < SERIALIZATION_BUDGET_BYTES


def test_ref_budget_exhausted_raises_typed():
    """T13b：不可截（超大 plan_id）→ SpatialProfileTooLargeError，不静默裁剪。"""
    agent = GeoComputeAgent(clock=FakeClock())
    with pytest.raises(SpatialProfileTooLargeError):
        agent.build_result_ref(
            plan_id="p" * (SERIALIZATION_BUDGET_BYTES * 2),
            plan_digest="d" * 16,
            volume=None,
        )


def test_unsupported_operation_is_honest_failure():
    agent = GeoComputeAgent(clock=FakeClock())
    req = _big_request()
    req["operation"] = "teleport_features"
    with pytest.raises(ValueError, match="teleport_features"):
        agent.plan_computation(req)


# ─────────────────────────── 基类纪律 ───────────────────────────


def test_heartbeat_and_wall_clock_fuse():
    """T14：心跳推进可观测；墙钟超限 → SpecialistTimeoutError 熔断。"""
    clock = FakeClock()
    agent = _scout(clock=clock, deadline_s=5.0)
    assert agent.heartbeat_age_s() == pytest.approx(0.0)
    clock.advance(2.0)
    agent.heartbeat("scout")
    assert agent.heartbeat_age_s() == pytest.approx(0.0)
    clock.advance(4.0)
    assert agent.heartbeat_age_s() == pytest.approx(4.0)
    agent.check_deadline()  # 4s < 5s：放行
    clock.advance(2.0)  # 距上次心跳 6s
    with pytest.raises(SpecialistTimeoutError, match="deadline"):
        agent.check_deadline()


def test_specialist_registry_complete_and_idempotent():
    """T15：两专家注册、角色入 SUBAGENT_ROLES、幂等重注册、注册表自检。"""
    ensure_subagent_roles_registered()
    ensure_subagent_roles_registered()  # 幂等
    assert {"data_scout", "geocompute"} <= set(SPECIALIST_REGISTRY)
    assert {"data_scout", "geocompute"} <= set(SUBAGENT_ROLES)
    validate_subagent_role_registry()  # 身份一致 + 无重复定义
    for name in ("data_scout", "geocompute"):
        role = get_subagent_role(name)
        assert role.allow_mutation is False  # 两个专家都只读
        assert role.expected_outputs  # 结构化输出契约
    assert isinstance(get_specialist("data_scout", clock=FakeClock()), DataScoutAgent)
    assert isinstance(get_specialist("geocompute", clock=FakeClock()), GeoComputeAgent)
    with pytest.raises(ValueError, match="未知"):
        get_specialist("ninja_hacker")


def test_spawn_tool_description_renders_new_roles():
    """spawn_subagent 工具描述动态渲染新角色预算（注册后自动可见）。"""
    from app.tools.subagent import _role_budget_lines

    ensure_subagent_roles_registered()
    lines = _role_budget_lines()
    assert "data_scout" in lines and "geocompute" in lines
    assert "只读" in lines


async def test_delegate_carries_role_and_specialist_prompt():
    """T16：委派路径 → dispatcher.run 收到 role= 与专属提示词任务头。"""
    class RecordingDispatcher:
        def __init__(self) -> None:
            self.calls: List[Tuple[str, Dict[str, Any]]] = []

        async def run(self, *, task: str, **kwargs: Any) -> SubagentResult:
            self.calls.append((task, kwargs))
            return SubagentResult(success=True, summary="ok")

    agent = _scout(clock=FakeClock())
    dispatcher = RecordingDispatcher()
    result = await agent.delegate(dispatcher, task="找北京市三甲医院 POI")
    assert result.success is True
    task_text, kwargs = dispatcher.calls[0]
    assert kwargs.get("role") == "data_scout"
    assert "数据猎手" in task_text  # 专属系统提示词边界注入
    assert "找北京市三甲医院 POI" in task_text


def test_specialists_have_disjoint_tool_surfaces():
    """RBAC 不变量：两专家工具面不相交（跨域即越权）。"""
    scout_surface = set(DataScoutAgent.TOOL_ALLOWLIST)
    compute_surface = set(GeoComputeAgent.TOOL_ALLOWLIST)
    assert scout_surface & compute_surface == set()
    assert len(scout_surface) >= 10 and len(compute_surface) >= 10
