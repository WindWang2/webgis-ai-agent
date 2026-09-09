"""Scientific Contract V4 ratchet 数据（本模块只持数据，不持逻辑）。

Wave 1（science-v4）：把「heavy 算法必须声明 ResourceEnvelope /
CancellationProfile / NumericalTolerance」从口头约定变成机器可查的
ratchet：

- 判据与 gen_science_benchmark_manifest._is_heavy 的开销子集对齐
  （cpu_cost/memory_cost == "high"）；backend_variants / envelope 本身
  也触发 heavy，但那类算法已自带声明，天然满足 ratchet。
- ``OWNED_DOMAIN_PREFIXES``：本 Epic（science-v4）ownership 域 —— 这些域
  **禁止**进入 allowlist（必须全量声明），由 ratchet 测试单独钉死。
- ``UNDECLARED_HEAVY_ALLOWLIST``：存量 heavy 算法的冻结清单（2026-09
  基线 44 项）。语义：
  1. 不在清单内的 heavy 算法缺任一声明 → ratchet 红（新增即受约束）；
  2. 清单成员必须**真的 heavy 且真的缺声明** —— 任何成员补齐声明后必须
     同步从清单删除（否则「洗白检测」红），清单只许收缩不许扩张；
  3. 清单按字典序冻结，diff 一目了然。

与 GATE_THRESHOLDS（app/lib/quality/manifest.py）同一棘轮哲学：基线
只升不降。后续 Epic 给 network/remote/sar 等域补声明时，删对应条目即可。
"""
from __future__ import annotations

from typing import FrozenSet, Tuple

# science-v4 ownership：这两个域的 heavy 算法必须全量声明（禁止 allowlist）。
OWNED_DOMAIN_PREFIXES: Tuple[str, ...] = ("interpolation.", "terrain.")

# 冻结基线（2026-09，44 项）：heavy 但尚未声明 resource_envelope /
# cancellation_profile / tolerance 的存量算法。只许删除，不许新增。
UNDECLARED_HEAVY_ALLOWLIST: FrozenSet[str] = frozenset({
    "data.ingest.pipeline",
    "density.analytical.mixed",
    "network.accessibility",
    "network.centrality",
    "network.closest_facility",
    "network.gravity_access",
    "network.huff_interaction",
    "network.isochrone",
    "network.isochrone.local",
    "network.location_allocation",
    "network.mclp_exact",
    "network.od_matrix",
    "network.optimize_route",
    "network.pcenter_exact",
    "network.pmedian_exact",
    "network.route_optimization",
    "network.service_area.multi",
    "network.shortest_path",
    "point_pattern.cross_pcf",
    "point_pattern.ripley_k_env",
    "point_pattern.space_time_k",
    "raster.algebra",
    "raster.resample.grid",
    "remote.change.raster",
    "remote.ica",
    "remote.mad_change",
    "remote.mnf",
    "remote.ndvi",
    "remote.pca",
    "sar.glcm_texture",
    "sar.multitemporal_speckle",
    "sar.speckle_filter",
    "spatial.gwr",
    "spatial.hotspot.local",
    "spatial.kde.contours",
    "spatial.kde.surface",
    "spatial.mgwr",
    "spatial.sar_ml",
    "spatial.sem_ml",
    "stats.h3_hotspot",
    "stats.h3_lisa",
    "stats.local_geary",
    "stats.st_dbscan",
    "temporal.hotspot",
})


def is_heavy_cost(algo) -> bool:
    """ratchet 的 heavy 判据（开销声明子集；与 benchmark manifest 对齐）。"""
    return algo.cpu_cost == "high" or algo.memory_cost == "high"


def missing_v4_declarations(algo) -> Tuple[str, ...]:
    """返回该算法缺失的 V3/V4 科学契约字段名（空元组 = 声明齐全）。"""
    missing = []
    if algo.resource_envelope is None:
        missing.append("resource_envelope")
    if not algo.cancellation_profile:
        missing.append("cancellation_profile")
    if algo.tolerance is None:
        missing.append("tolerance")
    return tuple(missing)
