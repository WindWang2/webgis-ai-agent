"""Product Runtime —— 语义产品层的生产编排（ADR-0183 §8 接线面）。

职责：把纯函数层（shapes → compiler → completeness）接到生产工具路径：

- `load_chapter_product_spec()`：SessionPlan chapter → 已持久化 spec（容错）；
- `merge_spec_with_replay()`：**编辑存活语义** —— webgis_map_product 重放
  重建 plan 时，用户对既有 spec 的显式编辑（overrides / 移除的视图 / 关闭的
  组件族）不被覆盖；fresh 侧只回填绑定 refs、模板指针与新增视图；
- `produce_product_layer()`：组装 additive 结果键
  （product_spec / product_views / product_completeness / product_compile_fallback），
  供 webgis_map_product / webgis_product_edit 结果携带。

不变式：本模块不做物理 MapSpec 写入（那仍是组装工具经 mapspec_store 通道的
职责）；零 LLM；有界输出；任何失败降级为 result 键缺席 + fallback 披露，
绝不阻断既有组装路径。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.gis_harness.product_completeness import (
    validate_product_completeness,
)
from app.services.gis_harness.product_compiler import compile_product_spec
from app.services.gis_harness.product_shapes import build_product_spec_from_plan
from app.services.gis_harness.product_spec import (
    MAX_RELATIONS,
    MAX_VIEWS,
    MapProductSpec,
    spec_digest,
    spec_from_storage,
    storage_payload,
    validate_product_spec,
)

logger = logging.getLogger(__name__)

_MAX_VIEW_PROJECTIONS = 12


def load_chapter_product_spec(chapter: Any) -> Optional[MapProductSpec]:
    """chapter["product_spec"] → spec（损坏/缺版本 → None，绝不抛出）。"""
    if not isinstance(chapter, dict):
        return None
    return spec_from_storage(chapter.get("product_spec"))


def _same_product_lineage(existing: MapProductSpec, fresh: MapProductSpec) -> bool:
    """同一产品的判定：同 query 且同 task（计划重放/重组装不换语义）。"""
    return bool(
        existing.query
        and existing.query == fresh.query
        and existing.task == fresh.task
    )


def _retracted_view_ids(existing: MapProductSpec) -> set:
    """既有 spec 中被用户显式移除的视图 id（override 账）。"""
    return {
        ov.target for ov in existing.overrides
        if ov.op == "remove_view" and ov.target
    }


def merge_spec_with_replay(
    existing: MapProductSpec,
    fresh: MapProductSpec,
) -> MapProductSpec:
    """重组装合并：existing（用户编辑过的语义）为主，fresh 回填事实。

    - existing 的 views/relations/overrides/enabled/required 原样保留
      （编辑存活）；
    - fresh 新增视图（如本轮 output_intents 新点了 chart）在未被显式移除时
      追加；
    - 绑定 refs / 模板指针 / plan_id / claims / delivery（未被 set_delivery
      覆盖时）从 fresh 刷新 —— 这些是"事实回填"，不是语义改写。
    """
    merged = existing.model_copy(deep=True)
    retracted = _retracted_view_ids(existing)

    fresh_by_id = {v.view_id: v for v in fresh.views}
    for view in merged.views:
        fw = fresh_by_id.get(view.view_id)
        if fw is None:
            continue
        # 事实回填：绑定 refs（dataset/analysis/layer hint）。用户显式设置过
        # filter 的视图保留其 filter（编辑存活）。
        view.binding.dataset_ref = fw.binding.dataset_ref or view.binding.dataset_ref
        view.binding.analysis_ref = fw.binding.analysis_ref or view.binding.analysis_ref
        view.binding.layer_hint = fw.binding.layer_hint or view.binding.layer_hint

    existing_ids = {v.view_id for v in merged.views}
    for fw in fresh.views:
        if fw.view_id not in existing_ids and fw.view_id not in retracted:
            if len(merged.views) < MAX_VIEWS:
                merged.views.append(fw.model_copy(deep=True))
    # fresh 的关系边在两端齐备且未超界时补上（保持图完整， relations 有界）
    merged_relations = {(r.src, r.dst, r.kind) for r in merged.relations}
    for r in fresh.relations:
        key = (r.src, r.dst, r.kind)
        ids = {v.view_id for v in merged.views}
        if (
            r.src in ids and r.dst in ids and key not in merged_relations
            and len(merged.relations) < MAX_RELATIONS
        ):
            merged.relations.append(r.model_copy(deep=True))
            merged_relations.add(key)

    # 事实回填：模板指针 / plan_id / claims / delivery（无 set_delivery override 时）
    merged.recipe_id = fresh.recipe_id or merged.recipe_id
    merged.template_id = fresh.template_id or merged.template_id
    merged.composition_template_id = (
        fresh.composition_template_id or merged.composition_template_id)
    merged.plan_id = fresh.plan_id or merged.plan_id
    merged.claims = list(fresh.claims) or merged.claims
    if not any(ov.op == "set_delivery" for ov in merged.overrides):
        merged.delivery = fresh.delivery.model_copy(deep=True)
    return merged


def _view_projection(spec: MapProductSpec) -> List[Dict[str, Any]]:
    """有界视图投影（工具结果披露面）。"""
    out: List[Dict[str, Any]] = []
    for v in spec.views[:_MAX_VIEW_PROJECTIONS]:
        out.append({
            "view_id": v.view_id,
            "kind": v.kind,
            "role": v.role,
            "enabled": v.enabled,
            "required": v.required,
            "chart_kind": v.chart_kind,
            "title": v.title[:80],
            "dataset_ref": v.binding.dataset_ref[:64],
        })
    return out


def produce_product_layer(
    *,
    plan: Any,
    intent: Any,
    template: Any = None,
    existing_spec: Optional[MapProductSpec] = None,
    primary_ref: str = "",
) -> Dict[str, Any]:
    """语义产品层结果键组装（纯函数核 + 可选既有 spec 合并）。

    返回 additive 键 dict；编译/校验失败 → 键降级为 fallback 披露，
    绝不抛出（组装路径的增值面，不是依赖面）。
    """
    try:
        fresh = build_product_spec_from_plan(plan, intent, template)
        # 绑定事实：primary_ref（工具侧已解析的授权主数据）
        for v in fresh.views:
            if v.kind == "map" and primary_ref and not v.binding.dataset_ref:
                v.binding.dataset_ref = primary_ref
        _merged_from_existing = (
            existing_spec is not None
            and _same_product_lineage(existing_spec, fresh)
        )
        spec = (
            merge_spec_with_replay(existing_spec, fresh)
            if _merged_from_existing
            else fresh
        )
        errors = validate_product_spec(spec)
        if errors:
            return {
                "product_compile_fallback": {
                    "code": "product_spec_invalid",
                    "errors": [str(e)[:120] for e in errors[:4]],
                },
            }
        compile_result = compile_product_spec(spec, plan=plan, template=template)
        completeness = validate_product_completeness(
            spec, compile_result=compile_result)
        out: Dict[str, Any] = {
            "product_spec": storage_payload(spec),
            "product_views": _view_projection(spec),
            "product_compile": {
                "compile_digest": compile_result.compile_digest,
                "spec_digest": compile_result.spec_digest,
                "composition_template_id": compile_result.composition_template_id,
                "chart_requirements": [
                    c.model_dump() for c in compile_result.chart_requirements[:6]],
                "fallbacks": list(compile_result.fallbacks)[:6],
                "decisions": list(compile_result.decisions)[:6],
            },
            "product_completeness": completeness.to_dict(),
        }
        if _merged_from_existing:
            # CAS 基线（review P1）：session_plan merge 侧据此检测并发覆盖。
            out["base_spec_digest"] = spec_digest(existing_spec)  # type: ignore[arg-type]
        return out
    except Exception as exc:  # noqa: BLE001 — 增值面降级，不阻断组装
        logger.exception("[product-runtime] product layer degraded")
        return {
            "product_compile_fallback": {
                "code": "product_layer_error",
                "detail": str(exc)[:160],
            },
        }


__all__ = [
    "load_chapter_product_spec",
    "merge_spec_with_replay",
    "produce_product_layer",
]
