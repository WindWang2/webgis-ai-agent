"""Atlas manifest —— 图册级聚合 provenance（atlas_publication_manifest.v1）。

定位（对齐 #1755 的单页 Cartographic Product Manifest v1，绝不重造单页语义）：

- **图册级**字段：页序/目录身份、driver/scope/可比性披露、输入指纹
  （committed spec 结构指纹 + PublicationIR 指纹 + 编译器键）、QA 门结果；
- **每页**字段：数据绑定身份（过滤描述符 + 要素计数）、分类摘要、页指纹、
  单页 product_manifest v1 的 digest（每页按其有效文档独立构建后取摘要——
  图册证据锚定到既有单图语义清单，而非平行新造）。

红线：bounded（页 ≤21、digest ≤40hex、字符串截断）；无坐标载荷/凭据；
``simulated_data`` 必须显式（生产缺省 false；合成 fixture 测试置 true）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lib.cartography.atlas_job_spec import AtlasJobSpec, ClassificationScope
from app.lib.cartography.plan_ir import digest_of
from app.lib.cartography.product_manifest import build_product_manifest

ATLAS_MANIFEST_SCHEMA = "atlas_publication_manifest.v1"

_MAX_PAGES = 21  # MAX_ATLAS_PAGES + 封面


def _bounded_str(value: Any, cap: int) -> str:
    return str(value)[:cap] if value is not None else ""


def _page_product_manifest_digest(
    page_effective_doc: Dict[str, Any], diagnostics: List[Dict[str, Any]],
) -> str:
    """每页单图语义清单（#1755 v1）→ canonical digest（不外泄整包，锚定证据）。"""
    try:
        manifest = build_product_manifest(
            page_effective_doc, export_format="pdf", diagnostics=diagnostics)
    except Exception:  # noqa: BLE001 — 单页清单构建失败不阻断图册（digest 置空披露）
        return ""
    return "prodman-sha256:" + digest_of(manifest)[:40]


def build_atlas_manifest(
    *,
    spec: AtlasJobSpec,
    plan: Any,
    page_records: List[Dict[str, Any]],
    qa_report: Any,
    job_meta: Dict[str, Any],
    artifacts: Dict[str, Optional[str]],
    simulated_data: bool = False,
) -> Dict[str, Any]:
    """图册 manifest（纯函数；输入均已落定的事实，不做二次计算/IO）。

    ``page_records``：executor 的页终态记录（含 effective_doc 的
    product_manifest digest 与渲染诊断 dict——由 executor 预备，本函数保持纯）。
    """
    scope = spec.classification_scope
    binding_by_id = {b.page_id: b for b in plan.bindings}
    pages_out: List[Dict[str, Any]] = []
    for rec in page_records[:_MAX_PAGES]:
        binding = binding_by_id.get(str(rec.get("page_id")))
        if binding is None:
            continue
        classification = dict(binding.classification or {})
        classification["scope"] = scope.value
        pages_out.append({
            "page_id": _bounded_str(binding.page_id, 120),
            "title": _bounded_str(binding.title, 200),
            "page_number": binding.page_number,
            "page_count": binding.page_count,
            "cover": binding.cover,
            "bounds": [round(v, 6) for v in binding.bounds] if binding.bounds else None,
            "data": {
                "filter_layer_id": _bounded_str(binding.filter_layer_id, 120),
                "filter_property": _bounded_str(binding.filter_property, 120),
                "filter_value": _bounded_str(binding.filter_value, 160),
                "feature_offset": binding.feature_offset,
                "feature_limit": binding.feature_limit,
                "feature_count": binding.feature_count,
            },
            "classification": classification,
            "legend_digest": binding.legend_digest,
            "page_fingerprint": binding.fingerprint,
            "product_manifest_digest": _bounded_str(rec.get("product_manifest_digest"), 60),
            "render_diagnostics": list(rec.get("diagnostics") or [])[:16],
            "degradations": list(rec.get("degradations") or [])[:8],
        })
    manifest: Dict[str, Any] = {
        "schema": ATLAS_MANIFEST_SCHEMA,
        "generated_at": _bounded_str(job_meta.get("generated_at"), 40),
        "job_id": job_meta.get("job_id"),
        "attempt": job_meta.get("attempt"),
        "atlas": {
            "driver": spec.atlas.driver,
            "layer_id": _bounded_str(spec.atlas.layer_id, 120),
            "category_property": _bounded_str(spec.atlas.category_property, 120),
            "page_budget": spec.atlas.page_budget,
            "include_cover": spec.atlas.include_cover,
            "atlas_title": _bounded_str(spec.atlas.atlas_title, 160),
            "classification_scope": scope.value,
            # 科学语义：local 分级每页阈值不同，跨页直接比较是错误结论
            "cross_page_comparability": scope == ClassificationScope.shared,
        },
        "inputs": {
            "session_id": _bounded_str(spec.session_id, 255),
            "mapspec_revision": job_meta.get("mapspec_revision"),
            "spec_fingerprint": _bounded_str(job_meta.get("spec_fingerprint"), 60),
            "ir_fingerprint": plan.ir_fingerprint,
            "target_dpi": spec.target_dpi,
            "compiler_key": plan.compiler_key,
        },
        "pages": pages_out,
        "qa": qa_report.model_dump(),
        "artifacts": {
            "pdf": artifacts.get("pdf"),
            "manifest": artifacts.get("manifest"),
            "pdf_url": (f"/api/v1/export/download/{artifacts['pdf']}"
                        if artifacts.get("pdf") else None),
        },
        "disclosures": {
            "simulated_data": bool(simulated_data),
            "notes": [
                n for n in (job_meta.get("notes") or []) if isinstance(n, str)
            ][:8],
        },
    }
    return manifest


__all__ = ["ATLAS_MANIFEST_SCHEMA", "build_atlas_manifest"]
