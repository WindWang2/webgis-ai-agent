"""Plan-node 数值资源 hint 投影（H06 D1）+ statistics heavy 族 descriptor 声明。

锁定：descriptor ``resource_envelope`` × rows 真值 → plan-node
memory/bytes/cpu 数值（ADR-0213 D1 单一先验表纪律 —— cpu_seconds 经
class_prior，无第二份表）；未注册操作逐字段回退旧行为；science_contract_v4
ratchet allowlist 净缩减 5 项（只删不加）。
"""

from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.services.geocompute.estimate_hints import (
    OPERATION_DESCRIPTOR_IDS,
    plan_estimate_for_operation,
)


def test_buffer_operation_projects_memory_and_cpu():
    est = plan_estimate_for_operation("buffer_analysis", rows=100_000,
                                      method="cost_hint")
    assert est.rows == 100_000
    assert est.bytes == 51_200_000  # 100k × 512B（geometry.buffer 声明）
    assert est.memory_mb == 49.0
    # cpu_seconds 来自 class_prior（medium 档 wall 期望 5s）——单一先验表
    assert est.cpu_seconds == 5.0
    assert est.confidence == "medium"


def test_unmapped_operation_falls_back_rows_only():
    est = plan_estimate_for_operation("query", rows=50_000, method="unknown")
    assert est.rows == 50_000
    assert est.memory_mb is None
    assert est.cpu_seconds is None
    assert est.confidence == "assumption"


def test_unknown_rows_falls_back_rows_only():
    est = plan_estimate_for_operation("buffer_analysis", rows=None)
    assert est.rows is None
    assert est.memory_mb is None


def test_identity_map_only_confident_ops():
    # 泛化操作（aggregate/filter/query/attribute_join）不猜身份
    for op in ("aggregate", "filter", "query", "attribute_join"):
        assert op not in OPERATION_DESCRIPTOR_IDS


def test_mapped_descriptors_are_registered_with_envelope():
    registry = get_algorithm_registry()
    for op, algo_id in OPERATION_DESCRIPTOR_IDS.items():
        d = registry.get(algo_id)
        assert d is not None, f"{op} → {algo_id} 未注册"
        assert d.resource_envelope is not None, f"{algo_id} 缺 resource_envelope"


def test_h06_allowlist_shrink_is_pure_deletion():
    # H06 后 allowlist 不再包含 statistics heavy 族（39 项 = 44 - 5）
    from app.lib.gis.algorithms.science_contract_v4 import (
        UNDECLARED_HEAVY_ALLOWLIST,
    )
    assert len(UNDECLARED_HEAVY_ALLOWLIST) == 39
    for algo_id in ("spatial.hotspot.local", "stats.h3_hotspot",
                    "stats.h3_lisa", "stats.local_geary", "stats.st_dbscan"):
        assert algo_id not in UNDECLARED_HEAVY_ALLOWLIST


def test_statistics_heavy_family_declares_full_v4_contract():
    from app.lib.gis.algorithms.science_contract_v4 import (
        missing_v4_declarations,
    )
    registry = get_algorithm_registry()
    for algo_id in ("spatial.hotspot.local", "stats.h3_hotspot",
                    "stats.h3_lisa", "stats.local_geary", "stats.st_dbscan"):
        algo = registry.get(algo_id)
        assert algo is not None
        assert missing_v4_declarations(algo) == (), algo_id
        # 取消档案声明必须与实现一致：置换族 chunk_boundary、
        # esda/sklearn 单调用族 coarse（H06 复核过实现）
        assert algo.cancellation_profile in ("chunk_boundary", "coarse")


def test_multi_ring_buffer_envelope_scales_with_rings_via_rows():
    # 行真值已含环数放大（agent_swarm volume 层职责）；此处锁定 envelope
    # 数值按行线性放大。
    est10k = plan_estimate_for_operation("multi_ring_buffer", rows=10_000)
    est40k = plan_estimate_for_operation("multi_ring_buffer", rows=40_000)
    assert est40k.memory_mb == est10k.memory_mb * 4
