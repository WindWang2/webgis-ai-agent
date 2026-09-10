"""闭环语料（Contextual Cartographic Harness V6 Wave 16a）—— 制图类型 × 故障注入。

§35 闭环回归：每条场景声明**六段式期望**（制图意图 / 工作流骨架 /
期望能力 / 注入故障 / 期望 finding 域 / 期望修复类 / 期望裁决），供
Wave 16 闭环 runner 驱动「执行 → finding 投影 → 修复计划 → 终验裁决」
全链路断言。

词汇纪律（红线——唯一事实源，不自创）：

- ``expected_finding_domain`` ⊆ ``UNIFIED_DOMAINS``（W6
  ``unified_findings`` 投影域）；
- ``expected_repair_class`` ⊆ ``REPAIR_CLASSES``（W10
  ``repair_planner`` 16 类）；
- ``expected_verdict`` ⊆ W7 ``contracts`` 产品裁决 5 token；
- ``expected_capabilities`` 全部命中 ``CapabilityRegistry`` 已注册 id。

全部离线、确定、零 LLM、无随机、无时间戳、无网络：构建函数是
``MAP_TYPES × FAULT_KINDS`` 笛卡尔积的纯展开（顺序稳定），id 全局唯一。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from app.services.gis_harness.completion.contracts import (
    VERDICT_BLOCKED_BY_DATA,
    VERDICT_BLOCKED_BY_METHOD,
    VERDICT_NEEDS_REPAIR,
    VERDICT_READY,
    VERDICT_READY_WITH_WARNINGS,
)

#: W7 产品裁决词表（冻结 5 token；READY 仅作语料 completeness 哨兵位，
#: 本语料故障场景不期望 READY——断言面显式排除，见测试）。
CLOSED_LOOP_VERDICTS = (
    VERDICT_READY,
    VERDICT_READY_WITH_WARNINGS,
    VERDICT_NEEDS_REPAIR,
    VERDICT_BLOCKED_BY_DATA,
    VERDICT_BLOCKED_BY_METHOD,
)

#: 部署后降级面裁决（render/chart 诊断全域 degradation_only，不阻断
#: live 地图完成 —— 见 unified_findings._blocks）。
_DEGRADED = VERDICT_READY_WITH_WARNINGS


@dataclass(frozen=True)
class ClosedLoopScenario:
    """一条闭环场景：六段式期望（字段名即六段）。"""

    scenario_id: str
    map_type: str                # ⊆ MAP_TYPE_IDS（§35 约17 种制图类型）
    map_intent: str              # 中文制图意图（用户请求原文风格）
    workflow_skeleton: Tuple[str, ...]      # 工作流骨架（阶段序列）
    expected_capabilities: Tuple[str, ...]  # 期望能力（CapabilityRegistry id）
    injected_failure: str        # ⊆ FAULT_KIND_IDS（故障注入类目）
    expected_finding_domain: str  # ⊆ UNIFIED_DOMAINS（W6）
    expected_repair_class: str    # ⊆ REPAIR_CLASSES（W10 16 类）
    expected_verdict: str         # ⊆ CLOSED_LOOP_VERDICTS（W7 5 token）


# ── §35 制图类型（17 种）：意图 + 骨架 + 能力 ──────────────────────────
# 能力 id 全部取自 CapabilityRegistry（139 已注册项）；骨架为阶段动词序列。

_MAP_TABLE: Tuple[Tuple[str, str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    ("school_points", "以街道为单元统计学校数量并做分级符号点图",
     ("query", "aggregate", "thematic_cartography", "verify"),
     ("poi_query", "thematic_cartography")),
    ("admin_choropleth", "按行政区统计常住人口并做分级设色",
     ("query", "aggregate", "thematic_cartography", "verify"),
     ("admin_aggregation", "thematic_cartography")),
    ("hotspot", "对案件点做热点分析并叠加显著性标注",
     ("query", "density_analyze", "thematic_cartography", "verify"),
     ("hotspot", "kde_density")),
    ("equity", "评估各社区公园可达的人口公平性差距",
     ("query", "aggregate", "equity_analyze", "thematic_cartography", "verify"),
     ("rate_aggregation", "gravity_accessibility")),
    ("interpolation", "基于监测站对 PM2.5 做空间插值连续面",
     ("query", "interpolate", "thematic_cartography", "verify"),
     ("spatial_interpolation", "variogram_analysis")),
    ("dem", "基于 DEM 生成坡度分级与山体阴影地形图",
     ("ingest", "terrain_derive", "thematic_cartography", "verify"),
     ("terrain_slope", "terrain_hillshade")),
    ("hydrology", "基于 DEM 提取河网与汇水区并做水文分区图",
     ("ingest", "terrain_derive", "zonal_analyze", "thematic_cartography", "verify"),
     ("terrain_hydrology", "zonal_statistics")),
    ("remote_sensing_index", "计算 NDVI 植被指数并做分级遥感专题图",
     ("ingest", "band_compute", "thematic_cartography", "verify"),
     ("ndvi", "band_math")),
    ("change_detection", "对比两期影像做城市扩张变化检测图",
     ("ingest", "change_analyze", "thematic_cartography", "verify"),
     ("change_detection", "raster_change_detection")),
    ("sar", "对 SAR 影像做滤波与地形校正后输出后向散射图",
     ("ingest", "sar_preprocess", "thematic_cartography", "verify"),
     ("sar_analysis", "sar_speckle_filtering")),
    ("network_access", "计算医院 15 分钟服务区与最短路径可达图",
     ("query", "network_analyze", "thematic_cartography", "verify"),
     ("service_area", "shortest_path")),
    ("site_selection", "多准则评价筛选养老设施备选址并排序出图",
     ("query", "mcda_analyze", "thematic_cartography", "verify"),
     ("mcda_evaluation", "location_allocation")),
    ("risk", "叠加致灾因子做滑坡风险等级图并标注高风险区",
     ("query", "risk_analyze", "thematic_cartography", "verify"),
     ("scenario_simulation", "kde_density")),
    ("classification", "对遥感影像做监督分类并输出土地利用图",
     ("ingest", "classify", "thematic_cartography", "verify"),
     ("image_segmentation", "raster_reclassify")),
    ("temporal_compare", "对比近五年夜间灯光时序做增长趋势对比图",
     ("ingest", "temporal_aggregate", "thematic_cartography", "verify"),
     ("temporal_composite", "temporal_trend")),
    ("bivariate", "做人口密度与房价双变量相关性专题图",
     ("query", "bivariate_analyze", "thematic_cartography", "verify"),
     ("bivariate_morans_i", "thematic_cartography")),
    ("uncertainty", "对克里金插值结果做不确定性区间图并披露置信度",
     ("query", "interpolate", "uncertainty_analyze", "thematic_cartography", "verify"),
     ("geostatistical_simulation", "spatial_weights_diagnostics")),
)

#: 制图类型 id（§35，17 种；顺序即语料展开顺序）。
MAP_TYPE_IDS: Tuple[str, ...] = tuple(row[0] for row in _MAP_TABLE)


# ── 故障注入（12 类）：finding 域 / 修复类 / 裁决三段映射 ──────────────
# 映射依据既有分类表（repair_planner._CODE_MAP / contracts._DATA_BLOCK_CODES /
# unified_findings._blocks）：render/chart 诊断全域 degradation_only →
# READY_WITH_WARNINGS；数据源族阻断 → BLOCKED_BY_DATA；其余可修 → NEEDS_REPAIR。

_FAULT_TABLE: Tuple[Tuple[str, str, str, str, str], ...] = (
    ("missing_data", "数据源缺失", "harness_finalizer", "recompute_node", VERDICT_BLOCKED_BY_DATA),
    ("wrong_crs", "坐标系错误", "harness_finalizer", "reproject", VERDICT_NEEDS_REPAIR),
    ("empty_result", "空结果", "harness_finalizer", "reselect_method", VERDICT_BLOCKED_BY_DATA),
    ("tool_timeout", "工具超时", "workflow_runtime", "retry_tool", VERDICT_NEEDS_REPAIR),
    ("stale_artifact", "产物过期", "workflow_runtime", "recompute_node", VERDICT_NEEDS_REPAIR),
    ("missing_source", "地图源缺失", "harness_finalizer", "rerender", VERDICT_BLOCKED_BY_DATA),
    ("render_failure", "渲染失败", "render_diagnostic", "reobserve", _DEGRADED),
    ("style_failure", "样式失败", "harness_finalizer", "reapply_style", VERDICT_NEEDS_REPAIR),
    ("chart_failure", "图表失败", "render_diagnostic", "reobserve", _DEGRADED),
    ("visual_overlap", "视觉重叠", "visual", "relayout_component", VERDICT_NEEDS_REPAIR),
    ("user_edit_mid_run", "用户中途编辑", "workflow_runtime", "recompute_subgraph", VERDICT_NEEDS_REPAIR),
    ("legend_missing", "图例缺失", "harness_finalizer", "regenerate_legend", VERDICT_NEEDS_REPAIR),
)

#: 故障类目 id（12 类；顺序即语料展开顺序）。
FAULT_KIND_IDS: Tuple[str, ...] = tuple(row[0] for row in _FAULT_TABLE)


def build_closed_loop_corpus() -> List[ClosedLoopScenario]:
    """确定性构建：MAP_TYPES × FAULT_KINDS 全矩阵展开（17×12=204 条）。

    顺序稳定（类型序 × 故障序）；id ``CL-<map_type>-<fault>`` 全局唯一；
    每故障类目 17 条（≥2），每制图类型 12 条，全矩阵无空洞。
    """
    scenarios: List[ClosedLoopScenario] = []
    for map_type, intent, skeleton, capabilities in _MAP_TABLE:
        for fault, _label, domain, repair_class, verdict in _FAULT_TABLE:
            scenarios.append(ClosedLoopScenario(
                scenario_id=f"CL-{map_type}-{fault}",
                map_type=map_type,
                map_intent=intent,
                workflow_skeleton=tuple(skeleton),
                expected_capabilities=tuple(capabilities),
                injected_failure=fault,
                expected_finding_domain=domain,
                expected_repair_class=repair_class,
                expected_verdict=verdict,
            ))
    return scenarios


def scenarios_by_map_type(
    scenarios: List[ClosedLoopScenario],
) -> Dict[str, List[ClosedLoopScenario]]:
    """按制图类型分组（组内保持构建序；纯函数）。"""
    grouped: Dict[str, List[ClosedLoopScenario]] = {mid: [] for mid in MAP_TYPE_IDS}
    for sc in scenarios:
        grouped[sc.map_type].append(sc)
    return grouped


def scenarios_by_fault(
    scenarios: List[ClosedLoopScenario],
) -> Dict[str, List[ClosedLoopScenario]]:
    """按故障类目分组（组内保持构建序；纯函数）。"""
    grouped: Dict[str, List[ClosedLoopScenario]] = {fid: [] for fid in FAULT_KIND_IDS}
    for sc in scenarios:
        grouped[sc.injected_failure].append(sc)
    return grouped


__all__ = [
    "CLOSED_LOOP_VERDICTS",
    "MAP_TYPE_IDS",
    "FAULT_KIND_IDS",
    "ClosedLoopScenario",
    "build_closed_loop_corpus",
    "scenarios_by_map_type",
    "scenarios_by_fault",
]
