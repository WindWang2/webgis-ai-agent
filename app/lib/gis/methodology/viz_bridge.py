"""Algorithm Output ↔ Visualization Bridge —— 类型化映射（Epic 11 §5.I）。

    artifact semantic type
      → VisualizationFamily（表达族）
      → legend semantics（图例语义：graduated/categorical/continuous/…）
      → template slot bindings（图例/色条/图表/不确定性面板绑定）

数据来源（确定性交叉投影，不复制 canonical 事实）：

- ``ArtifactTypeDescriptor.typical_map_models``（收编文档性字段为有校验
  消费的桥边——图边 ``typical_visualization`` 已在 graph 投影）；
- ``CapabilityDescriptor.compatible_map_models``；
- ``MapComponentDescriptor.compatible_artifact_types``。

分歧语义（R1-F5 冻结）：

- 悬空 id（registry 引用不存在）→ fatal（``methodology_intel:`` 上报，
  graph build 同样 fail-closed）；
- 跨源**模型分歧**（同一 artifact 在不同源指向不同 map model 家族）
  → 结构化 ``DISCLOSURE`` 条目（进解释面，不 fatal、不静默合并）；
  已知分歧在 ``_RECONCILE_OVERRIDES`` 审定表预解析（声明权威来源 +
  披露文案），不改 canonical registries。

零 LLM、零 I/O、确定性；覆盖 Epic §5.I 的全部产物面（KDE/热点显著性/
插值+不确定性/可达等时圈/分类栅格/变化图/网络结果/地形衍生/统计图表）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

#: viz bridge schema 版本（进指纹）。
VIZ_BRIDGE_SCHEMA_VERSION = 1

#: 可视化表达族（封闭词表；架构 §3.7 冻结）。
VIZ_FAMILIES = (
    "point_distribution",
    "choropleth_normalized",
    "choropleth_raw",
    "density_surface",
    "hotspot_significance",
    "cluster_map",
    "interpolation_surface",
    "uncertainty_overlay",
    "accessibility_isochrone",
    "service_area",
    "classified_raster",
    "change_map",
    "flow_map",
    "terrain_derivative",
    "statistics_chart",
    "comparison_frame",
)

#: 图例语义（封闭词表；renderer 域的 legend 族选择依据）。
LEGEND_SEMANTICS = (
    "graduated",      # 分级（分类面/分级符号）
    "categorical",    # 类别（分类图例）
    "continuous",     # 连续（色条）
    "significance",   # 显著性（Gi*/LISA 冷热+置信）
    "uncertainty",    # 不确定性（区间/方差）
    "none",           # 无图例（纯展示）
)


class VizBridgeEntry(BaseModel):
    """一条审定桥接：artifact 类型 → 表达族 → 图例语义 → 槽位绑定。"""

    artifact_type: str                # ⊆ ArtifactTypeRegistry（校验）
    family: str                       # ⊆ VIZ_FAMILIES
    legend_semantics: str             # ⊆ LEGEND_SEMANTICS
    slot_bindings: Tuple[str, ...] = ()   # 组件 id/type（校验）
    #: 权威 map model（reconcile 后；空 = 由 artifact typical 投影）
    authoritative_map_models: Tuple[str, ...] = ()
    note: str = ""

    @field_validator("slot_bindings")
    @classmethod
    def _bounded_slots(cls, v: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(str(x)[:40] for x in tuple(v)[:6])

    @field_validator("note")
    @classmethod
    def _bounded_note(cls, v: str) -> str:
        return v[:200]


class VizDisclosure(BaseModel):
    """跨源分歧的结构化披露（进解释面；不 fatal）。"""
    artifact_type: str
    sources: Tuple[str, ...] = ()       # 分歧来源（artifact/capability/component）
    models: Tuple[str, ...] = ()
    resolution: str = ""                # 审定解析说明（有界）

    def to_bounded_dict(self) -> Dict[str, str]:
        return {
            "artifact_type": self.artifact_type[:40],
            "sources": [s[:16] for s in self.sources[:3]],
            "models": [m[:48] for m in self.models[:6]],
            "resolution": self.resolution[:160],
        }


def _e(artifact: str, family: str, legend: str,
       slots: Tuple[str, ...] = (), models: Tuple[str, ...] = (),
       note: str = "") -> VizBridgeEntry:
    return VizBridgeEntry(
        artifact_type=artifact, family=family, legend_semantics=legend,
        slot_bindings=slots, authoritative_map_models=models, note=note)


#: 审定桥接表（Epic §5.I 全覆盖；纯加法演进；悬空 id 由中央校验拦截）。
_VIZ_BRIDGE: Tuple[VizBridgeEntry, ...] = (
    # 密度面（KDE）——连续色条 + 方法论披露（核平滑估计非真实密度）
    _e("density_surface", "density_surface", "continuous",
       slots=("continuous_colorbar", "title", "legend"),
       note="核平滑估计：连续色条；定量结论需面积归一化语境。"),
    # 热点显著性——显著性图例（非视觉热力渐变）
    _e("hotspot_result", "hotspot_significance", "significance",
       slots=("legend", "title", "methodology_note"),
       note="Gi*/LISA 冷热 + 置信分级；视觉热力不是显著性表达。"),
    # 插值面——连续 + 不确定性面板（克里金方差配套）
    _e("raster_surface", "interpolation_surface", "continuous",
       slots=("continuous_colorbar", "title", "uncertainty_panel"),
       note="连续预测面：色条 + 预测方差/样本限制披露（插值诚实）。"),
    # 地形衍生——连续
    _e("terrain_surface", "terrain_derivative", "continuous",
       slots=("continuous_colorbar", "title"),
       note="坡度/指数：连续色条；局部度量 CRS 义务随义务链携带。"),
    # 遥感指数——连续
    _e("remote_sensing_index", "classified_raster", "continuous",
       slots=("continuous_colorbar", "title"),
       note="光谱指数：连续色条 + 波段语义披露。"),
    # 变化集——显著性/分级 + 对比框
    _e("change_set", "change_map", "significance",
       slots=("legend", "title", "chart_panel"),
       note="变化方向 + 幅度：显著性/发散语义；双期对比框配套。"),
    # 服务区/邻近域
    _e("service_area", "service_area", "graduated",
       slots=("legend", "title", "scale_bar"),
       note="多级服务域：时间/距离分级图例。"),
    _e("proximity_zone", "service_area", "graduated",
       slots=("legend", "title", "scale_bar"),
       note="缓冲环：半径分级图例。"),
    # 网络结果
    _e("line_feature_set", "flow_map", "categorical",
       slots=("legend", "title"),
       note="路径/河网/设施连线：类别或分级线宽。"),
    _e("network_graph", "flow_map", "graduated",
       slots=("legend", "title"),
       note="网络图结构：分级语义。"),
    # 点/面要素集
    _e("point_feature_set", "point_distribution", "categorical",
       slots=("legend", "title", "scale_bar"),
       note="点分布：类别符号；分布描述不做推断。"),
    _e("polygon_feature_set", "choropleth_raw", "graduated",
       slots=("legend", "title"),
       note="面要素：分级填色；计数比较需归一化语境（raw/normalized 分流）。"),
    _e("poi_feature_set", "point_distribution", "categorical",
       slots=("legend", "title", "scale_bar"),
       note="POI 集合：类别符号。"),
    _e("admin_boundary_set", "choropleth_raw", "graduated",
       slots=("legend", "title"),
       note="行政边界：参考层语义。"),
    # 统计表/图表
    _e("stats_table", "statistics_chart", "none",
       slots=("chart_panel", "table_panel", "title"),
       note="统计表：图表/表格面板绑定。"),
    _e("chart_spec", "statistics_chart", "none",
       slots=("chart_panel", "title"),
       note="图表规格：chart 面板绑定。"),
    _e("admin_aggregate_table", "statistics_chart", "graduated",
       slots=("chart_panel", "legend", "title"),
       note="行政聚合表：图表面板 + 分级填色主图配套（raw→normalized 分流）。"),
    _e("grid_aggregate", "choropleth_raw", "graduated",
       slots=("legend", "chart_panel", "title"),
       note="格网聚合：格网填色 + 计数语境披露。"),
    # OD
    _e("od_matrix", "flow_map", "graduated",
       slots=("legend", "chart_panel", "title"),
       note="OD 矩阵：流量分级。"),
    _e("od_table", "flow_map", "graduated",
       slots=("chart_panel", "legend"),
       note="OD 表：流线图配套。"),
)


#: 已知跨源分歧的审定 reconcile（R1-F5；不改 canonical registries）。
_RECONCILE_OVERRIDES: Dict[str, VizDisclosure] = {
    # density.analytical.mixed 输出 density_surface 但能力声明 choropleth 族：
    # 密度面主表达 = 连续色条面；choropleth 族是其行政聚合伴生产物的表达。
    "density_surface": VizDisclosure(
        artifact_type="density_surface",
        sources=("artifact", "capability"),
        models=("isoline_contour", "raster_surface", "visual_heatmap",
                "administrative_choropleth", "aggregate_grid"),
        resolution="密度面权威表达 = 连续面族（raster_surface/kernel_density）；"
                   "choropleth 属行政聚合伴生视图（bridge 取 continuous）。"),
    # interpolation.dasymetric 输出 polygon_feature_set 但声明 dasymetric_map：
    # 面插值产物 = 面要素 + 专属 dasymetric 表达并存。
    "polygon_feature_set": VizDisclosure(
        artifact_type="polygon_feature_set",
        sources=("artifact", "capability"),
        models=("extrusion_3d", "dasymetric_map"),
        resolution="面插值权威表达 = dasymetric_map（分级填色语义）；"
                   "extrusion_3d 为 3D 场景变体（bridge 取 graduated）。"),
}


def _default_artifacts() -> Any:
    from app.lib.gis.artifacts import get_artifact_type_registry
    return get_artifact_type_registry()


def bridge_artifact(
    artifact_type: str,
    *,
    artifacts: Any = None,
) -> Optional[VizBridgeEntry]:
    """单 artifact 桥接查询（O(n) 审定表扫描；表 ≤ 21 条，预算内）。"""
    registry = artifacts or _default_artifacts()
    if not registry.has(artifact_type):
        return None
    for entry in _VIZ_BRIDGE:
        if entry.artifact_type == artifact_type:
            return entry
    return None


def bridge_plan(
    artifact_types: List[str],
    *,
    artifacts: Any = None,
) -> Dict[str, Any]:
    """产物集合 → 桥接计划（family/legend/slots + 披露；有界投影）。

    这就是「算法输出 → 制图表达」的类型化链：模板组合规划器消费
    slot_bindings 决定组件槽位，解释 API 消费 note/disclosures。
    """
    registry = artifacts or _default_artifacts()
    families: List[str] = []
    legends: List[str] = []
    slots: List[str] = []
    entries: List[Dict[str, str]] = []
    disclosures: List[Dict[str, str]] = []
    for at in artifact_types[:12]:
        if not registry.has(at):
            disclosures.append(VizDisclosure(
                artifact_type=at, resolution="artifact 类型未注册（悬空）",
            ).to_bounded_dict())
            continue
        entry = bridge_artifact(at, artifacts=registry)
        if entry is None:
            # 有类型无桥接条目 → 披露（不虚构）
            disclosures.append(VizDisclosure(
                artifact_type=at,
                resolution="无审定桥接条目：回退 artifact typical_map_models 投影。",
            ).to_bounded_dict())
            desc = registry.get(at)
            models = list(getattr(desc, "typical_map_models", ()) or ())
            entries.append({"artifact_type": at[:40],
                            "family": "statistics_chart",
                            "legend": "graduated",
                            "models": ",".join(m[:32] for m in models[:4])})
            families.append("statistics_chart")
            legends.append("graduated")
            continue
        families.append(entry.family)
        legends.append(entry.legend_semantics)
        for s in entry.slot_bindings:
            if s not in slots:
                slots.append(s)
        entries.append({"artifact_type": at[:40],
                        "family": entry.family[:32],
                        "legend": entry.legend_semantics[:16],
                        "note": entry.note[:120]})
        if at in _RECONCILE_OVERRIDES:
            disclosures.append(
                _RECONCILE_OVERRIDES[at].to_bounded_dict())
    # 主表达族 = 首个非 chart 族（图表是伴生不是主图）
    primary = next((f for f in families
                    if f != "statistics_chart"), "")
    if not primary and families:
        primary = families[0]
    return {
        "primary_family": primary[:40],
        "families": list(dict.fromkeys(families))[:8],
        "legend_semantics": list(dict.fromkeys(legends))[:4],
        "slot_bindings": slots[:8],
        "entries": entries[:12],
        "disclosures": disclosures[:6],
    }


def validate_bridge(
    *,
    artifact_type_exists: Any = None,
    map_model_exists: Any = None,
    component_exists: Any = None,
) -> List[str]:
    """桥接表对账（悬空 id → fatal issue；R1-F5 分歧语义）。"""
    registry = _default_artifacts()
    issues: List[str] = []
    seen: set = set()
    for entry in _VIZ_BRIDGE:
        tag = f"viz_bridge[{entry.artifact_type}]"
        if entry.artifact_type in seen:
            issues.append(f"{tag}: 重复条目")
        seen.add(entry.artifact_type)
        if artifact_type_exists and not artifact_type_exists(entry.artifact_type):
            issues.append(f"{tag}: artifact 类型未注册")
        if entry.family not in VIZ_FAMILIES:
            issues.append(f"{tag}: unknown family {entry.family}")
        if entry.legend_semantics not in LEGEND_SEMANTICS:
            issues.append(f"{tag}: unknown legend semantics")
        for slot in entry.slot_bindings:
            if component_exists and not component_exists(slot):
                issues.append(f"{tag}: component {slot} 未注册")
        for mm in entry.authoritative_map_models:
            if map_model_exists and not map_model_exists(mm):
                issues.append(f"{tag}: map model {mm} 未注册")
    for at in _RECONCILE_OVERRIDES:
        if artifact_type_exists and not artifact_type_exists(at):
            issues.append(f"viz_bridge_reconcile[{at}]: artifact 类型未注册")
    return issues


def content_fingerprint() -> str:
    payload = {
        "version": VIZ_BRIDGE_SCHEMA_VERSION,
        "entries": [e.model_dump(mode="json") for e in _VIZ_BRIDGE],
        "reconcile": {k: v.model_dump(mode="json")
                      for k, v in sorted(_RECONCILE_OVERRIDES.items())},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "VIZ_BRIDGE_SCHEMA_VERSION",
    "VIZ_FAMILIES",
    "LEGEND_SEMANTICS",
    "VizBridgeEntry",
    "VizDisclosure",
    "bridge_artifact",
    "bridge_plan",
    "validate_bridge",
    "content_fingerprint",
]
