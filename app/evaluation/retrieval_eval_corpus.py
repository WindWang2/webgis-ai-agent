"""Retrieval V5 open-loop query→tool 评测语料（ADR-0118 决策 D7）。

V4 基线的缺口（Phase-0 审计 §7）：``retrieval_corpus`` 由 306 意图
golden + paraphrase 层生成，期望工具经 AlgorithmRegistry **capability
反查** —— 量的是 capability→surface projection，不是「用户口语 query
→ 正确工具」的开环检索精度；且无 curated hard-negative / 近重复对 /
歧义案例。

V5 语料（全部**人工金标**，与 lexical 索引 / capability 反查不同源，
不存在答案写死对齐索引词表的通道；查询为口语化措辞，非描述符文案）：

- ``direct``：明确措辞 → 唯一期望工具；
- ``near_duplicate``：最小限定词翻转的兄弟对（期望 = 正确侧，
  ``must_not_select`` = 兄弟侧 —— 限定词必须改变选择）；
- ``hard_negative``：词面陷阱（距离邻近 vs 路网可达、点计数 vs 栅格
  分区统计、批量 vs 单条……期望 = 正确工具，``must_not_select`` =
  陷阱工具）；
- ``ambiguous``：措辞天然多解（``valid_tools`` ≥2，precision@1 按
  「top-1 ∈ 合法集」计 —— 不把歧义当错误，也不当满分对齐）。

指标（``retrieval_eval_report``）：precision@1（top-1 ∈ valid）、
recall@5/10（|top∩valid|/|valid|）、invalid_selection_rate（top-5 撞
must_not 的案例占比）、fallback_rate（空选择占比）、tier3_leak（恒期望
0）。全程确定性：真实 DynamicToolSurface.select，无 LLM、无时间戳。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

MIN_EVAL_CORPUS_SIZE = 60


@dataclass(frozen=True)
class RetrievalEvalCase:
    """一条人工金标的 query→tool 检索案例。"""

    case_id: str
    query: str
    kind: str                       # direct | near_duplicate | hard_negative | ambiguous
    expected_tools: Tuple[str, ...]
    valid_tools: Tuple[str, ...] = field(default=())
    must_not_select: Tuple[str, ...] = ()
    lang: str = "zh"

    def __post_init__(self) -> None:
        if not self.valid_tools:
            object.__setattr__(self, "valid_tools", self.expected_tools)

    def as_metrics_case(self) -> Any:
        return self


def _c(case_id: str, query: str, expected: Sequence[str], *, kind: str = "direct",
       valid: Sequence[str] = (), must_not: Sequence[str] = (),
       lang: str = "zh") -> RetrievalEvalCase:
    return RetrievalEvalCase(
        case_id=case_id, query=query, kind=kind,
        expected_tools=tuple(expected),
        valid_tools=tuple(valid) if valid else tuple(expected),
        must_not_select=tuple(must_not), lang=lang,
    )


def build_retrieval_eval_corpus() -> List[RetrievalEvalCase]:
    """确定性构建（列表顺序即稳定顺序；无随机、无时间戳）。"""
    cases: List[RetrievalEvalCase] = []
    add = cases.append

    # ── direct（30）──────────────────────────────────────────────
    add(_c("EV-D01", "给学校图层加500米缓冲区看看覆盖范围",
           ["buffer_analysis"], valid=["buffer_analysis", "multi_ring_buffer"]))
    add(_c("EV-D02", "查一下王府井周边1公里内的咖啡馆",
           ["query_local_poi"],
           valid=["query_local_poi", "query_local_osm", "search_poi_around"]))
    add(_c("EV-D03", "用三角网插值把监测点铺成连续表面", ["tin_interpolation"]))
    add(_c("EV-D04", "对这些站点做反距离加权插值", ["idw_interpolation"]))
    add(_c("EV-D05", "用克里金把土壤重金属铺成网格并给估计方差",
           ["kriging_interpolation", "block_kriging_surface",
            "variogram_model_selection"]))
    add(_c("EV-D06", "引入辅助协变量的协同克里金", ["cokriging_surface"]))
    add(_c("EV-D07", "共享单车订单有经纬度和时间，按时空密度一起分簇", ["st_dbscan"]))
    add(_c("EV-D08", "对比洪灾前后两期 NDVI 栅格找变化并给统计", ["detect_raster_change"]))
    add(_c("EV-D09", "分析哪些片区热点在增强哪些在减弱", ["emerging_hotspot_analysis"]))
    add(_c("EV-D10", "这段销售额序列哪个月发生了均值跳变", ["temporal_changepoint"]))
    add(_c("EV-D11", "看这几年绿地率的升降趋势并做显著性检验", ["temporal_trend"]))
    add(_c("EV-D12", "从 DEM 提取河网并做 Strahler 分级", ["stream_network"]))
    add(_c("EV-D13", "找出这个流域出口断面以上的全部汇水区", ["watershed_delineation"]))
    add(_c("EV-D14", "老城区的天空开阔度（SVF）分布", ["sky_view_factor_analysis"]))
    add(_c("EV-D15", "地铁站15分钟步行等时圈", ["service_area_simple"]))
    add(_c("EV-D16", "计算仓库到各门店的配送距离时间矩阵", ["distance_matrix_cn"]))
    add(_c("EV-D17", "把高德坐标系的数据批量转成 WGS84", ["transform_coordinates"]))
    add(_c("EV-D18", "在研究区范围内生成1公里见方的网格", ["fishnet_grid"]))
    add(_c("EV-D19", "把街道边界按所属区县合并", ["dissolve_layer"]))
    add(_c("EV-D20", "给每个行政区算区域内的平均海拔（栅格分区统计）", ["zonal_stats"]))
    add(_c("EV-D21", "统计每个街道办辖区内的餐饮POI数量", ["spatial_aggregate"]))
    add(_c("EV-D22", "挑一景云量最少的 Sentinel-2 影像", ["fetch_sentinel"]))
    add(_c("EV-D23", "拉取这片山区的30米高程数据", ["fetch_dem"]))
    add(_c("EV-D24", "给这份月度序列做季节分解", ["temporal_seasonal_decompose"]))
    add(_c("EV-D25", "导出一张带图例和指北针的出版级地图", ["export_thematic_map"]))
    add(_c("EV-D26", "把这份大 TIF 转成 COG 便于在线瓦片服务", ["convert_raster_to_cog"]))
    add(_c("EV-D27", "对这份数据做几何/拓扑/CRS 质量审计", ["audit_spatial_quality"]))
    add(_c("EV-D28", "把乡镇口径的人口总量按居住区面积权重切分到 finer 面", ["dasymetric_reallocation"]))
    add(_c("EV-D29", "检验土壤类型对作物产量差异的解释力 q 值", ["geodetector"]))
    add(_c("EV-D30", "Generate an isochrone of 15-minute walk from each metro station",
           ["service_area_simple"], lang="en"))

    # ── near_duplicate（6 对 = 12）───────────────────────────────
    add(_c("EV-N01a", "插值必须精确穿过原始观测点的三角网方案",
           ["tin_interpolation"], kind="near_duplicate", must_not=["trend_surface"]))
    add(_c("EV-N01b", "只要区域整体起伏大势，忽略局部细节",
           ["trend_surface"], kind="near_duplicate", must_not=["tin_interpolation"]))
    add(_c("EV-N02a", "订单带下单时间戳，时空一起聚类",
           ["st_dbscan"], kind="near_duplicate", must_not=["spatial_cluster"]))
    add(_c("EV-N02b", "只要空间分布的密度聚类，没有时间信息",
           ["spatial_cluster"], kind="near_duplicate", must_not=["st_dbscan"]))
    add(_c("EV-N03a", "两期多光谱影像逐像元变化幅度和方向角",
           ["detect_change_cva"], kind="near_duplicate", must_not=["detect_raster_change"]))
    add(_c("EV-N03b", "对比两期 NDVI 栅格输出变化图和变化统计",
           ["detect_raster_change"], kind="near_duplicate", must_not=["detect_change_cva"]))
    add(_c("EV-N04a", "找时空尺度上新增强的热点片区",
           ["emerging_hotspot_analysis"], kind="near_duplicate", must_not=["general_g"]))
    add(_c("EV-N04b", "全市单期口径的高值聚集显著性检验",
           ["general_g"], kind="near_duplicate", must_not=["emerging_hotspot_analysis"]))
    add(_c("EV-N05a", "定位序列里唯一的均值漂移发生点",
           ["temporal_changepoint"], kind="near_duplicate", must_not=["temporal_trend"]))
    add(_c("EV-N05b", "长期变化的斜率方向和显著性",
           ["temporal_trend"], kind="near_duplicate", must_not=["temporal_changepoint"]))
    add(_c("EV-N06a", "只调整缩放级别、俯仰和旋转角度",
           ["set_map_view"], kind="near_duplicate", must_not=["fly_to_location"]))
    add(_c("EV-N06b", "把相机平滑飞行到指定坐标",
           ["fly_to_location"], kind="near_duplicate", must_not=["set_map_view"]))

    # ── hard_negative（12）───────────────────────────────────────
    add(_c("EV-H01", "搜索人民广场周边2公里内的健身房",
           ["search_poi_around"], kind="hard_negative", must_not=["buffer_analysis"]))
    add(_c("EV-H02", "给门店图层生成5公里直线服务范围圈",
           ["buffer_analysis"], kind="hard_negative", must_not=["service_area_simple"]))
    add(_c("EV-H03", "沿路网骑行15分钟能覆盖哪些区域",
           ["service_area_simple"], kind="hard_negative", must_not=["buffer_analysis"]))
    add(_c("EV-H04", "对污染栅格按行政区统计平均值",
           ["zonal_stats"], kind="hard_negative", must_not=["spatial_aggregate"]))
    add(_c("EV-H05", "数一数每个区里有多少个公交站点",
           ["spatial_aggregate"], kind="hard_negative", must_not=["zonal_stats"]))
    add(_c("EV-H06", "从每个格网单元沿水流到出口累计多少米",
           ["flow_length_analysis"], kind="hard_negative", must_not=["dinf_flow_analysis"]))
    add(_c("EV-H07", "这批1000条地址批量转坐标",
           ["batch_geocode_cn"], kind="hard_negative", must_not=["geocode_cn"]))
    add(_c("EV-H08", "Geocode this single address",
           ["geocode_cn"], kind="hard_negative", must_not=["batch_geocode_cn"], lang="en"))
    add(_c("EV-H09", "上传后先摸底 bbox、数值分布和时间字段",
           ["webgis_source_profile"], kind="hard_negative", must_not=["describe_dataset"]))
    add(_c("EV-H10", "读数据集的 schema、几何类型和 SRS 元数据契约",
           ["describe_dataset"], kind="hard_negative", must_not=["webgis_source_profile"]))
    add(_c("EV-H11", "克里金建模前先在同经验变异函数上比选理论模型",
           ["variogram_model_selection"], kind="hard_negative",
           must_not=["block_kriging_surface"]))
    add(_c("EV-H12", "对影像做亮度阈值云掩膜咨询",
           ["cloud_qc_basic"], kind="hard_negative", must_not=["fetch_sentinel"]))

    # ── ambiguous（12；valid ≥2）────────────────────────────────
    add(_c("EV-A01", "把地图移到西湖看看",
           ["fly_to_location"], valid=["fly_to_location", "zoom_to_bbox"], kind="ambiguous"))
    add(_c("EV-A02", "分析这份数据的空间分布格局",
           ["spatial_cluster"],
           valid=["spatial_cluster", "standard_deviational_ellipse",
                  "central_feature", "geary_c"], kind="ambiguous"))
    add(_c("EV-A03", "这两地之间开车要多久多远",
           ["distance_matrix_cn"],
           valid=["distance_matrix_cn", "measure_distance"], kind="ambiguous"))
    add(_c("EV-A04", "给图层换个配色",
           ["update_layer_appearance"],
           valid=["update_layer_appearance", "apply_layer_style"], kind="ambiguous"))
    add(_c("EV-A05", "做因子对分布的解释力分析",
           ["geodetector"],
           valid=["geodetector", "geodetector_ecological", "geodetector_risk"],
           kind="ambiguous"))
    add(_c("EV-A06", "把观测点插值成连续面",
           ["idw_interpolation"],
           valid=["idw_interpolation", "tin_interpolation", "block_kriging_surface",
                  "trend_surface"], kind="ambiguous"))
    add(_c("EV-A07", "看看显著的高值区域都在哪",
           ["general_g"],
           valid=["general_g", "emerging_hotspot_analysis", "spatial_cluster"],
           kind="ambiguous"))
    add(_c("EV-A08", "把结果导出成图",
           ["export_thematic_map"],
           valid=["export_thematic_map", "generate_chart"], kind="ambiguous"))
    add(_c("EV-A09", "这份数据质量怎么样",
           ["audit_spatial_quality"],
           valid=["audit_spatial_quality", "webgis_source_profile", "describe_dataset"],
           kind="ambiguous"))
    add(_c("EV-A10", "两期数据对比一下有什么变化",
           ["detect_raster_change"],
           valid=["detect_raster_change", "detect_change_cva", "detect_ratio_change"],
           kind="ambiguous"))
    add(_c("EV-A11", "Zoom in a bit",
           ["set_map_view"], valid=["set_map_view", "zoom_to_bbox"],
           kind="ambiguous", lang="en"))
    add(_c("EV-A12", "这些点在空间上聚集吗",
           ["geary_c"],
           valid=["geary_c", "general_g", "spatial_cluster"], kind="ambiguous"))

    return cases


_CORPUS: List[RetrievalEvalCase] = []


def get_retrieval_eval_corpus() -> List[RetrievalEvalCase]:
    """进程级缓存（与 retrieval_corpus.get_retrieval_corpus 同门）。"""
    global _CORPUS
    if not _CORPUS:
        _CORPUS = build_retrieval_eval_corpus()
    return list(_CORPUS)


@dataclass(frozen=True)
class RetrievalEvalReport:
    """开环 query→tool 评测指标（确定性）。"""

    cases: int
    precision_at_1: float
    recall_at_5: float
    recall_at_10: float
    invalid_selection_rate: float
    fallback_rate: float
    tier3_leak: int
    by_kind: Dict[str, Dict[str, float]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cases": self.cases,
            "precision_at_1": self.precision_at_1,
            "recall_at_5": self.recall_at_5,
            "recall_at_10": self.recall_at_10,
            "invalid_selection_rate": self.invalid_selection_rate,
            "fallback_rate": self.fallback_rate,
            "tier3_leak": self.tier3_leak,
            "by_kind": {k: dict(v) for k, v in self.by_kind.items()},
        }


def retrieval_eval_report(
    registry: Any,
    cases: Sequence[RetrievalEvalCase],
    *,
    k_max: int = 30,
) -> RetrievalEvalReport:
    """对人工金标案例跑真实 surface 选择并统计开环指标。

    **开环口径**：只给 ``user_message``（不给 active_capabilities 提示
    —— 生产里该信号来自 planner 的意图归类；本评测刻意剥离，量纯检索）。
    """
    from app.services.chat.tool_surface_v3 import (
        DynamicToolSurface,
        ToolSelectionContext,
    )

    from app.services.chat.tool_surface_v3 import CORE_TOOL_NAMES

    core_names = set(CORE_TOOL_NAMES)
    surface = DynamicToolSurface(registry)
    p1_hits = 0
    r5_vals: List[float] = []
    r10_vals: List[float] = []
    invalid = 0
    must_not_total = 0
    fallback = 0
    tier3_leak = 0
    by_kind: Dict[str, List[Dict[str, float]]] = {}

    for case in cases:
        ctx = ToolSelectionContext(
            user_message=case.query,
            k_max=k_max,
        )
        sel = surface.select(ctx)
        names = list(sel.names)
        # 开环 precision 口径在**检索排序段**上：CORE 常驻管道工具
        # （intent/product/status/list）无条件前置、与 query 无关，若计入
        # top-1 则恒为 core —— 剔除后才是 query→tool 检索质量。
        ranked = [n for n in names if n not in core_names]
        if not ranked:
            fallback += 1
        valid = set(case.valid_tools)
        must_not = set(case.must_not_select)
        if ranked and ranked[0] in valid:
            p1_hits += 1
        for name in ranked[:5]:
            if name in must_not:
                invalid += 1
                break
        if must_not:
            must_not_total += 1
        for k, acc in ((5, r5_vals), (10, r10_vals)):
            top = set(ranked[:k])
            acc.append(len(top & valid) / len(valid) if valid else 1.0)
        for name in names:
            try:
                desc = registry.descriptor(name)
            except KeyError:
                continue
            if int(desc.tier) >= 3 or desc.effective_security_tier >= 3:
                tier3_leak += 1

        kind_stats = by_kind.setdefault(case.kind, [])
        kind_stats.append({
            "p1": 1.0 if ranked and ranked[0] in valid else 0.0,
            "invalid": 1.0 if any(n in must_not for n in ranked[:5]) else 0.0,
        })

    n = max(1, len(cases))
    kind_summary: Dict[str, Dict[str, float]] = {}
    for kind, stats in by_kind.items():
        m = max(1, len(stats))
        kind_summary[kind] = {
            "cases": float(len(stats)),
            "precision_at_1": round(sum(x["p1"] for x in stats) / m, 4),
            "invalid_selection_rate": round(
                sum(x["invalid"] for x in stats) / m, 4),
        }
    return RetrievalEvalReport(
        cases=len(cases),
        precision_at_1=round(p1_hits / n, 4),
        recall_at_5=round(sum(r5_vals) / max(1, len(r5_vals)), 4),
        recall_at_10=round(sum(r10_vals) / max(1, len(r10_vals)), 4),
        invalid_selection_rate=round(invalid / max(1, must_not_total), 4),
        fallback_rate=round(fallback / n, 4),
        tier3_leak=tier3_leak,
        by_kind=kind_summary,
    )


__all__ = [
    "RetrievalEvalCase",
    "RetrievalEvalReport",
    "MIN_EVAL_CORPUS_SIZE",
    "build_retrieval_eval_corpus",
    "get_retrieval_eval_corpus",
    "retrieval_eval_report",
]
