"""判选输入单一投影器（CM-01，G04-CARTOGRAPHY-COMPOSER D5）。

此前 ``TaskCartographyContext``（planner 侧）与 ``DesignTaskContext``
（tool_map_product 侧）由两个调用方各自投影，存在三处口径漂移：
geometry 坍缩两处实现、variable_kind 仅 planner 侧消费 profile、
statistic 一侧恒 ``count`` 一侧派生 density。本模块是**唯一**投影权威：
intent 事实 + profile（+overlay/pin 增量证据）→ :class:`SelectionFacts`，
两侧经 ``task_ctx_kwargs()`` / ``design_ctx_kwargs()`` 消费同一产物。

纪律：
- 纯函数、无 services 依赖（lib 层）；调用方负责把 harness intent 对象
  拆成标量事实传入；
- 几何坍缩与 ``app/services/gis_harness/recipes._geometry_category`` 在
  几何词表上等价（tests/cartography/test_selection_context_v1.py 锁定，
  不造第二坍缩事实源）；
- 产物表 id 闭合于 ``app.lib.gis.artifacts.ArtifactType``；
- 缺席事实 → 确定性缺省（polygon/continuous/count），不猜、不虚构。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

#: intent task 词表 → 组合选择类目（审定静态映射；自 planner 下移的单一
#: 事实源 —— 表外类目兜底 thematic_cartography，与 composition_selection
#: 的类目词表对齐）。
TASK_CATEGORY_BY_INTENT: Dict[str, Tuple[str, ...]] = {
    "distribution_overview": ("spatial_distribution",),
    "density_analysis": ("density",),
    # carto-design（评审 P2-5）：定量密度/集中度语义路由 density 类目
    #（density_analysis 为历史 key 保留）。
    "analytical_density": ("density",),
    "concentration_analysis": ("density", "hotspot"),
    "aggregation_overview": ("administrative_aggregation",),
    "hotspot_analysis": ("hotspot",),
    "clustering_analysis": ("clustering",),
    "comparison_analysis": ("comparison",),
    "change_detection": ("change_detection", "spatiotemporal_pattern"),
    "terrain_analysis": ("terrain",),
    "interpolation_analysis": ("interpolation",),
    "accessibility_analysis": ("accessibility_network",),
    "proximity_analysis": ("proximity",),
    "multi_criteria_analysis": ("multi_criteria",),
    "suitability_analysis": ("suitability",),
    "overlay_analysis": ("overlay",),
    "remote_sensing_extraction": ("remote_sensing_extraction",),
}

#: 几何类别 → 「数据在手可产出什么」的产物语义类型（缺省派生口径；
#: profile 显式声明优先）。
GEOMETRY_KIND_ARTIFACTS: Dict[str, Tuple[str, ...]] = {
    "point": ("point_feature_set", "admin_aggregate_table"),
    "line": ("line_feature_set", "admin_aggregate_table"),
    "polygon": ("polygon_feature_set", "admin_aggregate_table"),
    "raster": ("raster_surface",),
}

_POINT_GEOS = frozenset({"Point", "MultiPoint"})
_LINE_GEOS = frozenset({"LineString", "MultiLineString"})
_POLYGON_GEOS = frozenset({"Polygon", "MultiPolygon"})

#: dataKind → 判选 variable_kind（测量层级）单一映射。
_DATA_KIND_TO_VARIABLE: Dict[str, str] = {
    "sequential": "continuous",
    "diverging": "continuous",
    "categorical": "categorical",
    "qualitative": "categorical",
}

#: density 统计语义的 intent 任务族（statistic 派生单一口径）。
_DENSITY_TASKS = frozenset({"analytical_density", "concentration_analysis"})
_DENSITY_MEASURES = frozenset({"density", "rate", "ratio"})

_FALLBACK_CATEGORY: Tuple[str, ...] = ("thematic_cartography",)


def collapse_geometry_types(geometry_types: Optional[Sequence[str]]) -> str:
    """几何类型列表 → 主导类别 point/line/polygon/unknown（计数最大者）。

    与 harness ``recipes._geometry_category`` 同规则；此实现服务 lib 层
    判选投影（等价性由测试锁定，不引入第二坍缩语义）。
    """
    if not geometry_types:
        return "unknown"
    counts = {"point": 0, "line": 0, "polygon": 0}
    for gt in geometry_types:
        if gt in _POINT_GEOS:
            counts["point"] += 1
        elif gt in _LINE_GEOS:
            counts["line"] += 1
        elif gt in _POLYGON_GEOS:
            counts["polygon"] += 1
    active = [k for k, v in counts.items() if v > 0]
    if not active:
        return "unknown"
    return max(counts, key=lambda c: counts[c])


def geometry_kind_of(
    geometry_types: Sequence[str] = (),
    geometry_expectation: str = "",
) -> str:
    """判选几何类别：profile 几何事实优先，intent 期望次之，缺省 polygon。"""
    category = collapse_geometry_types(geometry_types)
    if category in ("point", "line", "polygon"):
        return category
    if category == "raster":
        return "raster"
    expectation = str(geometry_expectation or "").strip().lower()
    if expectation in ("point", "line", "polygon", "raster"):
        return expectation
    return "polygon"


def variable_kind_of(profile: Optional[Dict[str, Any]]) -> str:
    """profile dataKind（显式 > 字段类型粗判）→ 测量层级。

    粗判口径：数值字段在场 → continuous（专题值优先于名称/文本伴生字段
    —— name+count 型数据是分级设色的常态，不能因伴生文本降为
    categorical）；仅有文本/类别字段 → categorical；无字段 → continuous
    （与既有缺省一致，不虚构 categorical）。
    """
    prof = profile if isinstance(profile, dict) else {}
    data_kind = str(prof.get("dataKind") or "")
    if not data_kind:
        fields = prof.get("fields")
        field_types = [
            str((spec or {}).get("type") or "").lower()
            for spec in (fields.values() if isinstance(fields, dict) else [])
            if isinstance(spec, dict)
        ]
        has_numeric = any(t in ("number", "numeric", "int", "integer",
                                "float", "double") for t in field_types)
        has_text = any(t in ("text", "string", "category")
                       for t in field_types)
        data_kind = (
            "sequential" if has_numeric
            else ("categorical" if has_text else "sequential")
        )
    return _DATA_KIND_TO_VARIABLE.get(data_kind, "continuous")


def artifact_types_of(
    geometry_kind: str,
    explicit: Iterable[str] = (),
    *,
    admin_boundary_evidence: bool = False,
) -> Tuple[str, ...]:
    """产物语义类型：显式声明优先；否则几何派生（+面证据扩展边界产物）。"""
    if geometry_kind not in GEOMETRY_KIND_ARTIFACTS:
        raise ValueError(f"unknown geometry kind: {geometry_kind!r}")
    explicit_tuple = tuple(explicit)[:12]
    if explicit_tuple:
        return explicit_tuple
    derived = list(GEOMETRY_KIND_ARTIFACTS[geometry_kind])
    if admin_boundary_evidence and "admin_boundary_set" not in derived:
        derived.append("admin_boundary_set")
    return tuple(derived)


class SelectionFacts(BaseModel):
    """判选输入的单一投影产物（两侧调用面共用）。"""

    task_categories: Tuple[str, ...] = _FALLBACK_CATEGORY
    geometry_kind: str = "polygon"
    secondary_geometry_kinds: Tuple[str, ...] = ()
    variable_kind: str = "continuous"
    statistic: str = "count"
    scale_hint: str = ""
    artifact_types: Tuple[str, ...] = ()
    output_target: str = "interactive"
    point_count: int = 0
    point_count_basis: str = "unknown"
    temporal_periods: int = 0
    user_requested_models: Tuple[str, ...] = ()

    def task_ctx_kwargs(self) -> Dict[str, Any]:
        """composition_selection.TaskCartographyContext 构造参数。"""
        return {
            "task_categories": self.task_categories,
            "geometry_kind": self.geometry_kind,
            "variable_kind": self.variable_kind,
            "statistic": self.statistic,
            "scale_hint": self.scale_hint,
            "output_target": self.output_target,
            "artifact_types": self.artifact_types,
        }

    def design_ctx_kwargs(
        self,
        denominator: Any = None,
        distribution: Any = None,
    ) -> Dict[str, Any]:
        """design_candidates.DesignTaskContext 构造参数（denominator 默认
        空证据 —— 调用方有分母字段证据时显式传入）。"""
        kwargs = self.task_ctx_kwargs()
        kwargs.update({
            "secondary_geometry_kinds": self.secondary_geometry_kinds,
            "point_count": self.point_count,
            "point_count_basis": self.point_count_basis,
            "temporal_periods": self.temporal_periods,
            "user_requested_models": self.user_requested_models,
        })
        if denominator is not None:
            kwargs["denominator"] = denominator
        if distribution is not None:  # pragma: no cover - 显式扩展缝
            kwargs["distribution"] = distribution
        return kwargs


def project_selection_facts(
    *,
    intent_task: str = "",
    profile: Optional[Dict[str, Any]] = None,
    geometry_expectation: str = "",
    geometry_types: Optional[Sequence[str]] = None,
    measure: str = "",
    scale_hint: str = "",
    overlay_geometry_kinds: Sequence[str] = (),
    user_requested_models: Sequence[str] = (),
    temporal_periods: int = 0,
    output_target: str = "interactive",
) -> SelectionFacts:
    """intent 事实 + profile → SelectionFacts（唯一投影入口）。

    ``geometry_types`` 缺省取 ``profile["geometryTypes"]``。分母/分布等
    会话级证据由调用方组装后经 ``design_ctx_kwargs`` 注入 —— 本函数只做
    无副作用的结构化投影。
    """
    prof = profile if isinstance(profile, dict) else {}
    geo_types: Sequence[str] = (
        geometry_types if geometry_types is not None
        else (prof.get("geometryTypes") or [])
    )
    geometry_kind = geometry_kind_of(geo_types, geometry_expectation)
    task = str(intent_task or "")
    statistic = (
        "density" if (
            str(measure or "") in _DENSITY_MEASURES or task in _DENSITY_TASKS
        ) else "count"
    )
    feature_count = prof.get("featureCount")
    point_count = int(feature_count) if (
        geometry_kind == "point"
        and isinstance(feature_count, int) and not isinstance(feature_count, bool)
    ) else 0
    return SelectionFacts(
        task_categories=TASK_CATEGORY_BY_INTENT.get(task, _FALLBACK_CATEGORY),
        geometry_kind=geometry_kind,
        secondary_geometry_kinds=tuple(dict.fromkeys(
            g for g in overlay_geometry_kinds if g and g != geometry_kind
        )),
        variable_kind=variable_kind_of(prof),
        statistic=statistic,
        scale_hint=str(scale_hint or ""),
        artifact_types=artifact_types_of(
            geometry_kind,
            explicit=tuple(prof.get("artifactTypes") or ()),
            admin_boundary_evidence="polygon" in {
                g for g in overlay_geometry_kinds if g
            },
        ),
        output_target=str(output_target or "interactive"),
        point_count=point_count,
        point_count_basis=(
            "profile" if (
                geometry_kind == "point"
                and isinstance(feature_count, int)
                and not isinstance(feature_count, bool)
            ) else "unknown"
        ),
        temporal_periods=int(temporal_periods or 0),
        user_requested_models=tuple(dict.fromkeys(
            m for m in user_requested_models if m
        )),
    )


__all__ = [
    "GEOMETRY_KIND_ARTIFACTS",
    "TASK_CATEGORY_BY_INTENT",
    "SelectionFacts",
    "artifact_types_of",
    "collapse_geometry_types",
    "geometry_kind_of",
    "project_selection_facts",
    "variable_kind_of",
]
