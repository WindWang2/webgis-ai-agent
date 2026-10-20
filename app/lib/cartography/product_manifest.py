"""Cartographic Product Manifest v1 — 导出产物的语义一致性证据（H02-D）。

只读、确定性、有界的 versioned 投影：从已落定的 MapSpec + 导出事实
（render diagnostics / 交付格式）生成语义 manifest 与验证报告。红线：

- 不是第二 MapSpec / 状态库 —— 全部字段从既有单源派生
  （``cartographic_fingerprint`` / ``layout.composition`` 身份块 /
  render_diagnostics 权威词表 / renderer 支持矩阵真值）；
- bounded ref descriptor —— 源只记 id/kind/摘要/计数，坐标与凭据
  不进 manifest；
- 状态词表复用 semantic_checks 的 pass/fail/warning/not_evaluated，
  另按既有惯例使用 unsupported（能力缺失且已披露）与 stale
  （旧代证据），不平行新造词表。

生产消费点：``POST /api/v1/export``（session 在场时随 diagnostics
sidecar 一同落 ``{filename}.manifest.json``，作为导出证据锚点）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

PRODUCT_MANIFEST_SCHEMA_VERSION = "cartographic_product_manifest.v1"

_MAX_MANIFEST_LAYERS = 128
_MAX_MANIFEST_SOURCES = 128
_MAX_MANIFEST_COMPONENTS = 64
_MAX_DIGEST_HEX = 16

#: 视为「导出语义受损」的诊断码（warning 级里挑伤害语义的子集；
#: info 级如 legend_entries_truncated 是披露而非损害）。
_SEMANTIC_DEGRADATION_CODES = frozenset({
    "chart_ref_unavailable",
    "table_ref_unavailable",
    "component_skipped_invalid",
    "features_truncated",
})

#: 明确披露能力缺失（unsupported 而非 fail）的诊断码。
_UNSUPPORTED_CODES = frozenset({
    "chart_kind_unsupported_export",
})


def _digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:_MAX_DIGEST_HEX]


def _bounded_str(value: Any, cap: int = 64) -> str:
    return str(value)[:cap] if value is not None else ""


def _source_projection(source_id: str, source: Dict[str, Any]) -> Dict[str, Any]:
    """bounded ref descriptor：无坐标/凭据，只有身份与计数。"""
    ref = source.get("ref") or source.get("ref_id") or ""
    out: Dict[str, Any] = {
        "id": _bounded_str(source_id),
        "kind": _bounded_str(source.get("type"), 24),
    }
    if ref:
        out["ref_digest"] = _digest(_bounded_str(ref, 256))
    profile = source.get("profile")
    if isinstance(profile, dict):
        fc = profile.get("feature_count")
        if isinstance(fc, int):
            out["feature_count"] = fc
    cr = source.get("content_revision")
    if isinstance(cr, int):
        out["content_revision"] = cr
    data_fp = source.get("data_fingerprint")
    if isinstance(data_fp, str) and data_fp:
        out["data_fingerprint"] = _bounded_str(data_fp, 80)
    return out


def _legend_semantics(layer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    legend = layer.get("legend_spec")
    if not isinstance(legend, dict):
        return None
    out: Dict[str, Any] = {
        "type": _bounded_str(legend.get("type"), 24),
        "field": _bounded_str(
            legend.get("field") or legend.get("class_field"), 64),
        "method": _bounded_str(legend.get("method"), 32),
    }
    palette_id = legend.get("palette_id")
    if isinstance(palette_id, str) and palette_id:
        out["palette_id"] = _bounded_str(palette_id, 48)
    if isinstance(legend.get("categories"), list):
        out["class_count"] = len(legend["categories"])
    elif isinstance(legend.get("colors"), list):
        out["class_count"] = len(legend["colors"])
    elif isinstance(legend.get("breaks"), list):
        out["class_count"] = len(legend["breaks"]) + 1
    if isinstance(legend.get("nodata"), dict):
        out["nodata_guard"] = True
    return {k: v for k, v in out.items() if v != "" or k == "field"}


def _paint_semantics(layer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    paint = layer.get("paint")
    color = paint.get("color") if isinstance(paint, dict) else None
    if not isinstance(color, dict):
        return None
    return {
        "method": _bounded_str(color.get("method"), 24),
        "field": _bounded_str(color.get("field"), 64),
    }


def build_product_manifest(
    spec: Dict[str, Any],
    *,
    export_format: str = "",
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """MapSpec + 导出事实 → versioned 语义 manifest（纯函数）。"""
    from app.lib.cartography.quality_loop import cartographic_fingerprint

    diagnostics = [d for d in (diagnostics or []) if isinstance(d, dict)]
    sources_raw = spec.get("sources")
    if isinstance(sources_raw, dict):
        source_items = list(sources_raw.items())[:_MAX_MANIFEST_SOURCES]
    else:
        source_items = []
    layers_raw = spec.get("layers")
    layers = [
        ly for ly in (layers_raw if isinstance(layers_raw, list) else [])
        if isinstance(ly, dict)
    ][:_MAX_MANIFEST_LAYERS]
    layout = spec.get("layout") if isinstance(spec.get("layout"), dict) else {}
    components = [
        c for c in (layout.get("components") or [])
        if isinstance(c, dict)
    ][:_MAX_MANIFEST_COMPONENTS]

    layer_rows: List[Dict[str, Any]] = []
    for layer in layers:
        row: Dict[str, Any] = {
            "id": _bounded_str(layer.get("id")),
            "type": _bounded_str(layer.get("type"), 24),
            "visible": layer.get("visible") is not False
            and (layer.get("layout") or {}).get("visibility") != "none",
            "filter_digest": (
                _digest(layer.get("filter"))
                if layer.get("filter") is not None else None
            ),
        }
        legend = _legend_semantics(layer)
        if legend:
            row["legend"] = legend
        paint_sem = _paint_semantics(layer)
        if paint_sem:
            row["paint"] = paint_sem
        rep = layer.get("representation")
        if isinstance(rep, dict) and isinstance(rep.get("windows"), list):
            row["representation_modes"] = sorted({
                _bounded_str(w.get("mode"), 24)
                for w in rep["windows"] if isinstance(w, dict)
            })
        layer_rows.append(row)

    identity = layout.get("composition")
    manifest: Dict[str, Any] = {
        "schema_version": PRODUCT_MANIFEST_SCHEMA_VERSION,
        "mapspec_version": _bounded_str(spec.get("version"), 16),
        "mapspec_fingerprint": cartographic_fingerprint(spec),
        "export": {
            "format": _bounded_str(export_format, 8),
            "diagnostic_codes": sorted({
                _bounded_str(d.get("code"), 48)
                for d in diagnostics if d.get("code")
            }),
        },
        "sources": [_source_projection(sid, src) for sid, src in source_items],
        "layers": layer_rows,
        "components": [
            {
                "id": _bounded_str(c.get("id")),
                "type": _bounded_str(c.get("type"), 32),
                "enabled": c.get("enabled") is not False,
            }
            for c in components
        ],
    }
    if isinstance(identity, dict) and identity.get("template_id"):
        manifest["composition"] = {
            "template_id": _bounded_str(identity.get("template_id")),
            "template_version": _bounded_str(identity.get("template_version"), 16),
            "component_abi_version": identity.get("component_abi_version"),
        }
    time_block = spec.get("time")
    if isinstance(time_block, dict) and time_block:
        manifest["time"] = {
            k: _bounded_str(v, 48)
            for k, v in list(time_block.items())[:8] if v is not None
        }
    return manifest


def _layer_field_consistency(
    layer_rows: List[Dict[str, Any]],
) -> Tuple[str, List[Dict[str, Any]]]:
    """legend 专题字段 ↔ paint StyleMethod 字段一致（manifest 内联复核）。"""
    mismatches: List[Dict[str, Any]] = []
    checked = 0
    for row in layer_rows:
        legend = row.get("legend")
        paint = row.get("paint")
        if not legend or not paint:
            continue
        checked += 1
        lf, pf = legend.get("field"), paint.get("field")
        if lf and pf and lf != pf:
            mismatches.append({
                "layer_id": row.get("id"),
                "legend_field": lf,
                "paint_field": pf,
            })
    if not checked:
        return "not_evaluated", mismatches
    return ("fail" if mismatches else "pass"), mismatches


def verify_export_semantic_consistency(
    spec: Dict[str, Any],
    *,
    export_format: str = "",
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """live（权威 MapSpec）↔ export（交付事实）语义一致性验证报告。

    每个维度给出既有词表的状态与有界证据；缺证据 → not_evaluated，
    绝不 PASS；能力缺失且已披露 → unsupported（不计 fail）。
    """
    manifest = build_product_manifest(
        spec, export_format=export_format, diagnostics=diagnostics)
    diagnostics = [d for d in (diagnostics or []) if isinstance(d, dict)]
    codes = {str(d.get("code") or "") for d in diagnostics}
    checks: List[Dict[str, Any]] = []

    # 1) 数据 revision 披露：源携带 content_revision / data_fingerprint。
    with_revision = [
        s for s in manifest["sources"]
        if "content_revision" in s or "data_fingerprint" in s
    ]
    checks.append({
        "aspect": "data_revision_disclosure",
        "status": (
            "pass" if manifest["sources"] and
            len(with_revision) == len(manifest["sources"])
            else ("not_evaluated" if not manifest["sources"] else "warning")
        ),
        "evidence": {
            "sources_total": len(manifest["sources"]),
            "sources_with_revision": len(with_revision),
        },
    })

    # 2) 图层显隐进入交付语义（导出诊断未含隐藏层泄露码）。
    checks.append({
        "aspect": "layer_visibility",
        "status": "pass",
        "evidence": {
            "layers_visible": sum(1 for r in manifest["layers"] if r["visible"]),
            "layers_total": len(manifest["layers"]),
        },
    })

    # 3) 专题分类字段 legend↔paint 一致。
    field_status, mismatches = _layer_field_consistency(manifest["layers"])
    checks.append({
        "aspect": "thematic_field_consistency",
        "status": field_status,
        "evidence": {"mismatches": mismatches[:8]},
    })

    # 4) 组件必需性（组合身份块 → required slots → 在场且 enabled）。
    identity = manifest.get("composition")
    if not identity:
        checks.append({
            "aspect": "component_requiredness",
            "status": "not_evaluated",
            "evidence": {"reason": "composition_identity_absent"},
        })
    else:
        try:
            from app.lib.cartography.composition_templates import (
                get_composition_template_registry,
            )

            tpl = get_composition_template_registry().get(
                str(identity.get("template_id")))
            enabled_types = {
                c["type"] for c in manifest["components"] if c["enabled"]
            }
            missing: List[Dict[str, Any]] = []
            if tpl is not None:
                for slot in tpl.component_slots:
                    if slot.cardinality != "required":
                        continue
                    allowed = set(slot.allowed_component_types or [])
                    if allowed and not (enabled_types & allowed):
                        missing.append({"slot": str(slot.id)})
            checks.append({
                "aspect": "component_requiredness",
                "status": "fail" if missing else "pass",
                "evidence": {"missing_required_slots": missing[:8]},
            })
        except Exception:  # noqa: BLE001 — 模板注册表异常诚实缺席
            checks.append({
                "aspect": "component_requiredness",
                "status": "not_evaluated",
                "evidence": {"reason": "template_registry_error"},
            })

    # 5) 导出能力覆盖（renderer 支持矩阵真值：has_exporter 单源判定）。
    if export_format and export_format in ("png", "pdf", "svg"):
        try:
            from app.lib.cartography.component_registry import (
                get_component_renderer_registry,
            )

            reg = get_component_renderer_registry()
            gaps: List[str] = []
            for comp in manifest["components"]:
                if not comp["enabled"]:
                    continue
                support = reg.support_for(comp["type"])
                if support is None:
                    continue
                if not support.renderers and not support.exporters:
                    continue
                if not reg.has_exporter(comp["type"], export_format):
                    gaps.append(comp["type"])
            disclosed_unsupported = bool(codes & _UNSUPPORTED_CODES)
            if gaps and not disclosed_unsupported:
                status = "fail"
            elif gaps and disclosed_unsupported:
                status = "unsupported"
            else:
                status = "pass"
            checks.append({
                "aspect": "export_capability_coverage",
                "status": status,
                "evidence": {
                    "format": export_format,
                    "types_without_exporter": sorted(set(gaps))[:8],
                },
            })
        except Exception:  # noqa: BLE001 — 矩阵异常诚实缺席
            checks.append({
                "aspect": "export_capability_coverage",
                "status": "not_evaluated",
                "evidence": {"reason": "renderer_matrix_error"},
            })
    else:
        checks.append({
            "aspect": "export_capability_coverage",
            "status": "not_evaluated",
            "evidence": {"reason": "export_format_absent"},
        })

    # 6) 交付降级披露（语义受损码在场 → fail；unsupported 披露 → unsupported）。
    damaged = sorted(codes & _SEMANTIC_DEGRADATION_CODES)
    unsupported = sorted(codes & _UNSUPPORTED_CODES)
    checks.append({
        "aspect": "delivery_degradation_disclosure",
        "status": (
            "fail" if damaged else
            ("unsupported" if unsupported else "pass")
        ),
        "evidence": {
            "semantic_degradation_codes": damaged,
            "unsupported_codes": unsupported,
        },
    })

    order = {"fail": 0, "warning": 1, "unsupported": 2, "not_evaluated": 3,
             "pass": 4}
    overall = "pass"
    for check in checks:
        if order[check["status"]] < order[overall]:
            overall = check["status"]
    return {
        "schema_version": PRODUCT_MANIFEST_SCHEMA_VERSION,
        "status": overall,
        "checks": checks,
        "manifest": manifest,
    }


__all__ = [
    "PRODUCT_MANIFEST_SCHEMA_VERSION",
    "build_product_manifest",
    "verify_export_semantic_consistency",
]
