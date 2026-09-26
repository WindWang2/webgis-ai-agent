"""F08 / ADR-0214 D1：workflow 节点 ResourceEstimate 桥（parity 不变式）。

验收面：
- **parity**：同一工具/参数/证据下，workflow 节点桥与 dispatch
  ``_build_demand`` 产出逐维全等的 dims 与相同 resource_class（口径一致；
  subsystem 归因差异是有意的诚实标注）；
- 先验表单源（数值必须来自 estimation.class_prior，不许第二张表）；
- 节点声明覆盖（memory_class/latency_class/estimated_*/vram_bytes）＝
  同表切片或显式申报；GPU 无证据 → unknown 维（保守地板）；
- 输入行数兜底细化仅在无更强证据时生效（不破坏 parity）；
- render/export 形状节点走既有投影（render_formula.v1/export_formula.v1）；
- priority 映射与校准键：确定性、有界。
"""
from __future__ import annotations

import math

import pytest

from app.services.governor.contract import (
    CONSERVATIVE_FLOORS,
    Certainty,
    Dimension,
    ExecutionPriority,
    ResourceClass,
    Subsystem,
)
from app.services.governor.dispatch_adapter import (
    GovernorDispatchAdapter,
)
from app.services.governor.estimation import class_prior
from app.services.workflow_runtime.estimate import (
    execution_priority_for_node,
    node_estimate_key,
    node_declared_cost,
    resource_estimate_for_workflow_node,
)


# ── parity：同工具同参数 → dispatch 面与 workflow 节点面逐维全等 ─────────
@pytest.mark.parametrize("tool,cost,args", [
    ("query_parcel", "light", {"limit": 100}),
    ("raster_ndvi_calc", "heavy", {"width": 512, "height": 512}),
    ("search_datasets", "medium", {"k": 5}),
])
def test_parity_with_dispatch_demand(tool, cost, args):
    def metadata_fn(name):
        return {"cost": cost}

    adapter = GovernorDispatchAdapter(governor=None, metadata_fn=metadata_fn)
    demand = adapter._build_demand(tool, args, "s1", "t1")
    node = {
        "node_id": "n1", "kind": "transform",
        "capability": tool,
        "resources": {"cost": cost},
    }
    est = resource_estimate_for_workflow_node(node, params=args)
    assert est.resource_class is demand.estimate.resource_class
    assert est.subsystem is Subsystem.WORKFLOW  # 诚实归因差异（有意）
    assert set(est.dims.keys()) == set(demand.estimate.dims.keys())
    for dim in est.dims:
        a, b = est.dims[dim], demand.estimate.dims[dim]
        assert a.certainty is b.certainty
        for field in ("min", "expected", "max"):
            av, bv = getattr(a, field), getattr(b, field)
            if av is None or bv is None:
                assert av == bv
            else:
                assert math.isclose(av, bv, rel_tol=1e-9), (dim, field)


def test_parity_without_args_and_rows():
    """无参数无行数证据时与 dispatch 面同样逐维全等（parity 基线）。"""
    adapter = GovernorDispatchAdapter(
        governor=None, metadata_fn=lambda _n: {"cost": "light"})
    demand = adapter._build_demand("plain_tool", {}, "s1", "t1")
    est = resource_estimate_for_workflow_node(
        {"node_id": "n", "kind": "transform", "capability": "plain_tool",
         "resources": {"cost": "light"}})
    assert est.dim(Dimension.MEMORY_BYTES).expected == \
        demand.estimate.dim(Dimension.MEMORY_BYTES).expected
    assert est.dim(Dimension.WALL_TIME_S).expected == \
        demand.estimate.dim(Dimension.WALL_TIME_S).expected


# ── 先验表单源 ────────────────────────────────────────────────────────
def test_priors_come_from_single_table():
    node = {"node_id": "n", "kind": "transform",
            "resources": {"memory_class": "heavy", "cost": "light"}}
    est = resource_estimate_for_workflow_node(node)
    m_lo, m_exp, m_hi = class_prior("heavy")[:3]
    dv = est.dim(Dimension.MEMORY_BYTES)
    assert (dv.min, dv.expected, dv.max) == (m_lo, m_exp, m_hi)
    assert dv.source == "declared:memory_class"


# ── 声明覆盖 / GPU unknown 地板 ───────────────────────────────────────
def test_declared_estimates_and_vram():
    node = {"node_id": "n", "kind": "transform",
            "resources": {"estimated_memory_mb": 512,
                          "estimated_wall_s": 20,
                          "gpu_required": True, "vram_bytes": 8 * 1024**3}}
    est = resource_estimate_for_workflow_node(node)
    mem = est.dim(Dimension.MEMORY_BYTES)
    assert mem.expected == pytest.approx(512 * 1024**2)
    assert mem.certainty is Certainty.ESTIMATED
    wall = est.dim(Dimension.WALL_TIME_S)
    assert wall.expected == pytest.approx(20.0)
    vram = est.dim(Dimension.GPU_MEMORY_BYTES)
    assert vram.certainty is Certainty.KNOWN
    assert vram.expected == 8 * 1024**3
    assert est.gpu_required is True


def test_gpu_declared_without_vram_is_unknown_floor():
    node = {"node_id": "n", "kind": "transform",
            "resources": {"gpu_required": True}}
    est = resource_estimate_for_workflow_node(node)
    vram = est.dim(Dimension.GPU_MEMORY_BYTES)
    assert vram.certainty is Certainty.UNKNOWN
    # unknown≠0：准入判定按保守地板计（绝不静默 0）
    assert est.adjudged(Dimension.GPU_MEMORY_BYTES) == \
        CONSERVATIVE_FLOORS[Dimension.GPU_MEMORY_BYTES]


def test_gpu_false_has_no_vram_dim():
    node = {"node_id": "n", "kind": "transform",
            "resources": {"gpu_required": False}}
    est = resource_estimate_for_workflow_node(node)
    assert not est.dim(Dimension.GPU_MEMORY_BYTES).is_meaningful()
    assert est.gpu_required is False


# ── 输入行数兜底（只在无更强证据时；不破坏 parity）────────────────────
def test_input_rows_refine_only_when_no_evidence():
    node = {"node_id": "n", "kind": "transform", "capability": "plain_tool",
            "resources": {"cost": "light"}}
    est = resource_estimate_for_workflow_node(node, input_rows=5000)
    feats = est.dim(Dimension.FEATURE_COUNT)
    assert feats.certainty is Certainty.ESTIMATED
    assert feats.source == "workflow:input_identity"
    assert feats.expected == 5000


def test_input_rows_do_not_override_df_evidence():
    """DF 成本证据在场时行数不覆盖（与 dispatch 面同证据 → 同值）。"""
    node = {"node_id": "n", "kind": "transform",
            "capability": "query_datasets",
            "resources": {"cost": "medium"}}
    args = {"limit": 800}
    base = resource_estimate_for_workflow_node(node, params=args)
    refined = resource_estimate_for_workflow_node(
        node, params=args, input_rows=999_999)
    base_feats = base.dim(Dimension.FEATURE_COUNT)
    if base_feats.is_meaningful():
        # dispatch 同路径证据优先 —— 行数兜底不得覆盖
        assert refined.dim(Dimension.FEATURE_COUNT).expected == \
            base_feats.expected


# ── render/export 形状节点 ────────────────────────────────────────────
def test_render_node_uses_render_formula():
    node = {"node_id": "r1", "kind": "cartography",
            "resources": {"render": {"layers": 4, "features": 2000,
                                     "width": 1920, "height": 1080}}}
    est = resource_estimate_for_workflow_node(node)
    assert est.subsystem is Subsystem.WORKFLOW
    assert est.dim(Dimension.RENDER_WORK_UNITS).is_meaningful()
    assert "render_formula.v1" in est.source
    # 单调性：要素翻倍 → 估工不減
    bigger = resource_estimate_for_workflow_node({
        "node_id": "r1", "kind": "cartography",
        "resources": {"render": {"layers": 4, "features": 4000,
                                 "width": 1920, "height": 1080}}})
    assert bigger.dim(Dimension.RENDER_WORK_UNITS).expected >= \
        est.dim(Dimension.RENDER_WORK_UNITS).expected


def test_export_node_uses_export_formula_and_export_channel():
    node = {"node_id": "e1", "kind": "export",
            "resources": {"export": {"pages": 2, "width": 800,
                                     "height": 600}}}
    est = resource_estimate_for_workflow_node(node)
    assert est.resource_class is ResourceClass.EXPORT
    assert est.dim(Dimension.RENDER_WORK_UNITS).is_meaningful()
    assert "export_formula.v1" in est.source


# ── kind 保守档兜底 ───────────────────────────────────────────────────
def test_kind_fallback_heavy_raster_profile():
    node = {"node_id": "n", "kind": "transform",
            "resources": {"profile": "raster"}}
    est = resource_estimate_for_workflow_node(node)
    assert node_declared_cost(node) == "heavy"
    assert est.resource_class in (ResourceClass.RASTER, ResourceClass.HEAVY)
    # 未知 kind 的诚实披露
    assert "workflow:" in est.source or est.reason


def test_empty_node_estimate_is_conservative_light():
    est = resource_estimate_for_workflow_node({})
    assert est.resource_class is ResourceClass.LIGHT
    assert est.dim(Dimension.MEMORY_BYTES).is_meaningful()


# ── priority 映射 / 校准键 ────────────────────────────────────────────
def test_execution_priority_mapping_deterministic():
    assert execution_priority_for_node({"priority": 10}) is \
        ExecutionPriority.INTERACTIVE
    assert execution_priority_for_node({"priority": 8}) is \
        ExecutionPriority.INTERACTIVE
    assert execution_priority_for_node({"priority": 5}) is \
        ExecutionPriority.NORMAL
    assert execution_priority_for_node({}) is ExecutionPriority.NORMAL
    assert execution_priority_for_node({"priority": 2}) is \
        ExecutionPriority.BATCH
    assert execution_priority_for_node({"priority": -10}) is \
        ExecutionPriority.BATCH
    assert execution_priority_for_node({"priority": "garbage"}) is \
        ExecutionPriority.NORMAL


def test_node_estimate_key_bounded():
    key = node_estimate_key({"kind": "transform",
                             "capability": "x" * 300})
    assert key.startswith("workflow:transform:")
    assert len(key) <= 128
