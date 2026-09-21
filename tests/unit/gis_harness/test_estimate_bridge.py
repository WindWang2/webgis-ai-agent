"""node/tool 估算口径统一桥测试（ADR-0210 D1：parity 不变式）。"""
import pytest

from app.services.gis_harness.capability_graph import (
    KIND_ALGORITHM,
    KIND_MODEL,
    KIND_TOOL,
    GraphNode,
)
from app.services.gis_harness.estimate_bridge import (
    latency_class_of,
    memory_class_of,
    resource_estimate_for_node,
)
from app.services.gis_harness.qualification_v8 import estimate_for_node
from app.services.governor.contract import (
    CONSERVATIVE_FLOORS,
    Certainty,
    Dimension,
)
from app.services.governor.dispatch_adapter import classify_tool
from app.services.governor.estimation import estimate_for_tool


def _tool_node(name="query_osm_boundary", **extras) -> GraphNode:
    return GraphNode(name, KIND_TOOL, "test", label=name, extras=extras)


def test_parity_bridge_vs_dispatch_for_same_tool():
    """parity 不变式：同一工具经 planner 桥与经 dispatch _build_demand
    产出的 wall/memory 数值估算完全一致（无声明 extras 时）。"""
    node = _tool_node("query_osm_boundary", cost="medium")
    bridged = resource_estimate_for_node(node)
    subsystem, rclass = classify_tool("query_osm_boundary", "medium")
    dispatched = estimate_for_tool(
        "query_osm_boundary", tool_class="medium", subsystem=subsystem)
    dispatched = dispatched.model_copy(update={"resource_class": rclass})
    assert bridged.model_dump() == dispatched.model_dump()


def test_declared_classes_override_via_same_prior_table():
    """graph 声明档位 → 同一先验表的对应切片（数值单源，无第二份表）。"""
    node = _tool_node("t", cost="light", memory_class="heavy",
                      latency_class="fast")
    est = resource_estimate_for_node(node)
    mem = est.dim(Dimension.MEMORY_BYTES)
    wall = est.dim(Dimension.WALL_TIME_S)
    assert mem.source == "declared:memory_class"
    assert wall.source == "declared:latency_class"
    # roundtrip：声明档位 → 数值 → 反推档位 恒等
    assert memory_class_of(est) == "heavy"
    assert latency_class_of(est) == "fast"


def test_prior_derived_classes_when_no_declarations():
    node = _tool_node("get_district", cost="light")
    est = resource_estimate_for_node(node)
    assert latency_class_of(est) == "fast"      # light 先验 exp 0.5s
    assert memory_class_of(est) == "light"      # light 先验 exp 48MiB


def test_model_face_gpu_and_vram_unknown_floor():
    node = GraphNode("tiny-landcover-seg@1", KIND_MODEL, "test",
                     extras={"provider_ref": "gpu:cuda"})
    est = resource_estimate_for_node(node)
    assert est.gpu_required is True
    assert latency_class_of(est) == "slow"
    vram = est.dim(Dimension.GPU_MEMORY_BYTES)
    assert vram.certainty is Certainty.UNKNOWN
    # 准入口径：unknown → 保守地板（绝不静默 0）
    assert est.adjudged(Dimension.GPU_MEMORY_BYTES) == \
        pytest.approx(CONSERVATIVE_FLOORS[Dimension.GPU_MEMORY_BYTES])
    cpu_model = GraphNode("ref-seg@1", KIND_MODEL, "test",
                          extras={"provider_ref": "local_reference"})
    assert resource_estimate_for_node(cpu_model).gpu_required is False


def test_algorithm_face_complexity_to_class():
    heavy = GraphNode("voronoi", KIND_ALGORITHM, "test",
                      extras={"complexity": "high"})
    est = resource_estimate_for_node(heavy)
    assert latency_class_of(est) == "slow"
    assert memory_class_of(est) == "heavy"
    light = GraphNode("buffer", KIND_ALGORITHM, "test", extras={})
    est2 = resource_estimate_for_node(light)
    assert latency_class_of(est2) == "fast"


def test_estimate_for_node_derives_from_bridge():
    """estimate_for_node 分类档位 = rg.v1 投影派生；basis 语义保留。"""
    declared = _tool_node("t", cost="light", memory_class="medium",
                          latency_class="slow", execution_policy="celery")
    d_est = estimate_for_node(declared)
    assert d_est.latency_class == "slow"
    assert d_est.memory == "medium"
    assert d_est.basis["latency_class"] == "declared"
    assert d_est.basis["memory"] == "declared"
    assert d_est.basis["cpu"] == "declared"       # celery 推断
    assert d_est.confidence == pytest.approx(0.5)  # 2/4（celery 不计权）

    derived = _tool_node("t2", cost="light")
    r_est = estimate_for_node(derived)
    assert r_est.basis["latency_class"] == "estimated"
    assert r_est.basis["memory"] == "estimated"
    assert r_est.latency_class == latency_class_of(
        resource_estimate_for_node(derived))
    assert 0.0 <= r_est.confidence <= 1.0


def test_estimate_for_node_model_basis_compat():
    node = GraphNode("m@1", KIND_MODEL, "test",
                     extras={"provider_ref": "cuda:x"})
    est = estimate_for_node(node)
    assert est.basis["latency_class"] == "declared"   # 既有断言兼容
    assert est.basis["memory"] == "estimated"          # rg.v1 派生新披露
    assert est.latency_class == "slow"
