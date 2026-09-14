"""CartographerAgent —— 制图专家（ADR-0189 D1）。

挂载三域**既有确定性**制图知识库，`compose()` 全程零 LLM：

1. 色板分类规则域：``choose_classification``（分布驱动：重尾 →
   head_tail，适度偏态 → natural_breaks/Jenks，近均匀 →
   equal_interval/quantiles，落选者留痕）→ ``symbology_decision_from_
   values``（method×k×palette×clip_policy，CIEDE2000/CVD 硬约束）→
   ``build_graduated_spec`` / ``build_categorical_spec`` →
   ``spec_to_paint`` 单一投影点（图例 ↔ paint 不漂移）；
2. 组件版面整饰域：``required_components_for`` 必配基线（title /
   scale_bar / north_arrow / attribution / legend）→ ``solve_layout_v4``
   防撞自愈链 → 抑制/冲突诚实披露；
3. 多尺度与表达式域：产物过 ``canonicalize_mapspec`` schema 闸；
   源 profile 内嵌（featureCount/bbox/fields/CRS）使语义检查可评估。

配方先验（RecipeRegistry，164 配方）只参与检索与披露，不覆盖分布
证据。产出 ``MapSpecDeliveryRef``：载荷入 ``ArtifactLedger``，上下文
只过券（Zero Big Data in Context）。
"""
from __future__ import annotations

import logging
from typing import Any, ClassVar, Dict, FrozenSet, List, Optional, Tuple

from app.lib.cartography.component_composer import (
    OUTPUT_PURPOSES,
    required_components_for,
)
from app.lib.cartography.layout_solver import (
    LayoutParticipantV4,
    solve_layout_v4,
)
from app.lib.cartography.mapspec_schema import canonicalize_mapspec
from app.lib.cartography.quality_loop import (
    cartographic_fingerprint,
    review_and_repair_cartography,
)
from app.lib.cartography.thematic_spec import (
    build_categorical_spec,
    build_graduated_spec,
    spec_to_paint,
)
from app.lib.cartography.visualization_plan import (
    choose_classification,
    distribution_stats_from_values,
)
from app.services.agent_swarm.base import BaseSpecialistAgent
from app.services.agent_swarm.contracts import (
    _MAX_DELIVERY_SUMMARY_LEN,
    _MAX_DELIVERY_WARNING_LEN,
    MapSpecDeliveryRef,
)
from app.services.agent_swarm.specialists.ledger import (
    ArtifactLedger,
    payload_digest,
)

logger = logging.getLogger(__name__)

#: 分布裁决推荐集（模型先验；choose_classification 会按分布证据推翻）。
DEFAULT_RECOMMENDED_CLASSIFIERS: Tuple[str, ...] = (
    "natural_breaks",
    "head_tail",
    "quantiles",
    "equal_interval",
)

#: 专题组件 → 槽位意图（确定性基线；solve_layout_v4 负责防撞自愈）。
_COMPONENT_ZONE_INTENT: Tuple[Tuple[str, str, int], ...] = (
    ("title", "top-left", 10),
    ("legend", "top-right", 20),
    ("north_arrow", "top-right", 30),
    ("scale_bar", "bottom-left", 20),
    ("attribution", "bottom-center", 30),
)

_MAPSPEC_POSITIONS: FrozenSet[str] = frozenset(
    {"top-left", "top-center", "top-right",
     "bottom-left", "bottom-center", "bottom-right"}
)

#: 数值不足时的诚实兜底（不出分级、不出图例，交由审计红线兜底）。
_FALLBACK_COLOR = "#cccccc"


def _finite_numbers(raw: Any) -> List[float]:
    out: List[float] = []
    for v in raw if isinstance(raw, list) else []:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        out.append(float(v))
    return out


def _walk_coordinates(geometry: Dict[str, Any]) -> List[Tuple[float, float]]:
    """递归收集坐标对（Point/LineString/Polygon/Multi* 通吃）。"""
    out: List[Tuple[float, float]] = []

    def _walk(node: Any) -> None:
        if not isinstance(node, (list, tuple)):
            return
        if len(node) >= 2 and isinstance(node[0], (int, float)) and isinstance(node[1], (int, float)):
            out.append((float(node[0]), float(node[1])))
            return
        for child in node:
            _walk(child)

    _walk((geometry or {}).get("coordinates"))
    return out


def _survey_geojson(
    geojson: Dict[str, Any], field: str,
) -> Dict[str, Any]:
    """字段勘察：有限值 / 类别值 / null 数 / bbox / 几何类型（诚实统计）。"""
    features = (geojson or {}).get("features", []) or []
    raw_values: List[Any] = []
    categories: List[str] = []
    geometry_types: List[str] = []
    xs: List[float] = []
    ys: List[float] = []
    null_count = 0
    for f in features:
        if not isinstance(f, dict):
            continue
        props = f.get("properties") or {}
        if field in props:
            v = props[field]
            if v is None:
                null_count += 1
            elif isinstance(v, bool) or not isinstance(v, (int, float)):
                categories.append(str(v))
            else:
                raw_values.append(float(v))
        geom = f.get("geometry") or {}
        gtype = geom.get("type")
        if gtype and gtype not in geometry_types:
            geometry_types.append(gtype)
        for lng, lat in _walk_coordinates(geom):
            xs.append(lng)
            ys.append(lat)
    values = [v for v in raw_values if v == v and v not in (float("inf"), float("-inf"))]
    bbox = (
        [min(xs), min(ys), max(xs), max(ys)]
        if xs and ys else None
    )
    return {
        "feature_count": len(features),
        "values": values,
        "categories": categories,
        "null_count": null_count,
        "geometry_types": geometry_types,
        "bbox": bbox,
    }


def _build_source_profile(
    survey: Dict[str, Any], field: str,
) -> Dict[str, Any]:
    """内嵌源 profile（语义检查的评估面；缺席 → not_evaluated）。"""
    fields_entry: Dict[str, Any]
    if survey["categories"]:
        fields_entry = {
            "type": "string",
            "null_count": survey["null_count"],
            "unique_count": len(set(survey["categories"])),
        }
    elif survey["values"]:
        fields_entry = {
            "type": "number",
            "min": min(survey["values"]),
            "max": max(survey["values"]),
            "null_count": survey["null_count"],
        }
    else:
        fields_entry = {"type": "unknown", "null_count": survey["null_count"]}
    profile: Dict[str, Any] = {
        "featureCount": survey["feature_count"],
        "geometryTypes": survey["geometry_types"] or ["Unknown"],
        "fields": {field: fields_entry},
        "crs": "EPSG:4326",
        "crs_status": "explicit",
    }
    if survey["bbox"]:
        profile["bbox"] = survey["bbox"]
    return profile


class CartographerAgent(BaseSpecialistAgent):
    """制图专家：确定性 MapSpec 编排（compose）与审计驱动修复（revise）。"""

    name: ClassVar[str] = "cartographer"
    role_name: ClassVar[str] = "cartography_specialist"

    #: 制图与版面出图面（不碰数据接入与重计算；与 0188 两专家不相交）。
    TOOL_ALLOWLIST: ClassVar[FrozenSet[str]] = frozenset({
        "create_thematic_map",
        "apply_layer_style",
        "webgis_layout_set",
        "export_thematic_map",
        "webgis_compile_maplibre",
        "combine_map_theme",
    })

    SPECIALIST_PROMPT: ClassVar[str] = (
        "你是制图专家：按视觉变量纪律（位置/大小/形状/明度/色相）裁决"
        "符号化与分类方法，保证图例闭环、比例尺/指北针/署名必配、组件"
        "防撞；产出规范化 MapSpec 交付券，绝不把 GeoJSON 载荷写回上下文。"
    )

    def __init__(
        self,
        *,
        recipe_registry: Optional[Any] = None,
        ledger: Optional[ArtifactLedger] = None,
        **base_kwargs: Any,
    ) -> None:
        super().__init__(**base_kwargs)
        self._recipe_registry = recipe_registry
        self._ledger = ledger

    # ── compose（ADR-0189 D1 七步流水线）─────────────────────

    def compose(self, request: Dict[str, Any]) -> MapSpecDeliveryRef:
        """多字段 GeoJSON → 最优分类/色彩裁决 → 规范化 MapSpec 交付券。"""
        self.heartbeat("survey")
        self.check_deadline()
        request = dict(request or {})
        geojson = request.get("geojson")
        field = request.get("field")
        title = request.get("title")
        if (
            not isinstance(geojson, dict)
            or not isinstance(geojson.get("features"), list)
        ):
            raise ValueError("compose 请求缺少 geojson.features（fail-loud）")
        if not field or not title:
            raise ValueError("compose 请求缺少 field / title（fail-loud）")
        source_id = str(request.get("source_id") or "delivery")
        warnings: List[str] = []

        purpose = str(request.get("purpose") or "screen_16_9")
        if purpose not in OUTPUT_PURPOSES:
            warnings.append(
                f"purpose_unmapped: {purpose!r} 不在输出用途词表，按 screen_16_9 处理"
            )
            purpose = "screen_16_9"

        survey = _survey_geojson(geojson, field)
        self.heartbeat("symbology")

        classification: Dict[str, Any] = {}
        legend_spec: Optional[Dict[str, Any]] = None
        thematic = False

        if survey["categories"]:
            # 类别分支：定性色板 + categorical 图例。
            cats = sorted(set(survey["categories"]))
            from app.lib.cartography.palettes import resolve_palette_colors

            qualitative = resolve_palette_colors("Set1")
            entries = [
                {"key": key, "color": qualitative[i % len(qualitative)], "label": key}
                for i, key in enumerate(cats)
            ]
            legend_spec = build_categorical_spec(
                field, entries, palette="Set1", title=str(title),
            )
            if legend_spec is not None:
                thematic = True
                classification = {
                    "field": field,
                    "method": "categorical",
                    "k": len(entries),
                    "palette": "Set1",
                    "rejected_methods": [],
                }
        else:
            # 数值分支：分布驱动裁决 → symbology 唯一裁决 → graduated 图例。
            values = survey["values"]
            stats = distribution_stats_from_values(values)
            if stats is not None:
                choice = choose_classification(
                    stats,
                    recommended=list(DEFAULT_RECOMMENDED_CLASSIFIERS),
                    requested_method=request.get("requested_method"),
                    requested_k=request.get("requested_k"),
                )
                from app.lib.cartography.symbology import (
                    symbology_decision_from_values,
                )

                decision = symbology_decision_from_values(
                    values,
                    requested_method=choice.method,
                    requested_k=choice.k,
                    requested_palette=request.get("requested_palette"),
                )
                legend_spec = build_graduated_spec(
                    geojson, field, decision=decision, title=str(title),
                )
                classification = {
                    "field": field,
                    "method": decision.method,
                    "k": decision.k,
                    "palette": decision.palette,
                    "rejected_methods": [
                        r.get("method") for r in choice.rejected if r.get("method")
                    ],
                }
            if legend_spec is None:
                warnings.append(
                    "classification_unavailable: 可分级数值不足，"
                    "未产出专题分级（诚实降级为常量底色）"
                )
            else:
                thematic = True
        self.heartbeat("layout")

        # ── 版面整饰：必配基线 + V4 防撞自愈 ──
        plan = required_components_for(purpose, {
            "has_thematic_layer": thematic,
            "has_data_source": True,
        })
        intent = {t: z for t, z, _ in _COMPONENT_ZONE_INTENT}
        priority = {t: p for t, _, p in _COMPONENT_ZONE_INTENT}
        participants = [
            LayoutParticipantV4(
                id=f"comp-{req.type}",
                type=req.type,
                requested_zone=intent.get(req.type, "none"),
                priority=priority.get(req.type, 50),
                optional=False,
            )
            for req in plan.required
        ]
        solution = solve_layout_v4(participants, page_profile="viewport")
        for placement in solution.suppressed:
            warnings.append(
                f"布局抑制: 组件 {placement.id}（{placement.type}）未能落位"
                f"（{placement.reason}）"
            )
        for step in solution.repair_steps:
            warnings.append(
                f"布局自愈: {step.action} {step.component_id}"
                f" {step.from_zone}->{step.to_zone}（{step.reason}）"
            )

        components: List[Dict[str, Any]] = []
        for placement in solution.placements:
            position = (
                placement.zone if placement.zone in _MAPSPEC_POSITIONS else "none"
            )
            components.append({
                "id": placement.id,
                "type": placement.type,
                "enabled": True,
                "position": position,
                "priority": priority.get(placement.type, 50),
                "placement": {"mode": "anchor", "anchor": position},
            })
        legend_position = solution.zone_for("comp-legend")
        self.heartbeat("normalize")

        # ── 组装 + 规范化闸 ──
        bbox = survey["bbox"] or [100.0, 30.0, 105.0, 35.0]
        center = [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0]
        paint, paint_warnings = spec_to_paint(legend_spec)
        warnings.extend(paint_warnings[:2])
        layer: Dict[str, Any] = {
            "id": "thematic-main",
            "source": source_id,
            "type": "fill",
            "visible": True,
        }
        layer["paint"] = (
            {"fill-color": paint} if paint is not None
            else {"fill-color": _FALLBACK_COLOR}
        )
        if legend_spec is not None:
            layer["legend_spec"] = legend_spec

        mapspec: Dict[str, Any] = {
            "version": "1.2",
            "view": {"center": center, "zoom": 10},
            "sources": {
                source_id: {
                    "type": "geojson",
                    "inlineData": geojson,
                    "profile": _build_source_profile(survey, field),
                },
            },
            "layers": [layer],
            "layout": {
                "legend": {
                    "title": str(title),
                    "position": (
                        legend_position if legend_position in _MAPSPEC_POSITIONS
                        else "top-right"
                    ),
                    "visible": True,
                },
                "purpose": purpose,
                "components": components,
            },
            "thresholds": {},
        }
        payload = canonicalize_mapspec(mapspec)
        self.heartbeat("emit")

        # ── 配方先验检索（只披露，不覆盖分布证据）──
        hint = request.get("recipe_hint")
        if hint and self._recipe_registry is not None:
            recipe = self._recipe_registry.get(str(hint))
            if recipe is None:
                hits = self._recipe_registry.keyword_hits(str(hint))
                recipe = hits[0] if hits else None
            if recipe is not None:
                warnings.append(
                    f"recipe_prior: 命中配方 {recipe.id}"
                    f"（primary={recipe.primary_cartography}，仅作先验披露）"
                )
            else:
                warnings.append(f"recipe_miss: 未命中配方 {hint!r}")

        return self._emit_ref(payload, classification, warnings, revision=0)

    # ── revise（对抗回路修复面，ADR-0189 D4）────────────────

    def revise(
        self,
        delivery: MapSpecDeliveryRef,
        audit_report: Any = None,
    ) -> MapSpecDeliveryRef:
        """只执行审计红线驱动的 AUTO_SAFE 修复（review_and_repair 语义）。"""
        self.heartbeat("revise")
        self.check_deadline()
        if self._ledger is None:
            raise ValueError("revise 需要 ArtifactLedger（ref 取货位）")
        payload = self._ledger.get(delivery.ref_id)
        if payload is None:
            raise ValueError(
                f"revise 目标不可达: {delivery.ref_id!r}（账本无此券，fail-closed）"
            )
        result = review_and_repair_cartography(payload, max_iterations=2)
        warnings = list(delivery.warnings)
        if result.repair_count == 0:
            warnings.append(
                "revise_no_op: 审计建议未产生可安全自动执行的修复"
                f"（termination={result.termination_reason or 'none'}）"
            )
        for attempt in result.attempts:
            for op in attempt.get("applied", [])[:4] if isinstance(attempt, dict) else []:
                warnings.append(f"revise_applied: {op}")
        return self._emit_ref(
            result.mapspec,
            dict(delivery.classification),
            warnings,
            revision=delivery.revision + 1,
        )

    # ── 出券 ────────────────────────────────────────────────

    def _emit_ref(
        self,
        payload: Dict[str, Any],
        classification: Dict[str, Any],
        warnings: List[str],
        *,
        revision: int,
    ) -> MapSpecDeliveryRef:
        fingerprint = cartographic_fingerprint(payload)
        legend_cfg = (payload.get("layout") or {}).get("legend") or {}
        components = [
            str(c.get("type"))
            for c in (payload.get("layout") or {}).get("components", [])
            if c.get("type")
        ]
        layer_count = len(payload.get("layers", []))
        bounded_warnings = [
            w[:_MAX_DELIVERY_WARNING_LEN] for w in warnings[:8]
        ]
        summary = (
            f"thematic map: {layer_count} layer(s), "
            f"classification={classification.get('method')!r}"
            f"(k={classification.get('k')}), "
            f"legend={'on' if legend_cfg.get('visible', True) else 'off'}"
        )[:_MAX_DELIVERY_SUMMARY_LEN]
        ref_id: Optional[str] = None
        if self._ledger is not None:
            ref_id = self._ledger.allocate_ref("ref:mapspec")
            self._ledger.put(ref_id, payload)
        return MapSpecDeliveryRef(
            ref_id=ref_id,
            mapspec_fingerprint=fingerprint,
            digest=payload_digest(payload),
            revision=revision,
            layer_count=layer_count,
            legend_visible=legend_cfg.get("visible", True),
            classification=classification,
            components=components[:12],
            warnings=bounded_warnings,
            summary=summary,
            truncated=len(warnings) > 8,
        )


__all__ = [
    "DEFAULT_RECOMMENDED_CLASSIFIERS",
    "CartographerAgent",
]
