"""MapProductPlanner —— Intent → Recipe → Capability → Algorithm → Plan。

流程（§4 总体目标的中间层）：

    MapRequestIntent
          ↓ recipe selection（确定性）
    Candidate Recipe
          ↓ CapabilityRegistry / AlgorithmResolver（能力→算法→工具裁决）
    Capability Requirements + Algorithm Selections
          ↓ fetch data（agent 经 ToolDispatchService 执行能力面）
    Spatial Profile（ref descriptor 派生，零全量扫描）
          ↓ Recipe Eligibility + Algorithm applicability 复检（§17）
    Final Cartography Plan（含 fallback 决策记录）

plan 是**期望产品描述**，不是工具调用脚本。planner 是纯领域编排器：
具体工具名归 AlgorithmRegistry（经 resolver 裁决）、图层类型归
MapModelRegistry、模板选择归 TemplateSelector —— 本文件不再持有这些
知识的硬编码表。plan_id 由 (query, recipe_id) 决定性派生。
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.components import (
    CartographyComponent,
    build_default_components,
)
from app.services.gis_harness.intent import MapRequestIntent
from app.services.gis_harness.product_templates import (
    MapProductTemplate,
    get_product_template_registry,
)
from app.services.gis_harness.recipes import (
    CartographyRecipe,
    ChainResolution,
    EligibilityReport,
    FallbackDecision,
    default_charts_for_task,
    default_statistics_for_task,
    get_recipe_registry,
    resolve_fallback_chain,
)
from app.lib.gis.runtime_manifest import get_runtime_manifest
from app.services.gis_harness.template_catalog import get_template_catalog
from app.services.gis_harness.template_selector import TemplateSelector


# ── 兼容视图（audit #825 的锁定对象迁移到 registry；非第二事实源）──────
#
# CAPABILITY_TOOLS 现在是 AlgorithmRegistry 的**派生视图**：capability →
# 有序算法工具候选。新增算法只需注册 AlgorithmDescriptor，本模块零改动。
# tests/unit/test_capability_registry_parity.py 锁定派生视图与真实
# ToolRegistry / recipe 声明的 parity。
def capability_tool_map() -> Dict[str, List[str]]:
    """capability → 有序工具候选。

    v2(audit R4)：读 Compiled Runtime Manifest 的 O(1) 预排序视图 ——
    AlgorithmRegistry.capability_tool_map() 此前每次调用全量重建，
    plan_orchestrator 每步都调。manifest 编译时已按算法 priority 排序，
    内容与 registry 派生视图一致（同一来源）。
    """
    from app.lib.gis.runtime_manifest import get_runtime_manifest
    return dict(get_runtime_manifest().capability_to_tools)


def resolve_tool_for_capability(
    capability: str,
    available_tools: Optional[Any] = None,
) -> Optional[str]:
    """把能力 id 解析为当前注册表里真实存在的工具名。

    兼容 shim：委托 AlgorithmResolver 裁决（capability → algorithm →
    tool 的唯一裁决点）；available_tools 为 None 时返回首选候选。
    """
    from app.lib.gis.algorithm_resolver import get_algorithm_resolver
    resolution = get_algorithm_resolver().resolve(
        capability, available_tools=available_tools)
    return resolution.tool if resolution.status == "resolved" else None


# Kriging vertical slice: 显式插值语义的关键词投影（纯函数、确定性）。
# 命中时 (a) 计划 spatial_interpolation 能力（关键词门控 —— 普通
# raster_distribution 查询如「查看 DEM」不会过度计划插值），(b) 向
# resolver 传 algorithm_hint —— 用户点名克里金时算法不被静默换成默认
# IDW（hint 只在算法通过全部硬门后生效）。
_INTERPOLATION_QUERY_RE = None  # lazily compiled below


def _interpolation_query_signals(query: str) -> tuple[bool, str]:
    """Return (plan_interpolation_capability, algorithm_hint) for a query."""
    import re as _re

    global _INTERPOLATION_QUERY_RE
    if _INTERPOLATION_QUERY_RE is None:
        _INTERPOLATION_QUERY_RE = _re.compile(
            r"(克里金|kriging|插值|interpolat)", _re.I
        )
    q = query or ""
    if not _INTERPOLATION_QUERY_RE.search(q):
        return (False, "")
    hint = "interpolation.kriging" if _re.search(
        r"(克里金|kriging)", q, _re.I
    ) else ""
    return (True, hint)


# 模块级兼容名（DEPRECATED：新代码用 capability_tool_map()）。
# v2(review)：不再 import 时快照 —— 模块级调用会在 manifest 编译（持
# threading.Lock）经 import 链重入 get_runtime_manifest() 时死锁（非重入
# 锁 + 缓存未置的再编译）。PEP 562 惰性属性保持 `from planner import
# CAPABILITY_TOOLS` 兼容，且首次访问才取当前视图（顺带消除快照过期）。
def __getattr__(name: str):
    if name == "CAPABILITY_TOOLS":
        return capability_tool_map()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── V4（ADR-0104 #4/#6）：插值解析的事实驱动投影 ─────────────────────
#
# 审计 gap #5：插值候选散在 5 个 capability（spatial_interpolation /
# triangulation / regression_kriging / trend_surface / model_selection），
# 但只有 spatial_interpolation 经文本关键词门可达 —— 点数/度量字段/投影
# CRS/不确定性需求的**事实**无法把请求从 IDW 移向克里金族。
#
# 本投影是纯函数（同输入必同输出），只做两件事，绝不绕硬门：
#   a) 产出 algorithm_hint —— resolver 的 hinted-promotion 语义（既有）
#      保证被点名的算法仍须通过全部硬门；文本 hint 与事实冲突时**事实
#      胜**（drop hint），文本 hint 永不把不合适的方法顶过事实判断；
#   b) 产出可选 capability 追加（regression_kriging / model_selection）
#      —— 仅当 spatial_interpolation 已被计划（文本门不放大）且事实支持。
# 证据缺席（profile None / 事实键不在）→ 空投影 = 旧行为逐位保留。
_UNCERTAINTY_QUERY_RE = None  # lazily compiled below


def _uncertainty_demand(query: str) -> bool:
    """查询文本是否携带不确定性需求（方差/误差/置信…；纯词面、确定性）。"""
    import re as _re

    global _UNCERTAINTY_QUERY_RE
    if _UNCERTAINTY_QUERY_RE is None:
        _UNCERTAINTY_QUERY_RE = _re.compile(
            r"(不确定|方差|误差|置信|uncertaint|variance|confidence|error)", _re.I
        )
    return bool(_UNCERTAINTY_QUERY_RE.search(query or ""))


# ── V4（ADR-0151 #P5）：事实优先信号通用化 ────────────────────────────
#
# _interpolation_fact_signals 的「结构化数据事实覆盖文本 hint」模式推广到
# 通用裁决面：同一输入必同输出的纯投影，产出
#   evidence    有界数据事实摘要（进 plan）；
#   conflicts   事实与意图/hint 的冲突披露（走 methodology_warnings，
#               绝不改写 intent —— _HINT_PROTECTED_TASKS 的护栏语义只读
#               消费，降级一律显式披露，不静默）。
# 事实缺席（ctx 全 unknown）→ 空投影 = 旧行为逐位保留。
def fact_signals(
    ctx: Any,
    *,
    intent: Optional[MapRequestIntent] = None,
) -> Dict[str, Any]:
    """EligibilityContext 事实 → 通用信号投影（确定性、有界）。"""
    out: Dict[str, Any] = {
        "evidence": {},
        "conflicts": [],
    }
    if ctx is None:
        return out
    ev: Dict[str, Any] = {}
    conflicts: List[Dict[str, Any]] = []

    if ctx.n is not None:
        ev["featureCount"] = ctx.n
        ev["sample_tier"] = ctx.sample_tier()
    if ctx.geometry and ctx.geometry != "unknown":
        ev["geometry"] = ctx.geometry
    if ctx.spatial is not None and (ctx.spatial.crs or ctx.spatial.crs_class):
        ev["crs"] = ctx.spatial.crs
        ev["crsClass"] = ctx.spatial.crs_class
    if ctx.distribution is not None:
        shape = ctx.distribution_shape()
        if shape != "unknown":
            ev["distribution_shape"] = shape

    if intent is not None:
        # 1) 几何期望 vs 真实几何：raster 期望遇矢量事实是最强冲突信号
        #    （#781 同源语义，这里补事实侧披露）。
        expectation = str(getattr(intent, "geometry_expectation", "") or "")
        if expectation == "raster" and ev.get("geometry") not in (
                None, "raster", "unknown"):
            conflicts.append({
                "code": "FACT_GEOMETRY_MISMATCH",
                "hint": f"geometry_expectation={expectation}",
                "fact": f"geometry={ev['geometry']}",
                "disclosure": (
                    f"请求按栅格数据理解，但实际数据是 {ev['geometry']} 几何："
                    "栅格表达不可用，已按矢量事实裁决制图方案。"
                ),
            })
        # 2) 聚合表达 vs CRS 事实：地理坐标系下的格网/聚合精度不可信。
        carto_intents = {
            str(c) for c in (getattr(intent, "cartography_intents", []) or [])
        }
        aggregation_family = bool(
            carto_intents & {"aggregate_grid", "proportional_symbol"}
        ) or str(getattr(intent, "task", "")) in (
            "analytical_density", "administrative_statistic",
        )
        if aggregation_family and ev.get("crsClass") == "geographic":
            conflicts.append({
                "code": "FACT_PROJECTION_REQUIRED",
                "hint": "aggregation/analytical expression",
                "fact": "crsClass=geographic",
                "disclosure": (
                    "数据为地理坐标系（经纬度）：面积/密度类聚合在投影归一前"
                    "只是近似，结论以投影后重算为准。"
                ),
            })
        # 3) 零膨胀分布 vs 密度/分级语义：零值主导的度量直方图是制图谎报。
        if ev.get("distribution_shape") == "zero_inflated" and (
            "aggregate_grid" in carto_intents
            or str(getattr(intent, "task", "")) == "analytical_density"
        ):
            conflicts.append({
                "code": "FACT_ZERO_INFLATED_DISTRIBUTION",
                "hint": "density/aggregation expression",
                "fact": "distribution_shape=zero_inflated",
                "disclosure": (
                    "度量字段零值占比过高：密度/分级表达会被零值主导，"
                    "建议先过滤无数据单元或改用计数表达并披露。"
                ),
            })

    # 只读消费 intent 护栏词表（intent.py 归 01 线，本线零改动）：
    # 受保护任务不可被 hint 降级 —— 事实冲突的披露面上显式标注该约束，
    # 消费方（LLM/完成管线）不得据此改写任务路由。
    try:
        from app.services.gis_harness.intent import (
            _HINT_OVERRIDABLE,
            _HINT_PROTECTED_TASKS,
        )

        task = str(getattr(intent, "task", "") or "") if intent is not None else ""
        if task:
            ev["hint_overridable_keys"] = sorted(_HINT_OVERRIDABLE)[:8]
            if task in _HINT_PROTECTED_TASKS:
                ev["protected_task"] = task
                for c in conflicts:
                    c["protected_task"] = task
    except Exception:  # noqa: BLE001 — 词表缺席不影响投影主路
        pass

    out["evidence"] = ev
    out["conflicts"] = conflicts[:4]
    return out


def _profile_int(profile: Optional[Dict[str, Any]], key: str) -> Optional[int]:
    v = (profile or {}).get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


def _interpolation_fact_signals(
    profile: Optional[Dict[str, Any]],
    *,
    query: str = "",
) -> Dict[str, Any]:
    """resolver profile 事实 → 插值族选择信号（纯函数、确定性、有界）。

    返回 {
      "ok_family_suitable": bool,     # 克里金族与已知事实一致（无已知冲突）
      "fact_hint": str,               # "" | "interpolation.kriging"
      "extra_capabilities": [str],    # 事实支持的可选 capability（≤2）
      "evidence": {...},              # 有界决策证据（进 plan）
    }

    判定只用**在场**事实；缺席事实（键不在）一律按 unknown 放行 ——
    resolver 的硬门（min_features / crs_class / scientific_preconditions）
    仍是最终裁决，本投影只决定 hint 与可选能力，不虚构证据也不绕门。
    """
    out: Dict[str, Any] = {
        "ok_family_suitable": False,
        "fact_hint": "",
        "extra_capabilities": [],
        "evidence": {},
    }
    p = profile if isinstance(profile, dict) else None
    if not p:
        return out
    n = _profile_int(p, "featureCount")
    ev: Dict[str, Any] = {}
    suitable = True

    # 1) 点数下限（与 registry min_features=8 同值镜像；未知放行——硬门裁决）
    if n is not None:
        ev["featureCount"] = n
        if n < 8:
            suitable = False
            ev["reject"] = "feature_count_below_kriging_floor"

    # 2) 数值度量在场（numericFields 权威空清单 = 证据证明无度量字段）
    numeric = p.get("numericFields")
    if isinstance(numeric, list):
        ev["numeric_field_count"] = len(numeric)
        if not numeric:
            suitable = False
            ev["reject"] = ev.get("reject") or "no_numeric_measure"
    # 2b) 方差（证据在场且为 0 = 常量场 → 克里金无意义）
    variance = p.get("valueVariance")
    if isinstance(variance, (int, float)) and not isinstance(variance, bool):
        if float(variance) <= 0.0:
            suitable = False
            ev["reject"] = ev.get("reject") or "zero_value_variance"

    # 2c) V5 W4：经度约定/AM 语义证据（profile 证据进 planner）——
    # e360 或跨 AM 数据在度量投影前需要归一/拆分，先记事实供
    # fact_hint 与硬门之外的 remediation 提示；absent 缺席不放言。
    lon_conv = p.get("longitudeConvention")
    if lon_conv in ("pm180", "e360"):
        ev["longitudeConvention"] = lon_conv
    crosses = p.get("crossesAntimeridian")
    if isinstance(crosses, bool):
        ev["crossesAntimeridian"] = crosses
        if crosses or lon_conv == "e360":
            ev["longitudeNormalizationRequired"] = True

    # 3) CRS 类（geographic = PROJECTED_REQUIRED 硬门必拒 → 事实先行不 hint；
    #    projected/local/unknown 放行，硬门兜底）
    crs_class = p.get("crsClass")
    if crs_class not in ("geographic", "projected", "projected_local_metric"):
        crs = p.get("crs")
        if isinstance(crs, str) and crs:
            try:
                from app.lib.gis.crs_safety import classify_crs

                crs_class = classify_crs(crs)
            except Exception:  # noqa: BLE001 — 分类失败按 unknown
                crs_class = None
        else:
            crs_class = None
    if crs_class:
        ev["crsClass"] = crs_class
        if crs_class == "geographic":
            suitable = False
            ev["reject"] = ev.get("reject") or "geographic_crs_needs_projection"

    # 4) 重复坐标（深扫证据：去重后 n < 8 → 运行时 MIN_SAMPLES 结构化拒绝，
    #    审计 gap #9 —— 事实先行把它挡在 hint 层）
    uniques = _profile_int(p, "uniqueCoordinateCount")
    if uniques is not None:
        ev["uniqueCoordinateCount"] = uniques
        if uniques < 8:
            suitable = False
            ev["reject"] = ev.get("reject") or "post_dedup_below_floor"

    # 5) 趋势/各向异性证据（上游无统计 —— 如实缺席，绝不虚构 UK/RK 升级）。
    out["ok_family_suitable"] = suitable
    out["evidence"] = ev
    if suitable:
        out["fact_hint"] = "interpolation.kriging"

    # 6) 可选 capability 追加（仅 spatial_interpolation 已计划时由调用方
    #    采纳；协变量事实 → regression_kriging；不确定性需求 → 模型比较）。
    extras: List[str] = []
    if suitable and isinstance(numeric, list) and len(numeric) >= 3 \
            and crs_class in ("projected", "projected_local_metric"):
        # value + ≥2 协变量（RK 参数契约要求 ≥2 协变量字段）。
        extras.append("regression_kriging")
        ev["covariate_candidate"] = True
    if n is not None and n >= 8 and _uncertainty_demand(query):
        extras.append("interpolation_model_selection")
        ev["uncertainty_demand"] = True
    out["extra_capabilities"] = extras
    return out


def layer_type_for_cartography(cartography: str, default: str = "circle") -> str:
    """制图模型 → MapLibre 图层类型。MapModelRegistry 是唯一权威。"""
    from app.lib.cartography.model_library import get_map_model_registry
    model = get_map_model_registry().resolve(cartography)
    return model.maplibre_layer_type if model else default


def _geometry_aware_layer_type(cartography: str, planned_type: str, profile_geom: str) -> str:
    """Resolve a geometry-polymorphic cartography's layer type from real data.

    多态映射收编进 MapModel.geometry_layer_types（audit #832）；
    非多态模型保持计划类型。
    """
    from app.lib.cartography.model_library import get_map_model_registry
    model = get_map_model_registry().resolve(cartography)
    table = model.geometry_layer_types if model else {}
    if not table:
        return planned_type
    return table.get(profile_geom) or planned_type


class DataRequirement(BaseModel):
    capability: str
    purpose: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)
    # v3(Phase E)：failed = 执行尝试过但未产出 artifact（可重试）。
    status: Literal["pending", "available", "unavailable", "failed"] = "pending"
    bound_ref: str = ""
    resolved_tool: str = ""
    resolved_algorithm: str = ""
    # v3(Phase D)：依赖边（capability id 列表，registry artifact 类型推断）。
    # additive —— 旧持久计划无此字段，plan_graph 读取侧重放推断。
    depends_on: List[str] = Field(default_factory=list)
    optional: bool = False


class AnalysisStep(BaseModel):
    capability: str
    purpose: str = ""
    status: Literal["pending", "done", "skipped", "unavailable", "failed"] = "pending"
    bound_ref: str = ""
    resolved_tool: str = ""
    resolved_algorithm: str = ""
    depends_on: List[str] = Field(default_factory=list)
    optional: bool = False


class AlgorithmSelectionRecord(BaseModel):
    """一次 capability → algorithm → tool 裁决的有界证据（§27）。"""

    capability: str
    status: Literal["resolved", "unavailable"]
    algorithm: str = ""
    tool: str = ""
    reason: str = ""
    rejected: List[str] = []
    fallback_trail: List[Dict[str, Any]] = []
    fallback_candidates: List[str] = []
    # V4（ADR-0104 #5，additive）：plan-time backend 证据 —— 选中算法的
    # 实现变体 + 资源分层（resolver 的 select_backend 纯函数投影转录）。
    backend_variant: str = ""
    backend: str = ""
    scale_tier: str = ""
    runtime_strategy: str = ""


class PlannedLayer(BaseModel):
    role: Literal["primary", "secondary", "reference"] = "secondary"
    layer_type: str = "circle"
    cartography: str = "point_overlay"
    source_capability: str = ""
    layer_id: str = ""
    bound_ref: str = ""
    enabled: bool = True
    note: str = ""


class MapProductPlan(BaseModel):
    """GIS/制图执行计划（typed / deterministic / replayable）。"""
    plan_id: str
    query: str
    intent: MapRequestIntent
    recipe_id: str
    template_id: str = ""
    data_requirements: List[DataRequirement] = []
    analysis_steps: List[AnalysisStep] = []
    map_layers: List[PlannedLayer] = []
    components: List[CartographyComponent] = []
    statistics: List[str] = []
    charts: List[str] = []
    fallbacks: List[FallbackDecision] = []
    validation: List[str] = []
    exports: List[str] = []
    outputs: List[str] = []
    status: Literal["draft", "finalized"] = "draft"
    completeness: Dict[str, Any] = Field(default_factory=dict)
    eligibility: Dict[str, Any] = Field(default_factory=dict)
    # ── registry 编排证据（有界转录，§27）─────────────────────────────
    algorithm_selections: List[AlgorithmSelectionRecord] = []
    template_selection: Dict[str, Any] = Field(default_factory=dict)
    map_model_selection: List[Dict[str, Any]] = Field(default_factory=list)
    # v2(Phase 4, #1084)：计划编制时的 registry 内容指纹 —— 恢复/续跑时与
    # 当前 manifest 比对，不一致 → STALE_PLAN（旧计划不得静默套用新
    # registry 语义）。空 = 历史计划（不判 stale）。
    manifest_fingerprint: str = ""
    # Semantic GIS（方法论诚实）：规划期确定的方法论边界披露 —— e.g.
    # 「缺分母不能谈公平性」。模式投影（pattern projection）的 required
    # roles 缺席时在此留档；产品仍可产出（数量/密度可评），但结论边界
    # 必须随 plan 证据下行，绝不允许把数量分布伪装成公平性结论。
    methodology_warnings: List[Dict[str, Any]] = Field(default_factory=list)
    # Workflow V2（Goal C / ADR-0101）：workflow 契约评估摘要（有界 dict，
    # schema_version/roles/obligations/method_blockers/data_blockers）。
    # None = 纯 V1 recipe（无 workflow 画像），行为与历史一致。
    workflow_contract: Optional[Dict[str, Any]] = None
    # V4（ADR-0104 #6）：finalize 阶段的插值事实投影（有界 dict：
    # ok_family_suitable/fact_hint/extra_capabilities/evidence/text_hint/
    # effective_hint）。空 = 未计划插值或 profile 缺席（行为与历史一致）。
    algorithm_fact_signals: Dict[str, Any] = Field(default_factory=dict)
    # V4（ADR-0151 #P5）：通用数据事实信号摘要（sample_tier/geometry/crs/
    # distribution_shape）。空 = profile 缺席（行为与历史一致）。
    data_fact_signals: Dict[str, Any] = Field(default_factory=dict)


def _plan_id(query: str, recipe_id: str) -> str:
    digest = hashlib.sha1(
        f"{query}|{recipe_id}".encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:12]
    return f"plan-{digest}"


class _CompositionRejectedError(Exception):
    """组合校验失败（携带违规明细，供兜底路径记录 evidence）。"""

    def __init__(self, message: str, *, violations: Optional[List[Dict[str, Any]]] = None) -> None:
        super().__init__(message)
        self.violations = violations or []


# 计划图层 cartography → 资格规则元素（同一制图能力的多态命名对齐；
# 与 MapModel 的 geometry_layer_types 同思路：资格元素名与表达名分列时
# 在此显式对齐，不散落 if/else）。
_ELEMENT_ALIASES: Dict[str, str] = {
    "density_overview": "visual_heatmap",
    "native_heatmap": "visual_heatmap",
}


class MapProductPlanner:
    """确定性产品规划器（纯函数式，无 LLM 依赖、无 I/O）。"""

    def __init__(self) -> None:
        self.recipes = get_recipe_registry()
        self.templates = get_product_template_registry()
        self.catalog = get_template_catalog()
        self.selector = TemplateSelector(catalog=self.catalog)
        # v2(audit R4)：同一会话里 webgis_map_intent 与 webgis_map_product
        # 对同一 query 各解析一次（加上 finalize_with_profile 共 2-3 次）。
        # plan_from_intent 是纯函数 —— 以 (query, recipe, template,
        # available_tools, manifest 指纹) 为键 memo；registry 内容变化
        # （manifest 指纹变）自动失效。有界 64。
        #
        # v3(audit A2/A3)：planner 实例此前在每个 harness tool call 内新建
        # （tools.py / plan_orchestrator.py），memo 只活在一次调用里 ——
        # intent→product 链跨调用零复用。共享 PlannerRuntime 后 memo 真正
        # 跨调用存活，eviction 路径随之可达：plain dict 的
        # ``popitem(last=False)`` 是 TypeError（v2 遗留 bug，被"每调用新
        # 实例"掩盖），必须 OrderedDict。memo 读写持锁（进程内多线程
        # dispatch 并发规划安全）；命中与存入均深拷贝，调用方可变返回值
        # 不污染 memo 基底。
        self._plan_memo: OrderedDict = OrderedDict()
        self._plan_memo_max = 64
        self._plan_memo_lock = threading.RLock()

    def attached_to_current_registries(self) -> bool:
        """共享 runtime 的 registry 身份守卫（v3 Phase C）。

        ``reset_recipe_registry`` / 模板注册测试会替换 registry 单例——
        共享 planner 持有旧引用时 memo 与裁决会漂移。身份不同即重建。
        """
        return (
            self.recipes is get_recipe_registry()
            and self.templates is get_product_template_registry()
            and self.catalog is get_template_catalog()
        )

    def reset_memo(self) -> None:
        """测试隔离：清空 memo（不重建 planner）。"""
        with self._plan_memo_lock:
            self._plan_memo.clear()

    # ── registry 裁决辅助 ─────────────────────────────────────────────
    def _resolve_capabilities(
        self,
        capabilities: List[str],
        intent: MapRequestIntent,
        *,
        available_tools: Optional[Any] = None,
        profile: Optional[Dict[str, Any]] = None,
        optional_capabilities: Optional[set] = None,
        algorithm_hint: str = "",
    ) -> tuple[List[DataRequirement], List[AnalysisStep], List[AlgorithmSelectionRecord]]:
        """capability → DataRequirement/AnalysisStep + 裁决证据。"""
        from app.lib.gis.algorithm_resolver import get_algorithm_resolver
        from app.lib.gis.capability_registry import get_capability_registry
        from app.services.gis_harness.plan_graph import infer_dependency_edges
        caps = get_capability_registry()
        resolver = get_algorithm_resolver()
        # v3(Phase D)：registry artifact 类型推断的依赖边（A.output ∩ B.input
        # ⇒ A→B）随行持久化 —— 扁平行即携带依赖序，plan_graph 是其纯投影。
        edges = infer_dependency_edges(capabilities)
        optional_set = optional_capabilities or set()
        requirements: List[DataRequirement] = []
        steps: List[AnalysisStep] = []
        selections: List[AlgorithmSelectionRecord] = []
        subject = intent.subject.category or "主体"
        for cap in capabilities:
            purpose = caps.purpose_for(cap, subject)
            resolution_result = resolver.resolve(
                cap, profile=profile, available_tools=available_tools,
                algorithm_hint=algorithm_hint)
            record = AlgorithmSelectionRecord(
                capability=cap,
                status=resolution_result.status,
                algorithm=resolution_result.algorithm,
                tool=resolution_result.tool,
                reason=resolution_result.reason,
                rejected=list(resolution_result.rejected),
                fallback_trail=[f.model_dump() for f in resolution_result.fallback_trail],
                fallback_candidates=list(resolution_result.fallback_candidates),
                backend_variant=resolution_result.backend_variant,
                backend=resolution_result.backend,
                scale_tier=resolution_result.scale_tier,
                runtime_strategy=resolution_result.runtime_strategy,
            )
            selections.append(record)
            # audit #825: 调用方传入注册表可见工具时，解析不到真实工具的
            # 能力标记 unavailable（诚实报告）；视图未知（None）保持 pending。
            unavailable = (
                available_tools is not None and record.status != "resolved"
            )
            status = "unavailable" if unavailable else "pending"  # type: ignore[assignment]
            deps = edges.get(cap, [])
            optional = cap in optional_set
            requirements.append(DataRequirement(
                capability=cap, purpose=purpose, status=status,
                resolved_tool=record.tool if record.status == "resolved" else "",
                resolved_algorithm=record.algorithm if record.status == "resolved" else "",
                depends_on=deps, optional=optional,
            ))
            steps.append(AnalysisStep(
                capability=cap, purpose=purpose, status=status,  # type: ignore[arg-type]
                resolved_tool=record.tool if record.status == "resolved" else "",
                resolved_algorithm=record.algorithm if record.status == "resolved" else "",
                depends_on=deps, optional=optional,
            ))
        return requirements, steps, selections

    # ── 阶段 1：intent → draft plan（数据未到手） ────────────────────
    def plan_from_intent(
        self,
        intent: MapRequestIntent,
        template_id: str = "",
        recipe_id: str = "",
        available_tools: Optional[Any] = None,
        project_verified: Optional[set] = None,
        use_memo: bool = True,
    ) -> MapProductPlan:
        # v2(R4)：memo 命中直接返回既有 plan（确定性规划器，同输入同输出）。
        # available_tools 参与（工具面变化改变 resolution evidence）；
        # project_verified 参与（#864 项目记忆排序）。测试可用 use_memo=False
        # 绕过。
        #
        # v3(Phase C)：**确定性裁决前置** —— recipe/template 选择是廉价排序
        # （~10 recipe × ~7 模板），先裁决再查 memo，键用裁决结果而非原始
        # 参数。intent 阶段（无显式参数）与 product 阶段（显式回放同一
        # recipe/template，plan 连续性）由此命中同一条目；project_verified
        # 只在改变裁决结果时才分键（裁决相同 ⇒ 输出相同，命中是正确语义）。
        # recipe_id 显式指定（webgis_map_intent 阶段的推荐/LLM 纠偏）优先——
        # 保证意图阶段与产品阶段用同一份计划（plan 连续性）。
        recipe = self.recipes.get(recipe_id) if recipe_id else None
        if recipe is None:
            # #1067(E-12): 回退重选此前不带 project_verified（#864 只修了
            # 主路径）—— 应用 recipe 与 evidence 候选在项目记忆排序场景下分叉。
            candidates = self.recipes.select_candidates(
                intent, project_verified=project_verified
            )
            recipe = candidates[0] if candidates else None
        if recipe is None:
            # 兜底：没有 task/cartography 命中时按通用 POI 分布 recipe
            recipe = (
                self.recipes.get("poi_distribution_overview")
                or self.recipes.default_recipe()
            )

        # V4 Wave 8（ADR-0104）：证据链阶段 1-6 发射（有界、消毒、绝不
        # 阻断规划；turn 上下文缺席时静默跳过 —— 记录面不伪造链）。
        try:
            from app.lib.runtime.chain_emitters import emit_chain, emit_chain_once
            from app.lib.runtime.gis_trace import Stage

            emit_chain(Stage.USER_INTENT, query=str(intent.query or "")[:200])
            emit_chain_once(
                Stage.PARSED_INTENT,
                task=str(getattr(intent, "task", "") or ""),
                area=str(getattr(intent, "area", "") or "")[:64],
            )
            emit_chain_once(
                Stage.TASK_ONTOLOGY,
                task=str(getattr(intent, "task", "") or ""),
                cartography=str(getattr(intent, "cartography", "") or ""),
            )
            emit_chain_once(
                Stage.CANDIDATE_WORKFLOWS,
                candidates=[str(getattr(c, "id", "")) for c in (candidates or [])][:8],
                selected=str(getattr(recipe, "id", "") or ""),
            )
            emit_chain_once(
                Stage.SELECTED_WORKFLOW,
                recipe_id=str(getattr(recipe, "id", "") or ""),
            )
        except Exception:  # noqa: BLE001 — 记录面绝不阻断规划
            pass

        # 模板选择：TemplateSelector 确定性评分（subject/task/outputs/
        # priority）是证据基线；显式 template_id（plan 连续性回放）只在
        # **改写裁决**时覆盖证据。review-B P2：memo 键用裁决结果后，显式
        # 回放与选择器一致时必须共用选择器 dump——否则两路径命中同一条目
        # 会带回错误出处的 template_selection evidence；不一致时 resolved
        # template id 本就分键，各持各的 dump。
        template: Optional[MapProductTemplate] = None
        selection = self.selector.select_product(
            intent=intent, recipe_id=recipe.id,
        )
        selection_dump: Dict[str, Any] = selection.model_dump()
        if selection.status == "selected":
            template = self.catalog.get_product_template(selection.template_id)
        if template_id:
            explicit = self.catalog.get_product_template(template_id)
            if explicit is not None and (template is None or explicit.id != template.id):
                template = explicit
                selection_dump = {
                    "status": "selected",
                    "template_id": explicit.id,
                    "decision": {"reason": f"explicit_template_id:{template_id}"},
                }

        memo_key = None
        if use_memo:
            try:
                # v2(review R3-P1-3)：intent 全量参与键 —— task/subject/
                # output_intents 等字段都改变 plan 输出，只按 query 键会在
                # 同 query 不同 intent 时返回错误计划（复现于 review）。
                intent_canonical = json.dumps(
                    intent.model_dump(), ensure_ascii=False, sort_keys=True,
                )
                memo_key = (
                    intent_canonical, recipe.id, template.id if template else "",
                    tuple(sorted(available_tools)) if available_tools is not None else None,
                    get_runtime_manifest().fingerprint,
                )
                with self._plan_memo_lock:
                    cached = self._plan_memo.get(memo_key)
                    if cached is not None:
                        return cached.model_copy(deep=True)
            except Exception:  # noqa: BLE001 — memo 失败退直算
                memo_key = None

        plan = MapProductPlan(
            plan_id=_plan_id(intent.query, recipe.id),
            query=intent.query,
            intent=intent,
            recipe_id=recipe.id,
            template_id=template.id if template else "",
            status="draft",
            template_selection=selection_dump,
            manifest_fingerprint=get_runtime_manifest().fingerprint,
        )

        # 数据需求（能力去重，保持声明顺序）；simple_view 不过度分析——
        # 只保留主数据获取 + 画像，砍掉聚合/密度/热点等衍生分析。
        # Kriging slice：显式算法 hint 在分支前算一次（两个分支共用）。
        _plan_interp, _hint = _interpolation_query_signals(intent.query)
        if intent.task == "simple_view":
            capabilities = [
                c for c in recipe.preferred_analysis
                if c in ("poi_query", "point_profile", "raster_source", "service_area",
                         "proximity_buffer")
            ] or recipe.preferred_analysis[:1]
        else:
            capabilities = list(recipe.preferred_analysis)
            for extra in recipe.optional_analysis:
                if extra not in capabilities:
                    capabilities.append(extra)
            # 任务条件能力（recipe.task_optional_analysis）：同一 recipe 服务
            # 多个 intent task（如 raster_distribution 兼任 change_detection）
            # 时，只有对应 task 才补的专属分析能力 —— 与 optional_analysis
            # 的无条件并入不同（后者会污染共享该 recipe 的其他 task 产品）。
            for extra in (getattr(recipe, "task_optional_analysis", None) or {}).get(
                intent.task, []
            ):
                if extra not in capabilities:
                    capabilities.append(extra)
            # Kriging vertical slice: 关键词门控的插值能力 —— 「克里金/插值」
            # 语义才计划 spatial_interpolation（raster_surface 产品族）。
            if _plan_interp and "spatial_interpolation" not in capabilities:
                capabilities.append("spatial_interpolation")

        requirements, steps, selections = self._resolve_capabilities(
            capabilities, intent, available_tools=available_tools,
            optional_capabilities=set(recipe.optional_analysis),
            algorithm_hint=_hint)
        plan.data_requirements = requirements
        plan.analysis_steps = steps
        plan.algorithm_selections = selections

        # 图层角色：来自产品模板或 recipe 声明（layer_type 由模型库推导）
        if template:
            for role_spec in template.layer_roles:
                plan.map_layers.append(PlannedLayer(
                    role=role_spec.role,  # type: ignore[arg-type]
                    layer_type=layer_type_for_cartography(role_spec.cartography,
                                                          role_spec.layer_type or "circle"),
                    cartography=role_spec.cartography,
                    source_capability=role_spec.source_capability,
                ))
            plan.outputs = list(template.outputs)
            plan.exports = list(template.exports) if not intent.export_intents else list(
                dict.fromkeys(template.exports + intent.export_intents)
            )
        else:
            plan.map_layers.append(PlannedLayer(
                role="primary",
                layer_type=layer_type_for_cartography(recipe.primary_cartography),
                cartography=recipe.primary_cartography,
                source_capability=recipe.preferred_analysis[0] if recipe.preferred_analysis else "",
            ))
            for carto in recipe.secondary_cartography:
                plan.map_layers.append(PlannedLayer(
                    role="secondary",
                    layer_type=layer_type_for_cartography(carto),
                    cartography=carto,
                    source_capability=recipe.preferred_analysis[0] if recipe.preferred_analysis else "",
                ))
        plan.map_model_selection = self._map_model_evidence(plan.map_layers)

        # 统计/图表：recipe 声明优先（default_statistics/default_charts），
        # 未声明时按 task 的确定性派生规则（单一事实源在 recipes 模块）。
        if "statistics" in intent.output_intents:
            plan.statistics = list(recipe.default_statistics) or \
                default_statistics_for_task(intent.task)
        if "chart" in intent.output_intents:
            plan.charts = list(recipe.default_charts) or \
                default_charts_for_task(intent.task)

        plan.validation = list(recipe.validation_rules)

        # Semantic GIS（方法论诚实）：把 pattern projection 的 required-roles
        # 缺席披露为计划级警告 —— 此前该信号只经 suggest_analysis_patterns
        # advisory 工具（LLM 可忽略），规划证据链上无处留档。「缺分母不能谈
        # 公平性」必须在 plan 里可见：产品仍可产出（数量/密度可评），但
        # 结论边界随证据下行，benchmark 可断言（methodology-honesty）。
        #
        # Semantic V2（ADR-0098）：两类触发 ——
        #  a) 角色缺口（keyword 门控）：查询词面携带模式语义词（公平/变化…）
        #     且必需角色缺席 → 披露（既有语义，回归锁定）；
        #  b) 决策族义务（task 门控）：一等决策 task（选址/适宜性/风险）命中
        #     即披露准则/权重/受体义务 —— 评价类产品的诚实底线，非噪声。
        # 每条警告携带稳定机器可读 code（warning_codes[0] 兼容单码断言）。
        try:
            from app.lib.gis.pattern_projection import project_patterns

            projection = project_patterns(intent.query, intent_task=intent.task)
            # Review F1: the projection's top-6 (alphabetical tie-break)
            # could cut a keyword-matched equity pattern below rank 3 — the
            # honesty guarantee must not be probabilistic, so walk ALL
            # matches; the keyword gate below keeps the volume bounded.
            for match in projection.matches:
                keyword_matched = any(
                    v.startswith("keyword:") for v in match.matched_via)
                role_gap = bool(match.missing_roles)
                decision_duty = bool(match.decision_disclosures)
                if role_gap and not keyword_matched:
                    # Task-alias-only matches are generic (EVERY administrative
                    # statistic query aliases spatial_equity); a methodology
                    # warning is issued only when the query itself carries the
                    # pattern's semantic keywords (公平/均衡/是否合理/…).
                    continue
                if not role_gap and not decision_duty:
                    continue
                plan.methodology_warnings.append({
                    "pattern": match.pattern_id,
                    "code": (match.warning_codes or [""])[0],
                    "warning_codes": list(match.warning_codes),
                    "missing_roles": sorted(match.missing_roles),
                    "disclosures": list(
                        (match.disclosures or []) +
                        (match.decision_disclosures or [])),
                    "pitfalls": list(match.common_pitfalls or [])[:2],
                    "stage": "planning",
                })
        except Exception:  # noqa: BLE001 — 披露是增值，绝不阻断规划
            pass

        # Workflow V2（Goal C / R1-A4）：专业关键词命中、但被 seed 资历守卫
        # 压制的 V2 recipe，其科学义务的**披露面**必须随胜出 plan 下行 ——
        # 「显著性热点」即便路由到描述性产品族，检验条件义务（数值字段/
        # 空间单元下限）也不得丢失。有界（≤2 recipe × ≤4 警告）、去重、
        # 纯披露（不改路由、不加能力 —— 路由语义仍由 seed 资历守卫锁定）。
        try:
            overlay_recipes = [
                r for r in self.recipes.keyword_hits(intent.query)
                if r.id != plan.recipe_id and r.workflow is not None
            ][:2]
            existing_codes = {
                str(w.get("code")) for w in plan.methodology_warnings if w.get("code")
            }
            for overlay in overlay_recipes:
                # 无 profile 时义务评估恒 PASS（unknown ≠ unsatisfied），
                # 因此 overlay 直接下发**声明义务的披露面**（义务的存在
                # 本身就是披露），不经过评估 —— 有界 ≤4/recipe。
                for obl_decl in overlay.workflow.obligations[:4]:
                    code = obl_decl.warning_code or (
                        f"OBLIGATION_{obl_decl.obligation_id.upper()}_UNMET")
                    if not code or code in existing_codes:
                        continue
                    plan.methodology_warnings.append({
                        "pattern": "workflow_obligation_overlay",
                        "code": code,
                        "warning_codes": [code],
                        "obligation_id": obl_decl.obligation_id,
                        "on_violation": obl_decl.on_violation,
                        "disclosures": [obl_decl.description] if obl_decl.description else [],
                        "stage": "routing_overlay",
                        "overlay_from": overlay.id,
                    })
                    existing_codes.add(code)
        except Exception:  # noqa: BLE001 — 披露是增值，绝不阻断规划
            pass

        if memo_key is not None:
            # 存入即深拷贝：调用方持有返回对象并可变（plan1.data_requirements=[]
            # 不得污染 memo 基底）。
            with self._plan_memo_lock:
                self._plan_memo[memo_key] = plan.model_copy(deep=True)
                while len(self._plan_memo) > self._plan_memo_max:
                    self._plan_memo.popitem(last=False)

        return plan

    def _map_model_evidence(self, layers: List[PlannedLayer]) -> List[Dict[str, Any]]:
        """图层 → MapModel 解析证据（有界：≤ layers 数）。"""
        from app.lib.cartography.model_library import get_map_model_registry
        models = get_map_model_registry()
        evidence: List[Dict[str, Any]] = []
        for layer in layers:
            model = models.resolve(layer.cartography)
            evidence.append({
                "cartography": layer.cartography,
                "map_model": model.id if model else "",
                "layer_type": layer.layer_type,
                "source": "map_model_registry" if model else "fallback_default",
            })
        return evidence

    # ── 阶段 2：数据回来后 → eligibility 复检 + 终稿 ─────────────────
    def finalize_with_profile(
        self,
        plan: MapProductPlan,
        profile: Optional[Dict[str, Any]],
        *,
        min_points_default: int = 10,
        available_tools: Optional[Any] = None,
        _chain_depth: int = 0,
    ) -> MapProductPlan:
        """Spatial Profile 到手后的确定性复检（§17 反一锤定音）。

        - profile 几何/点数驱动 eligibility；
        - algorithm applicability 复检（resolver 带 profile 重裁决；
          available_tools 传入时与 draft 阶段同视图，evidence 不漂移）；
        - 不合格元素禁用 + fallback 记录（from/to/reason/evidence）；
        - recipe 级失格 → 声明式降级链（方案 B/C）；链穷尽 → 数据不足
          说明卡（非空白图）。``_chain_depth`` 供方案 B 重规划递归封顶，
          外部调用保持缺省 0。
        """
        recipe = self.recipes.get(plan.recipe_id)
        finalized = plan.model_copy(deep=True)
        if recipe is None:
            finalized.status = "finalized"
            return finalized

        # V4 Wave 8：证据链阶段 4（DATA_PROFILE）+ 6（SELECTED_WORKFLOW 的
        # finalize 复裁决）—— profile 事实摘要有界入链。
        try:
            from app.lib.runtime.chain_emitters import emit_chain_once
            from app.lib.runtime.gis_trace import Stage

            if isinstance(profile, dict):
                emit_chain_once(
                    Stage.DATA_PROFILE,
                    geometry=str(profile.get("geometry") or profile.get("geometryKind") or "")[:32],
                    feature_count=profile.get("featureCount") or profile.get("feature_count"),
                    crs=str(profile.get("crs") or "")[:32],
                    field_count=len(profile.get("fields") or {}),
                )
            emit_chain_once(Stage.SELECTED_WORKFLOW, recipe_id=plan.recipe_id, phase="finalize")
        except Exception:  # noqa: BLE001 — 记录面绝不阻断规划
            pass

        report: EligibilityReport = self.check_recipe_eligibility(
            recipe, profile, min_points_default=min_points_default,
        )
        finalized.eligibility = {
            "recipe_id": recipe.id,
            "eligible": report.eligible,
            "disabled": [d.model_dump() for d in report.disabled],
            "checks": report.checks,
        }
        finalized.fallbacks = list(report.fallbacks)

        # V4（ADR-0151 #P5）：事实优先信号 —— 数据事实覆盖文本 hint 的
        # 冲突走披露面（绝不改写 intent / 路由）；证据摘要随 plan 下行。
        try:
            from app.services.gis_harness.recipes import EligibilityContext

            _signals = fact_signals(
                EligibilityContext.from_profile(profile), intent=plan.intent)
            finalized.data_fact_signals = _signals["evidence"]
            for conflict in _signals["conflicts"]:
                finalized.methodology_warnings.append({
                    "pattern": "fact_signals",
                    "code": str(conflict.get("code") or ""),
                    "warning_codes": [str(conflict.get("code") or "")],
                    "disclosures": [str(conflict.get("disclosure") or "")],
                    "stage": "finalize",
                })
        except Exception:  # noqa: BLE001 — 披露是增值，绝不阻断终稿
            pass

        # Workflow V2（Goal C / C4+C6）：workflow 契约评估 —— 数据角色解析 +
        # 科学义务联动（precondition 委托算法层裁决，不重复实现）。仅对带
        # workflow 画像的 recipe 生效；产出的警告并入 methodology_warnings
        # （复用 verdict 联动：READY 永远让位于披露），触发的语义回退产出
        # 带降级分类的结构化 FallbackDecision。失败不阻断终稿（诚实留痕）。
        finalized.workflow_contract = self._evaluate_workflow_contract(
            finalized, recipe, profile,
        )

        # algorithm applicability 复检：带 profile 重裁决（不改变
        # DataRequirement 的 available 状态——那是绑定回填的职责；只更新
        # 裁决证据，让 evidence 能解释『为什么这个算法没跑』）。
        if profile is not None:
            capabilities = [r.capability for r in finalized.data_requirements]
            optional_set = {
                r.capability for r in finalized.data_requirements if r.optional
            }
            # Review F2（kriging slice）：显式算法 hint 必须与 draft 阶段同源
            # —— finalize 不带 hint 会把「用户点名克里金」的 finalized 证据
            # 静默翻回默认 IDW，与 draft/resolved_tool 矛盾。hint 是
            # intent.query 的纯函数，这里重算零成本。
            text_hint = _interpolation_query_signals(plan.intent.query)[1]
            hint = text_hint
            # V4（ADR-0104 #6）：插值事实投影 —— 事实胜过文本。文本点名的
            # 克里金与事实冲突（去重后样本不足/常量场/地理 CRS）时不再顶位
            # （硬门仍最终裁决，这里消除「必拒 + 补偿替补」的证据噪声）；
            # 事实支持而文本未点名时给 fact hint（被动→证据驱动升级）。
            # 命名注意：此处局部变量不得叫 fact_signals —— 与模块级
            # fact_signals() 通用投影（ADR-0151 P5）同名会在函数域内遮蔽
            # （finalize 前段的通用信号调用会 UnboundLocalError）。
            interp_signals: Optional[Dict[str, Any]] = None
            if "spatial_interpolation" in capabilities:
                interp_signals = _interpolation_fact_signals(
                    profile, query=plan.intent.query)
                if text_hint and not interp_signals["ok_family_suitable"]:
                    hint = ""
                elif not text_hint and interp_signals["fact_hint"]:
                    hint = interp_signals["fact_hint"]
                # 事实支持的可选 capability 追加（≤2；仅当插值已被文本门
                # 计划 —— 关键词门不放大）。optional 语义：不污染主数据流，
                # resolver 独立裁决其可行性。
                for extra in interp_signals["extra_capabilities"]:
                    if extra not in capabilities:
                        capabilities.append(extra)
                        optional_set.add(extra)
            _, _, selections = self._resolve_capabilities(
                capabilities, plan.intent, profile=profile,
                available_tools=available_tools,
                optional_capabilities=optional_set,
                algorithm_hint=hint)
            finalized.algorithm_selections = selections
            if interp_signals is not None:
                finalized.algorithm_fact_signals = {
                    "ok_family_suitable": interp_signals["ok_family_suitable"],
                    "fact_hint": interp_signals["fact_hint"],
                    "extra_capabilities": list(interp_signals["extra_capabilities"]),
                    "evidence": interp_signals["evidence"],
                    "text_hint": text_hint,
                    "effective_hint": hint,
                }
                # review R2 MAJOR-5：显式点名算法被事实否决**绝不能静默**
                # （M2 不变式：近似替代显式请求必须披露）。文本点名但事实
                # 不支持 ⇒ 方法论警告随 plan 下行（READY 让位于披露）。
                if text_hint and hint != text_hint:
                    finalized.methodology_warnings.append({
                        "pattern": "algorithm_request_substituted",
                        "code": "EXPLICIT_ALGORITHM_FACT_REJECTED",
                        "warning_codes": ["EXPLICIT_ALGORITHM_FACT_REJECTED"],
                        "requested": str(text_hint)[:48],
                        "effective": str(hint or "default")[:48],
                        "disclosures": [
                            f"数据事实不支持点名的 {text_hint}（样本/方差/CRS"
                            f"证据冲突），已按证据选择 {hint or '默认方法'}；"
                            "硬门仍可最终拒绝。"
                        ],
                        "stage": "finalize",
                    })

        disabled_elements = {d.element for d in report.disabled}
        # 主数据几何（点层提升的先决条件——面数据上提升 circle 层是制图空转）
        geom_types = (profile or {}).get("geometryTypes") or []
        profile_geom = "unknown"
        if isinstance(geom_types, list) and geom_types:
            from app.services.gis_harness.recipes import _geometry_category
            profile_geom = _geometry_category(geom_types)

        # audit #832: 几何多态表达的 layer_type 跟随真实数据几何 ——
        # categorical_thematic 计划为 fill 而点数据授权 circle 时，primary
        # 永不绑定、completeness 永远 missing（#784 修复的残留面）。
        for layer in finalized.map_layers:
            resolved = _geometry_aware_layer_type(
                layer.cartography, layer.layer_type, profile_geom)
            if resolved != layer.layer_type:
                layer.note = (
                    layer.note + "; " if layer.note else ""
                ) + f"layer_type {layer.layer_type}->{resolved} by profile geometry"
                layer.layer_type = resolved
        finalized.map_model_selection = self._map_model_evidence(finalized.map_layers)

        # ── recipe 级失格 → 声明式降级链（方案 B/C，ADR-0151 P3）────────
        # 旧实现只有「全禁 + 追加点图兜底」的静默路径；现在先求声明链，
        # 链上最优 eligible 目标产出真正的方案 B（按目标 recipe 完整重规划
        # + 终稿，带链式尝试证据）；链穷尽才落「数据不足说明卡」（非空白
        # 图、非空 MapSpec）。
        chain: Optional[ChainResolution] = None
        if not report.eligible and _chain_depth == 0:
            chain = resolve_fallback_chain(
                recipe, profile=profile,
                registry=self.recipes,
                min_points_default=min_points_default,
            )
            if chain.resolved and chain.final_recipe != recipe.id:
                return self._finalize_with_fallback_recipe(
                    plan, recipe, report, chain, profile,
                    min_points_default=min_points_default,
                    available_tools=available_tools,
                )

        # 图层级裁决（V4 通用化，ADR-0151 P3）：旧实现是
        # visual_heatmap/density_overview 与 aggregate_grid 两条硬编码分支；
        # 现按「被禁元素 × 计划图层」通用求解 —— 任何被禁元素命中的图层
        # 一律禁用；声明了 use 目标且该元素有存活图层时提升 primary，否则
        # 提升可用点叠加（converter 按真实几何定型）。每次降级都落带
        # downgrade_class/disclosure 的 FallbackDecision。
        disabled_by_element: Dict[str, Any] = {}
        for d in report.disabled:
            disabled_by_element.setdefault(d.element, d)
        declared_use: Dict[str, str] = {}
        for fb in recipe.fallbacks:
            if fb.reason_code and fb.use and fb.reason_code not in declared_use:
                declared_use[fb.reason_code] = fb.use
        degraded_elements: List[str] = []
        for layer in finalized.map_layers:
            element = _ELEMENT_ALIASES.get(layer.cartography, layer.cartography)
            reason = disabled_by_element.get(element)
            if reason is None or not layer.enabled:
                continue
            layer.enabled = False
            layer.role = "secondary"  # 禁用层不再是 primary（单一 primary 不变式）
            layer.note = (
                layer.note + "; " if layer.note else ""
            ) + f"disabled: {reason.reason_code}"
            if element not in degraded_elements:
                degraded_elements.append(element)
        for element in degraded_elements:
            reason = disabled_by_element.get(element)
            target_use = declared_use.get(reason.reason_code, "") if reason else ""
            promoted = None
            if target_use:
                promoted = next(
                    (ly for ly in finalized.map_layers
                     if ly.cartography == target_use and ly.enabled),
                    None,
                )
            if promoted is None:
                promoted = next(
                    (ly for ly in finalized.map_layers
                     if ly.cartography in ("point_overlay", "simple_point_map")
                     and ly.enabled),
                    None,
                )
            if promoted is not None and not any(
                ly.role == "primary" and ly.enabled for ly in finalized.map_layers
            ):
                # 单一 primary 不变式：多元素同批降级时，首个提升已产生
                # primary；后续元素不得再提升第二 primary（P3 修复，此前
                # 两个禁用元素可各提升一个 primary）。
                promoted.role = "primary"
            resolved_to = target_use or (
                promoted.cartography if promoted is not None else "")
            finalized.fallbacks.append(FallbackDecision(
                from_element=element,
                to_element=resolved_to,
                reason_code=reason.reason_code if reason else "INELIGIBLE",
                evidence={
                    **(reason.evidence if reason else {}),
                    "profile_geometry": profile_geom,
                },
                downgrade_class="approximation",
                disclosure=(
                    f"{element} 因 {reason.reason_code if reason else '数据不达标'} "
                    "不可用" + (f"，已降级为 {resolved_to}。" if resolved_to else "。")
                ),
            ))

        # recipe 级失格且链穷尽/未启用链求解 → 禁用全部专题层（reference
        # 保留）并记录带链证据的 RECIPE_INELIGIBLE。此前只禁 cartography 名
        # 恰好出现在 disabled_elements 里的层 —— 失格 recipe 的主层常因此
        # 存活，产出「失格 + 存活主层」的自相矛盾计划（P0 case05/06）。
        insufficient_card: Optional[Dict[str, Any]] = None
        if not report.eligible:
            for layer in finalized.map_layers:
                if layer.enabled and layer.role != "reference":
                    layer.enabled = False
                    layer.role = "secondary"
                    layer.note = (
                        layer.note + "; " if layer.note else ""
                    ) + "disabled: RECIPE_INELIGIBLE"
            attempts = [
                a.to_bounded_dict() for a in chain.attempts
            ] if chain is not None else []
            finalized.fallbacks.append(FallbackDecision(
                from_element=recipe.id,
                to_element="",
                reason_code="RECIPE_INELIGIBLE",
                evidence={
                    "disabled": sorted(disabled_elements),
                    "chain": attempts[:16],
                },
                attempts=attempts[:16],
                auto_generated=bool(chain and chain.auto_generated_used),
                downgrade_class="degraded",
                disclosure=(
                    "数据不满足任何可用制图方案的最小要求："
                    "本产品以数据说明卡替代专题图。"
                ),
            ))
            disabled_brief = "；".join(
                f"{d.element}:{d.reason_code}" for d in report.disabled[:4])
            insufficient_card = {
                "code": "INSUFFICIENT_DATA",
                "pattern": recipe.id,
                "text": (
                    f"数据不足以支撑「{recipe.name or recipe.id}」：{disabled_brief}。"
                    + (f"已尝试 {len(attempts)} 个替代方案，均不满足。" if attempts else "")
                    + "请补充或更换数据后重试。"
                ),
            }

        # 终稿组件集：优先走 component_resolver/composer（composition 驱动），
        # 失败回退到 build_default_components（兼容旧路径）。
        primary_layer = next(
            (ly for ly in finalized.map_layers if ly.role == "primary" and ly.enabled),
            None,
        )
        primary_carto = primary_layer.cartography if primary_layer else "point_overlay"
        try:
            from app.services.gis_harness.component_composer import get_component_composer
            from app.services.gis_harness.component_resolver import get_component_resolver
            template = self.templates.get(plan.template_id) if plan.template_id else None
            comp_tmpl_id = (template.composition_template_id if template and template.composition_template_id else "")
            # report_product prefers a composition that provides export_layout/map_border
            if plan.intent.report_product:
                output_target = "pdf"
                # force a report-capable composition（接线模板不满足时改选；
                # 满足 export_layout 必备的接线模板保持不变）
                from app.lib.cartography.composition_templates import get_composition_template_registry
                compo_reg = get_composition_template_registry()

                def _requires_export_layout(c) -> bool:
                    return c is not None and any(
                        s.id == "export_layout" and s.cardinality == "required"
                        for s in c.component_slots
                    )

                wired = compo_reg.get(comp_tmpl_id) if comp_tmpl_id else None
                if not _requires_export_layout(wired):
                    # wired 为 None 时（未接线/未注册 id）同样走自动改选 ——
                    # 报告产品的版面契约优先于具体模板选择。
                    cands = compo_reg.find_for_map_model(primary_carto, "pdf")
                    report_cands = [c for c in cands if _requires_export_layout(c)]
                    if report_cands:
                        comp_tmpl_id = report_cands[0].id
            else:
                output_target = "interactive"
            resolver = get_component_resolver()
            # available_context: statistics/chart if recipe declares those outputs
            ctx: list = []
            if plan.statistics:
                ctx.append("statistics")
            if plan.charts:
                ctx.append("chart")
            # v2：区位插图上下文 —— 有具名地理范围（scope.name）的报告产品
            # 才供应 inset_context（resolver 据此选出 inset_map 槽位；bbox
            # 由 Agent 经 component_update 填充，渲染端空 bbox 自弃）。
            if plan.intent.report_product and plan.intent.scope.name:
                ctx.append("inset_context")
            selection = resolver.resolve(
                composition_template_id=comp_tmpl_id,
                map_model_id=primary_carto,
                output_target=output_target,
                available_context=ctx,
            )
            title_text = self._default_title(plan)
            subtitle_text = plan.intent.scope.name if plan.intent.scope.name else ""
            # layer binding（v2）：全部图层角色 → layer_id（图例族
            # all_thematic 槽位按层展开实例；无兼容图例类型的层由 composer
            # 如实跳过 —— 例如纯边界参考层）。角色→层模型映射供 composer
            # 按层选型（heatmap→colorbar、choropleth→legend）与组合校验按
            # 绑定层判模型兼容。
            layer_bindings: dict = {}
            layer_model_ids: dict = {}
            for ly in finalized.map_layers:
                if not ly.layer_id or not ly.enabled:
                    continue
                layer_bindings[ly.role] = ly.layer_id
                layer_model_ids[ly.layer_id] = ly.cartography
            composer = get_component_composer()
            overrides = (template.component_overrides if template else {})  # type: ignore[attr-defined]
            composed = composer.compose(
                selection,
                title_text=title_text,
                subtitle_text=subtitle_text,
                layer_bindings=layer_bindings,
                composition_template_id=selection.composition_template_id,
                overrides=overrides if isinstance(overrides, dict) else {},
                layer_model_ids=layer_model_ids,
            )
            if not composed:
                raise ValueError("empty composition")
            # 组合级校验（conflicts/cardinality/required/forbidden/planned/
            # 孤儿绑定）：error 级违规 → 抛错走 build_default_components 兜底；
            # warning（zone 碰撞等）记入 evidence，QA（semantic_checks）单独报告。
            from app.lib.cartography.composition_validation import validate_component_composition
            validation = validate_component_composition(
                composed,
                composition_template_id=selection.composition_template_id,
                map_model_id=primary_carto,
                layer_ids=[ly.layer_id for ly in finalized.map_layers if ly.layer_id],
                output_target=output_target,
                layer_model_ids=layer_model_ids,
            )
            if not validation.ok:
                raise _CompositionRejectedError(
                    "composition violations: "
                    + "; ".join(
                        f"{v.code}[{v.component_type or v.slot}] {v.detail}"
                        for v in validation.errors
                    ),
                    violations=[v.to_dict() for v in validation.errors],
                )
            finalized.components = composed
            self._append_methodology_disclosure(finalized)
            # stash composition evidence
            finalized.template_selection = {
                **finalized.template_selection,
                "composition_template_id": selection.composition_template_id,
                "component_templates": selection.component_templates,
                "composition_warnings": [v.to_dict() for v in validation.warnings],
                # recipe 数据面导出画像（chart 必需信号的既有读面 —— 此前
                # 断线：product_graph 读 template_selection.export_profile
                # 而 planner 从未写入；现随组合证据一并落盘，facet
                # contract / chart:required 合成在真实会话路径生效）。
                "export_profile": dict(getattr(recipe, "export_profile", None) or {}),
            }
        except Exception as exc:
            # 组合路径失败 → build_default_components 兜底，但必须留下可追溯
            # evidence（FallbackDecision + template_selection），不静默吞掉。
            reason_code = (
                "COMPOSITION_INVALID" if isinstance(exc, _CompositionRejectedError)
                else "COMPOSITION_ERROR"
            )
            fallback_evidence: Dict[str, Any] = {
                "reason_code": reason_code,
                "error": str(exc)[:500],
            }
            if isinstance(exc, _CompositionRejectedError):
                fallback_evidence["violations"] = exc.violations
            finalized.fallbacks.append(FallbackDecision(
                from_element="composition_template",
                to_element="default_components",
                reason_code=reason_code,
                evidence=fallback_evidence,
            ))
            finalized.template_selection = {
                **finalized.template_selection,
                "composition_fallback": fallback_evidence,
                "export_profile": dict(getattr(recipe, "export_profile", None) or {}),
            }
            finalized.components = build_default_components(
                primary_cartography=primary_carto,
                title=self._default_title(plan),
                subtitle=plan.intent.scope.name if plan.intent.scope.name else "",
                report_product=plan.intent.report_product,
                scope_name=plan.intent.scope.name,
                subject_category=plan.intent.subject.category,
                extra_types=recipe.default_components,
            )
            self._append_methodology_disclosure(finalized)

        if insufficient_card is not None:
            self._append_insufficient_data_card(finalized, insufficient_card)

        finalized.status = "finalized"
        finalized.completeness = self.assess_completeness(finalized)
        return finalized

    @staticmethod
    def _append_methodology_disclosure(finalized: MapProductPlan) -> None:
        """VNext §5：计划带方法论警告 → methodology_note 组件随产品落地。

        「缺分母不能谈公平性」长在地图产品上：终稿组件集携带警告码+文案
        （live 渲染端 methodology-note.tsx）。幂等（已在场不重复追加）；
        失败绝不阻断终稿（披露是增值，组件缺席由 QA 另行披露）。
        """
        if not finalized.methodology_warnings:
            return
        if any(
            getattr(c, "type", "") == "methodology_note"
            for c in finalized.components
        ):
            return
        try:
            from app.services.gis_harness.components import (
                MAX_METHODOLOGY_NOTES,
                methodology_note_component,
            )

            notes = []
            for w in finalized.methodology_warnings[:6]:
                disclosures = [str(d) for d in (w.get("disclosures") or []) if d]
                # review m2：每条披露一 note（有界）—— 硬约束否决等次级
                # 披露不再被 [0] 吞掉。
                for text in disclosures[:3]:
                    notes.append({
                        "code": str(w.get("code") or ""),
                        "pattern": str(w.get("pattern") or ""),
                        "text": text,
                    })
                if not disclosures and w.get("missing_roles"):
                    notes.append({
                        "code": str(w.get("code") or ""),
                        "pattern": str(w.get("pattern") or ""),
                        "text": "缺失角色: " + ",".join(
                            map(str, w["missing_roles"][:4])),
                    })
            notes = notes[:MAX_METHODOLOGY_NOTES]
            if notes:
                finalized.components.append(methodology_note_component(notes))
        except Exception:  # noqa: BLE001 — 披露组件失败不阻断终稿
            pass

    def _finalize_with_fallback_recipe(
        self,
        plan: MapProductPlan,
        origin_recipe: CartographyRecipe,
        report: EligibilityReport,
        chain: ChainResolution,
        profile: Optional[Dict[str, Any]],
        *,
        min_points_default: int = 10,
        available_tools: Optional[Any] = None,
    ) -> MapProductPlan:
        """链式降级命中方案 B：按目标 recipe 完整重规划并终稿（ADR-0151 P3）。

        目标 recipe 已通过链上复检（eligible），其 finalize 不再进入链
        求解（_chain_depth=1 封顶）。链式尝试证据（含落选者）以首条
        FallbackDecision 随方案 B 下行 —— 降级可解释，绝不静默换案。
        """
        fb_plan = self.plan_from_intent(
            plan.intent, recipe_id=chain.final_recipe,
            available_tools=available_tools, use_memo=False,
        )
        fb_final = self.finalize_with_profile(
            fb_plan, profile,
            min_points_default=min_points_default,
            available_tools=available_tools,
            _chain_depth=1,
        )
        attempts = [a.to_bounded_dict() for a in chain.attempts][:16]
        target = self.recipes.get(chain.final_recipe)
        target_name = (target.name if target else "") or chain.final_recipe
        fb_final.fallbacks.insert(0, FallbackDecision(
            from_element=origin_recipe.id,
            to_element=chain.final_recipe,
            reason_code=chain.primary_reason_code,
            evidence={
                "origin_recipe": origin_recipe.id,
                "origin_disabled": sorted({d.element for d in report.disabled}),
                "chain": attempts,
            },
            attempts=attempts,
            auto_generated=chain.auto_generated_used,
            downgrade_class="approximation",
            disclosure=(
                f"首选方案「{origin_recipe.name or origin_recipe.id}」因 "
                f"{chain.primary_reason_code} 不可用，已切换为方案 "
                f"「{target_name}」。"
            ),
        ))
        fb_final.completeness = self.assess_completeness(fb_final)
        return fb_final

    @staticmethod
    def _append_insufficient_data_card(
        finalized: MapProductPlan, card: Dict[str, Any],
    ) -> None:
        """数据不足说明卡（复用 methodology_note 通道，前端零改动）。

        链穷尽时产品不落空白图：说明卡组件随 MapSpec 下行（07 线消费），
        失败不阻断终稿。
        """
        if any(
            getattr(c, "type", "") == "methodology_note"
            and any(
                w.get("code") == "INSUFFICIENT_DATA"
                for w in (getattr(c, "options", {}) or {}).get("warnings", [])
            )
            for c in finalized.components
        ):
            return
        try:
            from app.services.gis_harness.components import (
                methodology_note_component,
            )

            finalized.components.append(methodology_note_component([card]))
        except Exception:  # noqa: BLE001 — 说明卡失败不阻断终稿
            pass

    def _evaluate_workflow_contract(
        self,
        plan: MapProductPlan,
        recipe: CartographyRecipe,
        profile: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """workflow 画像的确定性评估（finalize 阶段，Goal C / C4+C6）。

        返回有界摘要 dict（写入 plan.workflow_contract）；无 workflow 画像
        返回 None。副作用：把义务/角色警告并入 plan.methodology_warnings，
        把触发的语义回退追加为 FallbackDecision（带 downgrade_class 与
        用户可见 disclosure ——「模型 fallback 不得隐去语义降级」）。
        任何异常都不得阻断终稿（评估失败如实留痕为 status=error）。
        """
        wf_profile = getattr(recipe, "workflow", None)
        if wf_profile is None:
            return None
        try:
            from app.services.gis_harness.workflow_schema import (
                evaluate_workflow_obligations,
                resolve_data_roles,
            )

            # R1-A3：角色绑定用**真实计划行**证据 —— 数据行已落（available/
            # done 且带 bound_ref）才把对应角色升为 bound；capability_hint
            # 存在 ≠ 数据在场。data_blockers 因此在生产路径可达。
            bound_refs: Dict[str, str] = {}
            role_by_cap: Dict[str, str] = {}
            for req in wf_profile.data_roles:
                # R2-9：同一 capability_hint 服务多个角色时按声明序首个胜出
                # （确定性；词表校验另约束必选 block 角色不得复用 hint）。
                if req.capability_hint and req.capability_hint not in role_by_cap:
                    role_by_cap[req.capability_hint] = req.role
            for row in plan.data_requirements:
                role_name = role_by_cap.get(row.capability)
                if (role_name and row.status in ("available", "done")
                        and row.bound_ref):
                    bound_refs[role_name] = row.bound_ref

            role_resolutions = resolve_data_roles(
                recipe.id, wf_profile, resolver_profile=profile,
                bound_refs=bound_refs,
            )
            contract = evaluate_workflow_obligations(
                recipe.id, wf_profile,
                resolver_profile=profile, role_resolutions=role_resolutions,
            )
            # 合并去重：同一 warning_code 已在 plan（如 pattern projection 的
            # 规划期披露）时不再重复追加 —— 披露幂等，证据不冗余。
            existing_codes = {
                str(w.get("code")) for w in plan.methodology_warnings if w.get("code")
            }
            for warning in contract.warnings[:8]:
                if str(warning.get("code")) not in existing_codes:
                    plan.methodology_warnings.append(warning)
                    existing_codes.add(str(warning.get("code")))
            # 证据刷新：已被正向证据满足的义务，其规划期披露（同码）过时
            # —— 确定性移除（finalize 的 profile 事实优先于 draft 假设）。
            satisfied_codes = {
                ev.warning_code for ev in contract.obligations
                if ev.status == "satisfied" and ev.warning_code
            }
            if satisfied_codes:
                plan.methodology_warnings = [
                    w for w in plan.methodology_warnings
                    if str(w.get("code")) not in satisfied_codes
                ]

            # 触发的语义回退 → 结构化 FallbackDecision（匹配工作流声明的
            # 降级策略；未声明策略时按 degraded 保守合成，绝不静默）。
            declared = {p.reason_code: p for p in wf_profile.fallback_policies}
            triggered: List[tuple] = []  # (reason_code, from_element, to_element, evidence)
            for res in role_resolutions:
                if res.status == "degraded":
                    triggered.append((
                        res.reason_code, f"role:{res.role}", "",
                        {"role": res.role, "policy": res.missing_policy},
                    ))
            for ev in contract.obligations:
                if ev.status in ("degraded", "blocked") and ev.on_violation == "degrade_with_disclosure":
                    triggered.append((
                        ev.warning_code or f"OBLIGATION_{ev.obligation_id.upper()}_UNMET",
                        f"obligation:{ev.obligation_id}", "",
                        {"verdict": ev.detail[:80]},
                    ))
            for reason_code, from_el, to_el, evidence in triggered[:8]:
                policy = declared.get(reason_code)
                # R1-A14：未声明策略的回退也必须带用户可见披露 —— 兜底取
                # 同码义务/角色警告的 detail，绝不落空字符串（「绝不静默」）。
                warning_detail = next(
                    (w["disclosures"][0] for w in contract.warnings
                     if w.get("code") == reason_code and w.get("disclosures")),
                    "",
                )
                plan.fallbacks.append(FallbackDecision(
                    from_element=from_el,
                    to_element=(policy.to_element if policy else to_el),
                    reason_code=reason_code,
                    evidence={
                        **evidence,
                        "downgrade_class": policy.downgrade_class if policy else "degraded",
                        "disclosure": (
                            (policy.disclosure if policy else "")
                            or warning_detail
                        ),
                    },
                    downgrade_class=policy.downgrade_class if policy else "degraded",
                    disclosure=(policy.disclosure if policy else "") or warning_detail,
                ))

            return {
                "schema_version": 2,
                "domain": wf_profile.domain,
                "workflow_family": wf_profile.workflow_family,
                "roles": [r.to_bounded_dict() for r in role_resolutions[:16]],
                "obligations": [o.to_bounded_dict() for o in contract.obligations[:16]],
                "method_blockers": contract.method_blockers[:8],
                "data_blockers": contract.data_blockers[:8],
                "stage": "finalize",
            }
        except Exception as exc:  # noqa: BLE001 - 评估失败诚实留痕，不阻断终稿
            return {
                "schema_version": 2,
                "domain": getattr(wf_profile, "domain", ""),
                "status": "error",
                "error": str(exc)[:200],
                "stage": "finalize",
            }

    def check_recipe_eligibility(
        self,
        recipe: CartographyRecipe,
        profile: Optional[Dict[str, Any]],
        *,
        min_points_default: int = 10,
    ) -> EligibilityReport:
        from app.services.gis_harness.recipes import check_eligibility

        return check_eligibility(
            recipe, profile=profile, min_points_default=min_points_default,
        )

    def _default_title(self, plan: MapProductPlan) -> str:
        intent = plan.intent
        template = self.templates.get(plan.template_id) if plan.template_id else None
        pattern = template.title_pattern if template and template.title_pattern else "{scope}{subject}分布"
        return pattern.format(
            scope=intent.scope.name or "",
            subject=intent.subject.category or "",
        )

    # ── 完整性评估（Harness evidence 消费面）──────────────────────────
    def assess_completeness(self, plan: MapProductPlan) -> Dict[str, Any]:
        expected_outputs = set(plan.outputs) or {"interactive_map"}
        present: Dict[str, bool] = {}
        # #716: interactive_map means an AUTHORED+BOUND layer exists — planned
        # layers default enabled, so the old planned-layer check reported
        # completeness even when every authoring attempt failed.
        # #784: layer_ids 绑定流没有 primary_ref —— 绑定的已提交图层本身
        # 就是「已授权且已挂载」的证据，bound_ref 不再是必要条件。
        has_bound_layer = any(
            ly.enabled and ly.layer_id for ly in plan.map_layers
        )
        present["interactive_map"] = has_bound_layer
        # #784: data_bound 的判据是「计划元素绑到了已提交图层或 ref 任一」——
        # grid/simple_view 流常只带 layer_ids（无 primary_ref），绑定的图层
        # 本身就是数据到位的证据。
        present["data_bound"] = any(
            ly.bound_ref or ly.layer_id for ly in plan.map_layers
        )
        # #784: 统计维只在该产品族声明了统计输出时适用 —— simple_view 等
        # 轻量产品不该因「没有统计」被记成 missing statistics。
        present["statistics"] = (
            bool(plan.statistics) and any(
                s.status == "done" for s in plan.analysis_steps
            )
        ) if plan.statistics else True
        present["components"] = bool(plan.components)
        present["exports"] = bool(plan.exports) if "export" in expected_outputs else True
        # #784: 未绑定的结构性规划图层（primary/reference）必须可见 ——
        # 此前任何单个绑定图层就满足 interactive_map，缺失的 reference 层
        #（如教育产品的行政区层）是静默的，completeness 对缺层产品假报
        # complete。secondary 点叠加是可选增强（planner 资格降级本身就可能
        # 砍掉它），不因缺席记 missing。
        unbound_planned = [
            ly.cartography or ly.layer_type
            for ly in plan.map_layers
            if ly.enabled and ly.role != "secondary"
            and not (ly.bound_ref or ly.layer_id)
        ]
        present["planned_layers"] = not unbound_planned
        missing = sorted(k for k, v in present.items() if not v)
        return {
            "expected_outputs": sorted(expected_outputs),
            "present": present,
            "missing": missing,
            "unbound_planned_layers": unbound_planned,
            "complete": not missing,
            "fallback_count": len(plan.fallbacks),
        }


__all__ = [
    "MapProductPlan",
    "DataRequirement",
    "AnalysisStep",
    "AlgorithmSelectionRecord",
    "PlannedLayer",
    "MapProductPlanner",
    # CAPABILITY_TOOLS 经 PEP 562 __getattr__ 惰性提供，不在模块命名空间（ruff F822 豁免）
    "CAPABILITY_TOOLS",  # noqa: F822
    "capability_tool_map",
    "resolve_tool_for_capability",
    "layer_type_for_cartography",
]
