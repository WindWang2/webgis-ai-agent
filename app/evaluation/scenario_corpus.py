"""ScenarioCorpus —— 生成式 GIS 任务金标语料（V7 ADR-0130 D4-W5）。

V6 基线缺口：``retrieval_eval_corpus`` 是 **598 条硬编码 ``_c(...)`` 行**
（V6 PR 时 358 → master 增长）。按此增速到 2k-5k 场景的行式维护不可持续
（V6 follow-up 已预告）。V7 Goal 明确要求「可自动扩充的可演进结构」。

V7 结构（**域包 × 参数槽 × 确定性展开**，不是行）：

- **域包（pack）**：一个 GIS 任务族（密度/插值/聚合/网络/……），声明
  ``family`` + ``expected``（capability/algorithm id 白名单，构建期对
  registry 校验 —— registry 里不存在的 id = 语料缺陷，gate 直接红）+
  ``templates``（带 ``{slot}`` 的查询模板，zh/en 双语）。
- **参数槽（slots）**：区域 / 距离 / 字段 / 半径等填空词表（有界）；
  模板 × 槽组合确定性展开为场景（同序同 case_id —— 稳定可 diff）。
- **覆盖门**：展开总数 ≥ ``MIN_CORPUS_SIZE``（2000）且每个已注册
  capability 至少被一个 pack 声明（registry 长出新族而语料未跟 →
  ``coverage_gaps`` 非空，评测门红 —— 结构自动「要求」扩充）。
- **评测**：确定性抽样（stride，无随机）→ ``select_capabilities`` /
  hybrid 词法 → p@1 / invalid；**语料自身零 LLM 依赖**。

评测口径：语料是**生成结构**的投影，抽样评估用（全量展开仅构建期校验
结构，不进测试热路径 —— 预算纪律）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 结构门（V7：可演进结构的最小规模承诺）。
MIN_CORPUS_SIZE = 2000
#: 评测抽样规模（确定性 stride；测试热路径有界）。
EVAL_SAMPLE_SIZE = 200

#: 参数槽（有界填空词表；展开因子的一部分 —— 加槽值即扩语料，不加行）。
_SLOT_REGIONS: Tuple[str, ...] = (
    "成都市", "武侯区", "北京市海淀区", "研究区", "上海市浦东新区",
    "广州市天河区", "武汉市洪山区", "杭州市西湖区", "南京市鼓楼区",
    "西安市雁塔区", "重庆市渝中区", "成都市锦江区",
)
_SLOT_DISTANCES: Tuple[str, ...] = (
    "500米", "1公里", "2公里", "15分钟", "800米", "3公里",
)
_SLOT_FIELDS: Tuple[str, ...] = (
    "人口", "房价", "GDP", "气温", "绿地率", "海拔", "PM2.5", "门店数",
)


@dataclass(frozen=True)
class ScenarioPack:
    """一个任务族的生成器（expected 对 registry 白名单校验）。"""

    family: str
    expected: Tuple[str, ...]
    templates: Tuple[str, ...] = ()
    slots: Dict[str, Sequence[str]] = field(default_factory=dict)
    lang: str = "zh"

    def expand(self) -> List[Tuple[str, str]]:
        """模板 × 槽 → (query, case_key) 确定性展开。"""
        out: List[Tuple[str, str]] = []
        for t_idx, template in enumerate(self.templates):
            names = sorted(self.slots.keys())
            if not names:
                out.append((template, f"{self.family}:{t_idx}"))
                continue
            # 笛卡尔积（槽词表各有界；总因子 = Π len(slot)）
            combos: List[List[str]] = [[]]
            for name in names:
                combos = [c + [v] for c in combos for v in self.slots[name]]
            for c_idx, combo in enumerate(combos):
                query = template
                for name, value in zip(names, combo):
                    query = query.replace("{" + name + "}", value)
                out.append((query, f"{self.family}:{t_idx}:{c_idx}"))
        return out


#: 域包（family × capability 白名单锚定 registry —— id 均为真实注册项；
#: 扩语料 = 加模板/加槽值，不加行）。
def _packs() -> List[ScenarioPack]:
    regions = {"region": _SLOT_REGIONS}
    regions_dist = {
        "region": _SLOT_REGIONS,
        "dist": _SLOT_DISTANCES,
    }
    regions_field = {
        "region": _SLOT_REGIONS,
        "field": _SLOT_FIELDS,
    }
    return [
        ScenarioPack(
            family="proximity_buffer",
            expected=("geometry_buffer", "multi_ring_buffer", "proximity_buffer"),
            templates=(
                "给{region}的学校图层加{dist}缓冲区",
                "求{region}道路两侧{dist}范围内的覆盖面",
                "对{region}的医院点做{dist}邻域范围",
                "分析{region}设施周边{dist}服务覆盖",
            ),
            slots=regions_dist,
        ),
        ScenarioPack(
            family="density_heat",
            expected=("kde_density", "analytical_density", "density_surface"),
            templates=(
                "把{region}的POI铺成核密度图",
                "看看{region}设施分布的热点程度",
                "对{region}的案件点做密度表面",
                "生成{region}人口分布的连续密度面",
            ),
            slots=regions,
        ),
        ScenarioPack(
            family="interpolation",
            expected=("spatial_interpolation", "block_kriging", "cokriging",
                      "triangulation_interpolation", "areal_interpolation"),
            templates=(
                "把{region}的监测点{field}插值成连续表面",
                "用克里金把{region}的{field}铺成网格",
                "{region}的{field}缺站点，用面插值补",
                "对{region}采样点做空间插值并给方差",
            ),
            slots=regions_field,
        ),
        ScenarioPack(
            family="aggregation",
            expected=("admin_aggregation", "zonal_statistics", "temporal_aggregate"),
            templates=(
                "统计{region}每个街道的POI数量",
                "按行政区汇总{region}的{field}总量",
                "对{region}栅格做分区统计取均值",
                "把{region}的月度记录聚合成年度总量",
            ),
            slots=regions_field,
        ),
        ScenarioPack(
            family="network_access",
            expected=("service_area", "shortest_path", "transit_routing",
                      "accessibility", "gravity_accessibility"),
            templates=(
                "{region}地铁站{dist}步行等时圈",
                "给{region}的每个小区找最近医院",
                "计算{region}两点间最短路径",
                "评估{region}公交可达性与引力可达",
            ),
            slots=regions_dist,
        ),
        ScenarioPack(
            family="overlay_clip",
            expected=("geometry_clip", "geometry_dissolve"),
            templates=(
                "把{region}的图层按行政区裁剪",
                "两个图层求交后按街道合并",
                "对{region}边界做融合去内洞",
                "用{region}范围裁切大图层",
            ),
            slots=regions,
        ),
        ScenarioPack(
            family="terrain_raster",
            expected=("terrain_slope", "terrain_hillshade"),
            templates=(
                "从DEM提取{region}的坡度坡向",
                "给{region}做山体阴影渲染",
                "计算{region}地形的坡度分级",
            ),
            slots=regions,
        ),
        ScenarioPack(
            family="spatial_stats",
            expected=("global_morans_i", "local_morans_i", "hotspot",
                      "emerging_hotspot_analysis", "point_pattern_analysis"),
            templates=(
                "检验{region}{field}的空间自相关",
                "找出{region}的热点与冷点区域",
                "分析{region}事件点的空间格局",
                "对{region}做LISA局部聚集检验",
            ),
            slots=regions_field,
        ),
        ScenarioPack(
            family="clustering_ml",
            expected=("spatiotemporal_clustering",),
            templates=(
                "对{region}的轨迹点做时空密度聚类",
                "把{region}的POI按空间位置分簇",
                "对{region}共享单车订单做时空聚类",
            ),
            slots=regions,
        ),
        ScenarioPack(
            family="data_access",
            expected=("poi_query", "local_data_query", "admin_boundary_query"),
            templates=(
                "拉取{region}的兴趣点数据",
                "查{region}的行政区边界",
                "获取{region}范围内本地数据",
            ),
            slots=regions,
        ),
        ScenarioPack(
            family="cartography_export",
            expected=("map_export_publishing", "report_charting"),
            templates=(
                "导出一张带图例指北针的{region}专题图",
                "给{region}地图配统计图表",
                "把{region}成图批量导出发布",
            ),
            slots=regions,
        ),
        ScenarioPack(
            family="data_mgmt",
            expected=("raster_cog_conversion",),
            templates=(
                "把{region}的大TIF转成COG方便在线服务",
                "转换{region}影像格式为云端优化",
            ),
            slots=regions,
        ),
        ScenarioPack(
            family="quality_audit",
            expected=("dataset_profiling_quality",),
            templates=(
                "对{region}数据做几何拓扑质量画像",
                "检查这份数据的字段语义与几何有效性",
                "给{region}数据集出质量审计报告",
            ),
            slots=regions,
        ),
        ScenarioPack(
            family="comparison",
            expected=("change_detection", "temporal_change_point"),
            templates=(
                "对比{region}两期矢量要素找变化",
                "检测{region}的{field}序列突变点",
                "分析{region}两年间的用地变化",
            ),
            slots=regions_field,
        ),
    ]


# ── 场景模型 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScenarioCase:
    """一条生成场景（case_id 确定性 —— 稳定可 diff）。"""

    case_id: str
    query: str
    family: str
    expected: Tuple[str, ...]
    lang: str = "zh"


def build_scenario_corpus(
    *,
    max_cases: int = 5000,
    registry_validate: bool = True,
) -> List[ScenarioCase]:
    """域包 → 场景全集（确定性；同 pack 集合同序同 id）。

    ``registry_validate=True``（默认）时 expected 白名单对 capability +
    algorithm registry 校验 —— 未知 id 的 pack 仍展开（语料生成与
    registry 演进解耦），但 ``coverage_report`` 会暴露缺陷（gate 消费）。
    """
    cases: List[ScenarioCase] = []
    for pack in _packs():
        for query, key in pack.expand():
            if len(cases) >= max_cases:
                return cases
            cases.append(ScenarioCase(
                case_id=f"SC-{key}",
                query=query,
                family=pack.family,
                expected=pack.expected,
                lang=pack.lang,
            ))
    return cases


def coverage_report(cases: Optional[List[ScenarioCase]] = None) -> Dict[str, Any]:
    """结构覆盖报告（gate 消费）：

    - ``total``：展开总数（≥MIN_CORPUS_SIZE = 结构门）；
    - ``registry_missing``：expected 声明了但 registry 不存在的 id；
    - ``coverage_gaps``：registry 中 analysis 类 capability 完全没有被
      任何 pack 声明的 id（registry 长出新族而语料未跟 → 非空 = 门红）。
    """
    cases = cases if cases is not None else build_scenario_corpus()
    declared: Dict[str, str] = {}
    for c in cases:
        for cap in c.expected:
            declared.setdefault(cap, c.family)
    registry_missing = sorted(
        cap for cap in declared
        if not _registry_has(cap)
    )
    analysis_ids = _registry_analysis_ids()
    coverage_gaps = sorted(
        cap for cap in analysis_ids
        if cap not in declared
    )[:64]
    return {
        "total": len(cases),
        "min_required": MIN_CORPUS_SIZE,
        "size_ok": len(cases) >= MIN_CORPUS_SIZE,
        "registry_missing": registry_missing[:32],
        "coverage_gaps": coverage_gaps,
        "families": len({c.family for c in cases}),
    }


def _registry_has(capability_id: str) -> bool:
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        if get_capability_registry().has(capability_id):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        return get_algorithm_registry().get(capability_id) is not None
    except Exception:  # noqa: BLE001
        return False


def _registry_analysis_ids() -> List[str]:
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        caps = get_capability_registry()
        out = []
        for cid in caps.all_ids():
            d = caps.get(cid)
            if d is not None and str(getattr(d, "category", "")) not in (
                "data_access",
            ):
                out.append(str(cid))
        return out
    except Exception:  # noqa: BLE001 — registry 缺席 → 无覆盖要求
        return []


def sample_cases(
    cases: List[ScenarioCase],
    n: int = EVAL_SAMPLE_SIZE,
) -> List[ScenarioCase]:
    """确定性抽样（stride；无随机 —— 同语料同样本来历）。"""
    if len(cases) <= n:
        return list(cases)
    stride = len(cases) / float(n)
    return [cases[int(i * stride)] for i in range(n)]


def evaluate_scenarios(
    cases: List[ScenarioCase],
    *,
    index: Optional[Dict[str, Any]] = None,
    sample_n: int = EVAL_SAMPLE_SIZE,
) -> Dict[str, Any]:
    """抽样评测：``select_capabilities`` top-1 命中 expected 的比例（p@1）。

    - ``index`` 缺席 → 现建（进程内一次性）；
    - expected 是 id 白名单（capability:/algorithm:/裸 id 兼容 —— 裸 id
      对两种 kind 都试）；
    - 返回有界报告（p@1 / evaluated / misses 样例 ≤8 —— 诊断面）。
    """
    if index is None:
        from app.services.gis_harness.capability_descriptors import (
            build_capability_index,
        )

        index = build_capability_index()
    from app.services.gis_harness.capability_descriptors import (
        select_capabilities,
    )

    sampled = sample_cases(cases, sample_n)
    hits = 0
    misses: List[str] = []
    for case in sampled:
        results = select_capabilities(index, case.query, limit=3)
        expected_ids = set()
        for e in case.expected:
            expected_ids.add(e)
            expected_ids.add(f"capability:{e}")
            expected_ids.add(f"algorithm:{e}")
        top = results[0]["id"] if results else ""
        if top in expected_ids:
            hits += 1
        elif len(misses) < 8:
            misses.append(f"{case.case_id}:{case.query[:32]}→{top or 'none'}")
    evaluated = len(sampled)
    return {
        "evaluated": evaluated,
        "p_at_1": round(hits / evaluated, 4) if evaluated else 0.0,
        "misses_sample": misses,
    }


__all__ = [
    "MIN_CORPUS_SIZE",
    "EVAL_SAMPLE_SIZE",
    "ScenarioPack",
    "ScenarioCase",
    "build_scenario_corpus",
    "coverage_report",
    "sample_cases",
    "evaluate_scenarios",
]
