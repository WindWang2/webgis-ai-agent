"""Golden Corpus builder — 结构化 golden 语料（ADR-0101 D8）.

确定性用例矩阵：native 模型 × 兼容组合模板 × output target × locale ×
page profile + 边界用例（planned 泄漏门、缺 context、fallback 触发、
多层绑定、极端标题）。

每个用例固化 resolve → compose → validate → solve 的**结构化 digest**
（非像素）：选型、组件实例、组合校验、布局求解。黄金文件入库受
``GOLDEN_CORPUS_UPDATE=1`` 显式刷新 —— 禁止无审查的批量快照更新。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

GOLDEN_DIR = Path(__file__).parent / "goldens"

LONG_TITLE_ZH = "成都市国土空间总体规划「三区三线」实施评估与城市更新单元划定专题图（2021—2035 年）"
LONG_TITLE_EN = "Chengdu Territorial Spatial Planning Implementation Assessment & Urban Renewal Unit Delineation Thematic Map (2021-2035)"
EXTREME_LEGEND_LABEL = "0.000012345 – 9876543210.12345（超大跨度数值分级区间标签极端用例）"


def _model_output_targets(model) -> List[str]:
    targets = [t for t in model.export_compatibility if t in ("interactive", "png", "pdf", "svg")]
    return targets or ["interactive"]


def _pick_output(template_targets: List[str], model_targets: List[str]) -> str:
    for t in ("interactive", "png", "pdf"):
        if t in template_targets and t in model_targets:
            return t
    common = [t for t in template_targets if t in model_targets]
    return common[0] if common else (template_targets[0] if template_targets else "interactive")


def build_cases() -> List[Dict[str, Any]]:
    """确定性构建用例清单（排序稳定；同库状态永远同清单）。"""
    from app.lib.cartography.composition_templates import get_composition_template_registry
    from app.lib.cartography.model_library import get_map_model_registry

    model_reg = get_map_model_registry()
    compo_reg = get_composition_template_registry()

    cases: List[Dict[str, Any]] = []

    # ── 基础矩阵：native 模型 × 兼容模板（前 3 个，确定性序）──────────
    for model_id in model_reg.native_ids():
        model = model_reg.resolve(model_id)
        candidates = [
            t for t in compo_reg.find_for_map_model(model_id)
            if not t.compatible_map_models or model_id in t.compatible_map_models
        ][:3]
        model_targets = _model_output_targets(model)
        for tpl in candidates:
            output = _pick_output(tpl.output_targets, model_targets)
            cases.append({
                "id": f"base::{model_id}::{tpl.id}::{output}",
                "kind": "base",
                "model": model_id,
                "template": tpl.id,
                "output": output,
                "profile": "viewport",
                "title": f"{model_id} 专题图",
                "subtitle": "",
                "context": [],
                "bindings": {"primary": "layer-primary"},
                "layer_models": {"layer-primary": model_id},
            })

    # ── locale / profile 变体（长标题中英 + A4 版式）───────────────────
    locale_models = [
        "administrative_choropleth", "normalized_choropleth", "diverging_choropleth",
        "risk_exposure_classes", "equity_assessment", "zoning_planning",
        "site_selection_result", "mcda_score_map", "change_comparison_map",
        "spectral_index_surface", "terrain_analytical_surface", "flow_od_arc",
    ]
    for model_id in locale_models:
        if model_reg.resolve(model_id) is None:
            continue
        for tpl_id, profile, title in [
            ("composition.government_report_map", "a4_portrait", LONG_TITLE_ZH),
            ("composition.statistical_report", "a4_landscape", LONG_TITLE_EN),
        ]:
            if compo_reg.get(tpl_id) is None:
                continue
            cases.append({
                "id": f"stress::{model_id}::{tpl_id}::{profile}",
                "kind": "stress",
                "model": model_id,
                "template": tpl_id,
                "output": "pdf",
                "profile": profile,
                "title": title,
                "subtitle": "副标题：数据口径与评价方法说明（极端长度换行压力用例 / extreme-length wrapping stress）",
                "context": ["statistics"],
                "bindings": {"primary": "layer-primary"},
                "layer_models": {"layer-primary": model_id},
            })

    # ── 多层绑定用例 ───────────────────────────────────────────────────
    multi_cases = [
        ("visual_heatmap", "composition.standard_analysis",
         {"primary": "layer-heat", "secondary": "layer-choro"},
         {"layer-heat": "visual_heatmap", "layer-choro": "administrative_choropleth"}),
        ("administrative_choropleth", "composition.statistical_map",
         {"primary": "layer-2020", "secondary": "layer-2025"},
         {"layer-2020": "administrative_choropleth", "layer-2025": "administrative_choropleth"}),
        ("flow_od_arc", "composition.flow_map_report",
         {"primary": "layer-flow", "secondary": "layer-admin"},
         {"layer-flow": "flow_od_arc", "layer-admin": "categorical_thematic"}),
    ]
    for model_id, tpl_id, bindings, layer_models in multi_cases:
        if model_reg.resolve(model_id) is None or compo_reg.get(tpl_id) is None:
            continue
        cases.append({
            "id": f"multi::{model_id}::{tpl_id}",
            "kind": "multi",
            "model": model_id,
            "template": tpl_id,
            "output": "pdf",
            "profile": "viewport",
            "title": "多层绑定用例",
            "subtitle": "",
            "context": [],
            "bindings": bindings,
            "layer_models": layer_models,
        })

    # ── planned 门（实际不变量：planned 模型层不得获得任何绑定组件实例；
    # 组合选型不得落在按 planned 模型特化的模板上 —— resolver 记因
    # model_planned 后仅 generic 兜底。通用 chrome（title/scale_bar 等
    # 与模型无关）合法在场，不是门禁对象。）────────────────────────
    for model_id in model_reg.planned_ids():
        cases.append({
            "id": f"planned-gate::{model_id}",
            "kind": "planned-gate",
            "model": model_id,
            "template": "",
            "output": "interactive",
            "profile": "viewport",
            "title": "planned 门禁用例",
            "subtitle": "",
            "context": [],
            "bindings": {"primary": "layer-primary"},
            "layer_models": {"layer-primary": model_id},
        })

    # ── 缺失可选 context / fallback 触发 ───────────────────────────────
    cases.append({
        "id": "edge::no-context::statistical_report",
        "kind": "edge",
        "model": "administrative_choropleth",
        "template": "composition.statistical_report",
        "output": "pdf",
        "profile": "a4_portrait",
        "title": "缺失统计 context 用例",
        "subtitle": "",
        "context": [],
        "bindings": {"primary": "layer-primary"},
        "layer_models": {"layer-primary": "administrative_choropleth"},
    })
    cases.append({
        "id": "edge::unknown-model::minimal_interactive",
        "kind": "edge",
        "model": "visual_heatmap",
        "template": "composition.minimal_interactive",
        "output": "interactive",
        "profile": "viewport",
        "title": "极简兜底用例",
        "subtitle": "",
        "context": [],
        "bindings": {"primary": "layer-primary"},
        "layer_models": {"layer-primary": "visual_heatmap"},
    })

    cases.sort(key=lambda c: c["id"])
    return cases


def digest_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """单用例结构化 digest：resolve → compose → validate → solve。"""
    from app.lib.cartography.composition_validation import validate_component_composition
    from app.lib.cartography.layout_solver import LayoutParticipant, solve_component_layout
    from app.services.gis_harness.component_composer import ComponentComposer
    from app.services.gis_harness.component_resolver import ComponentResolver

    sel = ComponentResolver().resolve(
        composition_template_id=case["template"],
        map_model_id=case["model"],
        output_target=case["output"],
        available_context=case["context"],
    )
    components = ComponentComposer().compose(
        sel,
        title_text=case["title"],
        subtitle_text=case["subtitle"],
        layer_bindings=case["bindings"],
        layer_model_ids=case["layer_models"],
    )
    layer_ids = list(case["bindings"].values())
    validation = validate_component_composition(
        components,
        composition_template_id=sel.composition_template_id,
        map_model_id=case["model"],
        layer_ids=layer_ids,
        output_target=case["output"],
        layer_model_ids=case["layer_models"],
    )

    required_types = set()
    if hasattr(sel, "required"):
        required_types = set()
    # optional 判定：选型里 required 槽类型 → optional=False。
    # resolver.required 记录的是槽 id 而非类型；用 composition 槽反查。
    from app.lib.cartography.composition_templates import get_composition_template_registry
    compo = get_composition_template_registry().get(sel.composition_template_id)
    required_slots = {s.id for s in (compo.component_slots if compo else []) if s.cardinality == "required"}
    for slot in (compo.component_slots if compo else []):
        if slot.cardinality == "required":
            required_types.update(slot.allowed_component_types)

    participants = [
        LayoutParticipant(
            id=c.id, type=c.type,
            requested_zone=c.position,
            priority=c.priority,
            optional=c.type not in required_types,
        )
        for c in components
    ]
    layout = solve_component_layout(participants, page_profile=case["profile"])

    return {
        "case": case["id"],
        "kind": case["kind"],
        "model": case["model"],
        "template": case["template"],
        "output": case["output"],
        "profile": case["profile"],
        "selection": {
            "composition_template_id": sel.composition_template_id,
            "selected": sorted(sel.selected),
            "required_slots": sorted(required_slots),
            "component_templates": dict(sorted(sel.component_templates.items())),
            "reason_codes": sorted(sel.reason_codes),
            "rejected_count": len(sel.rejected),
        },
        "components": [
            {
                "id": c.id,
                "type": c.type,
                "position": c.position,
                "priority": c.priority,
                "variant": c.variant,
                "templateId": c.templateId,
                "layerId": str((c.options or {}).get("layerId") or ""),
                "title": str((c.options or {}).get("text") or "") if c.type in ("title", "subtitle") else "",
            }
            for c in components
        ],
        "validation": {
            "ok": validation.ok,
            "error_codes": sorted(v.code for v in validation.errors),
            "warning_codes": sorted(v.code for v in validation.warnings),
        },
        "layout": {
            "placements": {p.id: p.zone for p in layout.placements},
            "moved": sorted(p.id for p in layout.placements if p.moved),
            "suppressed": sorted(p.id for p in layout.suppressed),
            "warning_count": len(layout.warnings),
        },
    }


def digest_sha(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def golden_path(case_id: str) -> Path:
    safe = case_id.replace("::", "__").replace("/", "_")
    return GOLDEN_DIR / f"{safe}.json"


def load_golden(case_id: str) -> Dict[str, Any] | None:
    path = golden_path(case_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_golden(case_id: str, payload: Dict[str, Any]) -> None:
    path = golden_path(case_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )
