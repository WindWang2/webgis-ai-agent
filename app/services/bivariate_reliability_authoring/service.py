"""G41 作者服务门面（WP08 / WP11）：UI 与 Pi **共用同一入口**。

* inspect：层/源身份、强版本锚、有界字段画像、当前表达与证据、共享组资格。
* preview：完整候选（canonical 图例 + paint + 派生展示副本 + 数值证据），
  **零 mutation**；任何参数/源变化 → ``candidate_digest`` 改变。
* commit：重新冻结同一代源、核对计划锚与预览指纹、单笔
  ``UpsertLayerIntent``（一次 CAS / 一个 checkpoint / 一条 provenance），
  同时原子替换 layer + source carrier；失败 nothing was written，派生 ref 清理。

不新建第二套撤销栈/MapSpec 存储/分类算法；CAS 用权威
``_cartographic_mutation_revision``，undo 由 ``lifecycle_engine`` 负责。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services.bivariate_reliability_authoring import cache
from app.services.bivariate_reliability_authoring.classify import (
    PAIR_CLASS_FIELD,
    compute_bivariate,
)
from app.services.bivariate_reliability_authoring.contract import (
    ALGORITHM_VERSION,
    AuthoringPlan,
)
from app.services.bivariate_reliability_authoring.errors import AuthoringError
from app.services.bivariate_reliability_authoring.expression import (
    AUTHORING_KEY,
    CandidateExpression,
    build_bivariate_candidate,
    build_reliability_candidate,
    expression_fingerprint,
    make_derived_fc,
)
from app.services.bivariate_reliability_authoring.reliability import (
    compute_reliability,
)
from app.services.bivariate_reliability_authoring.snapshot import (
    FrozenSource,
    check_anchor,
    freeze_source,
    source_ref_of,
)
from app.services.bivariate_reliability_authoring.stats import (
    VALUE_KINDS,
    scan_pairs,
    value_kind,
)

logger = logging.getLogger(__name__)

_VECTOR_LAYER_TYPES = {"fill", "circle", "line", "symbol"}
_MAX_FIELDS = 200
_MAX_CELLS = 9
_MAX_DISCLOSURES = 12


def _is_constant_number(value: Any) -> bool:
    """paint 值是常量数字（而非 match/interpolate 表达式或缺失）。

    ``True``/``False`` 在 Python 里也是 int，必须显式排除——布尔不是透明度。
    """
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


@dataclass
class LayerContext:
    session_id: str
    mapspec: Dict[str, Any]
    layer: Dict[str, Any]
    source_id: str
    source_entry: Dict[str, Any]

    @property
    def layer_type(self) -> str:
        return str(self.layer.get("type") or "")


async def _read_mapspec(session_id: str) -> Optional[Dict[str, Any]]:
    from app.services.mapspec_store import mapspec_store
    return await mapspec_store.get_mapspec(session_id)


async def _current_revision(session_id: str) -> int:
    """CAS 令牌 = 会话状态里的权威修订（与 G20/G33 同口径）。"""
    from app.services.session_data import session_data_manager
    getter = getattr(session_data_manager, "get_state_field", None)
    raw = await getter(session_id, "_cartographic_mutation_revision") if callable(getter) else None
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


async def resolve_layer(session_id: str, layer_id: str) -> LayerContext:
    mapspec = await _read_mapspec(session_id)
    if not isinstance(mapspec, dict):
        raise AuthoringError("NO_SESSION_MAPSPEC",
                             f"会话 {session_id!r} 没有权威 MapSpec", status_code=404)
    layer = None
    for cand in mapspec.get("layers") or []:
        if isinstance(cand, dict) and cand.get("id") == layer_id:
            layer = cand
            break
    if layer is None:
        raise AuthoringError("UNKNOWN_LAYER",
                             f"层 {layer_id!r} 不在权威 MapSpec 中", status_code=404)
    if layer.get("type") not in _VECTOR_LAYER_TYPES:
        raise AuthoringError(
            "NOT_VECTOR_LAYER",
            f"层 {layer_id!r} 类型 {layer.get('type')!r} 不是矢量层", status_code=409)
    source_id = layer.get("source")
    sources = mapspec.get("sources") or {}
    entry = sources.get(source_id) if isinstance(source_id, str) else None
    if not isinstance(entry, dict):
        raise AuthoringError("SOURCE_NOT_FOUND",
                             f"层引用的源 {source_id!r} 不存在", status_code=409)
    return LayerContext(session_id=session_id, mapspec=mapspec, layer=layer,
                        source_id=str(source_id), source_entry=entry)


def _shared_expression_scope(ctx: LayerContext) -> Optional[Dict[str, Any]]:
    """复用 G33 的产品级共享表达资格判定（不复制组计算器）。"""
    from app.services.thematic_authoring.service import (
        _shared_expression_scope as g33_scope,
    )
    # LayerContext 形状与 G33 兼容（session_id/mapspec/layer/source_id/source_entry）
    return g33_scope(ctx.mapspec, ctx)  # type: ignore[arg-type]


def _assert_not_shared_member(ctx: LayerContext, *, action: str) -> None:
    if _shared_expression_scope(ctx) is not None:
        raise AuthoringError(
            "SHARED_EXPRESSION_MEMBER",
            f"该层参与跨幅共享分级——拒绝单成员{action}（会破坏共享同值同色口径）；"
            "本任务不新建共享组计算器",
            status_code=409,
            correction_hint="在 frame_product 产品级入口原子更新整组，或先解除共享。")


def _base_ref(ctx: LayerContext) -> str:
    """后续作者始终从**基准源**冻结，避免派生字段在展示副本上累积。"""
    ev = ctx.layer.get(AUTHORING_KEY)
    if isinstance(ev, dict):
        ref = ev.get("base_source_ref")
        if isinstance(ref, str) and ref.startswith("ref:"):
            return ref
    ref = source_ref_of(ctx.source_entry)
    if not ref:
        raise AuthoringError(
            "NO_STRONG_REVISION",
            "层源不是会话 ref（纯 URL/瓦片/内联载荷没有权威单调版本）——"
            "MVT 可见瓦片、50 条选择样本或当前屏幕不能代替完整源",
            status_code=409,
            correction_hint="把数据注册为 session ref 源后再进入作者工作台。")
    return ref


async def _freeze(
    ctx: LayerContext, *, owner_token: Optional[str], trusted_dispatch: bool,
) -> FrozenSource:
    return await freeze_source(
        ctx.session_id, _base_ref(ctx),
        owner_token=owner_token, trusted_dispatch=trusted_dispatch)


def _field_candidates(features: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """有界字段画像：每个键给出显式分型计数（不猜测类型）。"""
    seen: Dict[str, Dict[str, int]] = {}
    for feat in features:
        props = feat.get("properties")
        if not isinstance(props, dict):
            continue
        for key, value in props.items():
            name = str(key)
            if name not in seen:
                if len(seen) >= _MAX_FIELDS:
                    break
                seen[name] = {k: 0 for k in VALUE_KINDS}
            seen[name][value_kind(value)] += 1
        if len(seen) >= _MAX_FIELDS:
            break
    out = []
    for name, kinds in seen.items():
        numeric = kinds.get("number", 0)
        out.append({
            "name": name,
            "numeric_count": numeric,
            "nonnumeric_count": sum(v for k, v in kinds.items() if k != "number"),
            "type": "number" if numeric and not any(
                kinds.get(k) for k in VALUE_KINDS if k != "number") else "mixed_or_unknown",
            "kind_counts": {k: v for k, v in kinds.items() if v},
        })
    return out


def _supported(ctx: LayerContext) -> Dict[str, Any]:
    t = ctx.layer_type
    return {
        "layer_type": t,
        "bivariate_matrix": t in ("fill", "circle", "line"),
        "reliability_opacity": t in ("fill", "circle", "line"),
        "reliability_stroke_width": t == "circle",
        "derived_display_copy": True,
        "note": "未支持的层/通道一律 typed 拒绝，不静默删除第二通道；不调用 G40 纹理作 fallback",
    }


async def inspect_layer(
    session_id: str, layer_id: str, *,
    owner_token: Optional[str] = None, trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """作者检查：强版本源锚 + 有界字段画像 + 当前表达/证据 + 共享组资格。"""
    ctx = await resolve_layer(session_id, layer_id)
    frozen = await _freeze(ctx, owner_token=owner_token,
                           trusted_dispatch=trusted_dispatch)
    # 有界缓存：键含源 ref / 单调版本 / 内容指纹 / 算法版本；旧源刷新不会
    # 自动沿用新统计标签而保留旧 paint。
    ck = cache.cache_key(
        ref_id=frozen.ref_id, content_revision=frozen.content_revision,
        content_fingerprint=frozen.content_fingerprint,
        plan_digest="inspect", algorithm_version=ALGORITHM_VERSION,
        fields=("__field_profile__",), units=(), measure_kind="none")
    cached = cache.get(ck)
    if cached and isinstance(cached.get("fields"), list):
        fields = cached["fields"]
    else:
        fields = _field_candidates(frozen.features)
        cache.put(ck, {"fields": fields})
    ev = ctx.layer.get(AUTHORING_KEY) if isinstance(ctx.layer.get(AUTHORING_KEY), dict) else None
    legend = ctx.layer.get("legend_spec")
    shared = _shared_expression_scope(ctx)
    receipt: Dict[str, Any] = {
        "layer_id": layer_id,
        "layer_type": ctx.layer_type,
        "source": {
            "source_id": ctx.source_id,
            "base_ref": frozen.ref_id,
            "content_revision": frozen.content_revision,
            "content_fingerprint": frozen.content_fingerprint,
            "feature_count": frozen.feature_count,
            "snapshot_guarantee": (
                "权威单调 generation 前后核对 + 同会话锁 + 全量单次深拷贝；"
                "ABA 由版本号捕获，不靠 fingerprint 相同"),
        },
        "source_anchor": frozen.anchor(),
        "current": {
            "legend_type": legend.get("type") if isinstance(legend, dict) else None,
            "fingerprint": expression_fingerprint(legend),
            "paint_color": (ctx.layer.get("paint") or {}).get("color"),
            "authoring": ev,
            "source_has_biv_class": bool(ev and ev.get("source_has_biv_class")),
        },
        "fields": fields,
        "fields_truncated": len(fields) >= _MAX_FIELDS,
        "supported": _supported(ctx),
        "measure_evidence_contract": {
            "accepted": ["standard_error", "variance", "confidence_interval",
                         "prediction_interval"],
            "requires": "逐要素字段必须与估计值在同一已授权源、同一行；"
                        "CI/PI 需 low+high+合法 level；author_declared 需方法说明",
            "not_accepted": ["sample_count", "p_value", "model_score",
                             "classification_probability", "任意常数", "LLM 推断"],
            "disclosure": "只有估计值而没有误差/区间事实时无法建立可靠性表达；"
                          "不由样本量、字段名或图形颜色合成统计信心",
        },
        "revision": await _current_revision(session_id),
        "shared_expression_scope": shared,
        "algorithm_version": ALGORITHM_VERSION,
    }
    if shared:
        receipt["notes"] = [
            "该层参与跨幅共享分级（G15 frame_product）——单成员编辑会破坏"
            "共享同值同色口径；请使用产品级入口更新整组，或先解除共享"]
    return receipt


def _compute_candidate(
    plan: AuthoringPlan,
    ctx: LayerContext,
    frozen: FrozenSource,
) -> Tuple[CandidateExpression, Dict[str, Any]]:
    """preview / commit 共用的确定性构建（不写任何状态）。"""
    anchor = frozen.anchor()
    digest = plan.plan_digest()
    if plan.mode == "bivariate_matrix":
        spec = plan.bivariate
        assert spec is not None
        scan = scan_pairs(frozen.features, spec)
        result = compute_bivariate(scan, spec)
        candidate = build_bivariate_candidate(
            spec, scan, result, frozen, anchor=anchor, plan_digest=digest,
            layer_type=ctx.layer_type, title=plan.title)
        summary = {
            "valid_pairs": scan.valid_pairs,
            "only_a_missing": scan.only_a_missing,
            "only_b_missing": scan.only_b_missing,
            "both_missing": scan.both_missing,
            "kind_counts_a": scan.kind_counts_a,
            "kind_counts_b": scan.kind_counts_b,
            "requested_classes": result.requested_classes,
            "actual_classes_a": result.actual_classes_a,
            "actual_classes_b": result.actual_classes_b,
        }
        return candidate, summary
    if plan.mode == "reliability_dual_channel":
        spec = plan.reliability
        assert spec is not None
        result = compute_reliability(frozen.features, spec)
        candidate = build_reliability_candidate(
            spec, result, frozen, anchor=anchor, plan_digest=digest,
            layer_type=ctx.layer_type, title=plan.title)
        summary = {
            "valid_estimate": result.scan.valid_estimate,
            "valid_measure": result.scan.valid_measure,
            "unknown_measure": result.scan.unknown_measure,
            "zero_measure": result.scan.zero_measure,
            "clipped": result.scan.clipped,
            "kind_counts": result.scan.kind_counts,
        }
        return candidate, summary
    raise AuthoringError("INVALID_PLAN", "该模式没有候选构建器", status_code=422)


def _bounded_receipt(
    ctx: LayerContext, plan: AuthoringPlan, frozen: FrozenSource,
    candidate: CandidateExpression, summary: Dict[str, Any], *,
    committed: bool, candidate_digest: str,
    revision: Optional[int] = None,
    mapspec_fingerprint: Optional[str] = None,
    derived_ref: Optional[str] = None,
    mutations: int = 0,
) -> Dict[str, Any]:
    legend = candidate.legend_spec
    return {
        "success": True,
        "layer_id": ctx.layer.get("id"),
        "mode": plan.mode,
        "committed": committed,
        "algorithm_version": ALGORITHM_VERSION,
        "source_anchor": frozen.anchor(),
        "plan_digest": plan.plan_digest(),
        "candidate_digest": candidate_digest,
        "current_fingerprint": expression_fingerprint(ctx.layer.get("legend_spec")),
        "candidate_fingerprint": expression_fingerprint(legend),
        "legend": {
            "type": legend.get("type"),
            "schema_version": legend.get("schema_version"),
            "title": legend.get("title"),
            "n": legend.get("n"),
            "boundary_rule": legend.get("boundary_rule"),
            "row_major": legend.get("row_major"),
            "class_field": legend.get("class_field"),
            "colors": (legend.get("colors") or [])[:_MAX_CELLS],
            "cells": (legend.get("cells") or [])[:_MAX_CELLS],
            "joint": legend.get("joint"),
            "axes": legend.get("axes"),
            "measure": legend.get("measure"),
            "channel": legend.get("channel"),
            "buckets": (legend.get("buckets") or [])[:10],
            "estimate": {
                k: legend.get("estimate", {}).get(k)
                for k in ("field", "label", "unit", "unit_declared", "method",
                          "actual_k", "breaks", "intervals")
            } if isinstance(legend.get("estimate"), dict) else None,
            "counts": legend.get("counts"),
            "missing": legend.get("missing"),
            "source": legend.get("source"),
            "approximate": legend.get("approximate"),
        },
        "stats_basis": candidate.stats_basis,
        "summary": summary,
        "disclosures": (candidate.disclosures + legend.get("disclosures", []))[:_MAX_DISCLOSURES],
        "derived": {
            "fields": list(candidate.derived_fields),
            "ref": derived_ref,
            "reversible": True,
            "source_written_back": False,
            "source_has_biv_class": candidate.source_has_biv_class,
        },
        "revision": revision if revision is not None else None,
        "mapspec_fingerprint": mapspec_fingerprint,
        "can_undo": committed,
        "mutations": mutations,
        "export": {
            "via": "existing_authorized_export_routes",
            "disclosure": "SVG/PNG/PDF 由既有出版链消费同一 canonical 图例；"
                          "下载须经会话归属校验，回执不回传完整要素或媒体字节",
        },
    }


async def preview_expression(
    session_id: str, layer_id: str, plan: AuthoringPlan, *,
    owner_token: Optional[str] = None, trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """预览：完整候选 + 零 mutation；不创建派生 ref、不推进修订。"""
    ctx = await resolve_layer(session_id, layer_id)
    _assert_not_shared_member(ctx, action="预览")
    if plan.mode == "revert":
        ev = ctx.layer.get(AUTHORING_KEY)
        # 判定「有可恢复快照」的依据是**上一代 source carrier**，而不是
        # ``previous_legend_spec`` 是否为 dict：层在作者化之前本来就可能没有
        # legend_spec，此时撤销的语义是「恢复为无图例」，而不是「无快照」。
        # 两处判定若不一致，预览会拒、提交却支持 → 撤销整体不可达（复审 P1）。
        prev_ref_raw = ev.get("previous_source_ref") if isinstance(ev, dict) else None
        if not (isinstance(ev, dict) and isinstance(prev_ref_raw, str)
                and prev_ref_raw.startswith("ref:")):
            raise AuthoringError(
                "REVERT_SNAPSHOT_INVALID",
                "没有本域作者决定前的表达快照，无法预览恢复", status_code=409,
                correction_hint="使用 MapSpec undo 回退整笔提交。")
        prev_legend = ev.get("previous_legend_spec")
        # 恢复也要绑定同一代源锚：UI/Pi 必须能拿到 candidate_digest，
        # 且源在预览后被刷新时旧恢复候选同样失效。
        frozen = await _freeze(ctx, owner_token=owner_token,
                               trusted_dispatch=trusted_dispatch)
        check_anchor(plan.source_anchor, frozen)
        return {
            "success": True, "mode": "revert", "committed": False,
            "preview_only": True,
            "source_anchor": frozen.anchor(),
            "plan_digest": plan.plan_digest(),
            "candidate_digest": plan.candidate_digest(frozen.anchor()),
            "restores": {
                "legend_type": (prev_legend.get("type")
                                if isinstance(prev_legend, dict) else None),
                "had_legend": isinstance(prev_legend, dict),
                "source_ref": prev_ref_raw,
            },
            "revision": await _current_revision(session_id),
            "disclosures": ["恢复本域作者决定前的完整表达（单步；更多历史用 undo）"],
        }
    frozen = await _freeze(ctx, owner_token=owner_token,
                           trusted_dispatch=trusted_dispatch)
    check_anchor(plan.source_anchor, frozen)
    candidate, summary = _compute_candidate(plan, ctx, frozen)
    digest = plan.candidate_digest(frozen.anchor())
    receipt = _bounded_receipt(
        ctx, plan, frozen, candidate, summary,
        committed=False, candidate_digest=digest,
        revision=await _current_revision(session_id))
    receipt["preview_only"] = True
    return receipt


def _revert_layer(ctx: LayerContext, *, origin: str, actor: str,
                  plan_digest: str, applied_at: str) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """从证据块恢复整层本域键（含 source carrier），供单笔 mutation 提交。"""
    ev = ctx.layer.get(AUTHORING_KEY)
    if not isinstance(ev, dict):
        raise AuthoringError("REVERT_SNAPSHOT_INVALID",
                             "层上没有本域作者证据，无法恢复", status_code=409)
    prev_ref = ev.get("previous_source_ref")
    if not (isinstance(prev_ref, str) and prev_ref.startswith("ref:")):
        raise AuthoringError("REVERT_SNAPSHOT_INVALID",
                             "证据块缺少基准源 ref，无法原子恢复 source carrier",
                             status_code=409)
    layer = dict(ctx.layer)
    prev_paint = ev.get("previous_paint")
    paint = dict(prev_paint) if isinstance(prev_paint, dict) else {}
    # 显式中性 opacity：阻止生命周期引擎的 durable-presentation 继承把上一模式
    # 的 reliability ``match`` 表达式重新注入恢复后的层（B30）。但**只在必要时**
    # 补：上一代表达没有 opacity 而当前是表达式才写 1.0；上一代已有常量
    # （含用户 0.4）必须原样保留，否则恢复会凭空改掉用户透明度（复审 P2）。
    cur_opacity = (ctx.layer.get("paint") or {}).get("opacity")
    if "opacity" not in paint and not _is_constant_number(cur_opacity):
        paint["opacity"] = 1.0
    layer["paint"] = paint
    prev_legend = ev.get("previous_legend_spec")
    if prev_legend is None:
        layer.pop("legend_spec", None)
    else:
        layer["legend_spec"] = dict(prev_legend) if isinstance(prev_legend, dict) else prev_legend
    prev_binding = ev.get("previous_data_binding")
    if prev_binding is None:
        layer.pop("data_binding", None)
    else:
        layer["data_binding"] = dict(prev_binding) if isinstance(prev_binding, dict) else prev_binding
    prev_biv = ev.get("previous_bivariate")
    if prev_biv is None:
        layer.pop("bivariate", None)
    else:
        layer["bivariate"] = dict(prev_biv)
    if ev.get("previous_title") is None:
        layer.pop("title", None)
    else:
        layer["title"] = ev["previous_title"]
    layer[AUTHORING_KEY] = {
        "schema_version": 1, "mode": "revert", "reverted": True,
        "decided_by": origin, "actor": actor, "plan_digest": plan_digest,
        "applied_at": applied_at,
        # 恢复后再次作者仍从基准源冻结，避免派生字段在展示副本上累积。
        "base_source_ref": ev.get("base_source_ref"),
        "restored_source_ref": prev_ref,
    }
    return layer, {"ref_id": prev_ref}, prev_ref


async def commit_expression(
    session_id: str, layer_id: str, plan: AuthoringPlan, *,
    expected_revision: int,
    preview_digest: str,
    origin: str = "user", actor: str = "bivariate_reliability_panel",
    mutation_id: Optional[str] = None,
    owner_token: Optional[str] = None,
    trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """单笔提交：同 id 整层 upsert + 新 source carrier（一次 CAS/checkpoint）。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent
    from app.services.session_data import session_data_manager

    ctx = await resolve_layer(session_id, layer_id)
    _assert_not_shared_member(ctx, action="提交")
    applied_at = datetime.now(timezone.utc).isoformat()
    plan_digest = plan.plan_digest()

    if plan.mode == "revert":
        frozen = await _freeze(ctx, owner_token=owner_token,
                               trusted_dispatch=trusted_dispatch)
        digest = plan.candidate_digest(frozen.anchor())
        if preview_digest != digest:
            raise AuthoringError(
                "STALE_PREVIEW", "预览后设置或源代次已变化——旧候选不可直接提交",
                status_code=409, field="preview_digest")
        cur = await _current_revision(session_id)
        if int(expected_revision) != cur:
            raise AuthoringError(
                "CAS_SUPERSEDED", "MapSpec 修订在提交期间变化；nothing was written",
                status_code=409,
                correction_hint="重新读取 revision 与当前表达后重做计划。")
        layer, source_data, _ = _revert_layer(
            ctx, origin=origin, actor=actor, plan_digest=plan_digest,
            applied_at=applied_at)
        result = await apply_gis_mutation(
            session_id, UpsertLayerIntent(layer=layer, source_data=source_data),  # type: ignore[arg-type]
            origin=origin, actor=actor, expected_revision=int(expected_revision),
            mutation_id=mutation_id)
        if result.superseded:
            raise AuthoringError("CAS_SUPERSEDED",
                                 "MapSpec 修订在提交期间变化；nothing was written",
                                 status_code=409)
        if result.is_error:
            raise AuthoringError("MUTATION_REJECTED",
                                 str(result.error_msg or "MapSpec mutation rejected"),
                                 status_code=422)
        return {
            "success": True, "mode": "revert", "committed": True,
            "layer_id": layer_id, "revision": int(result.mutation_revision or cur),
            "mapspec_fingerprint": result.mapspec_fingerprint,
            "can_undo": True, "mutations": 1,
            "candidate_digest": digest,
            "disclosures": ["已恢复本域作者决定前的完整表达；undo 可回退整笔提交"],
        }

    # ── 重新冻结（不复用 preview 的数字）并与计划锚核对 ──
    frozen = await _freeze(ctx, owner_token=owner_token,
                           trusted_dispatch=trusted_dispatch)
    check_anchor(plan.source_anchor, frozen)
    candidate, summary = _compute_candidate(plan, ctx, frozen)
    digest = plan.candidate_digest(frozen.anchor())
    if preview_digest != digest:
        raise AuthoringError(
            "STALE_PREVIEW",
            "预览后轴/范围/误差类型/源代次或任何计划参数已变化——"
            "旧预览不可直接提交（late response 也不会落到错误会话）",
            status_code=409, field="preview_digest",
            correction_hint="重新 preview 取得新的 candidate_digest 后再提交。")
    cur = await _current_revision(session_id)
    if int(expected_revision) != cur:
        raise AuthoringError(
            "CAS_SUPERSEDED",
            "MapSpec 修订在提交期间变化（用户/agent 并发或层已锁）；"
            "nothing was written，用户既有决定保留",
            status_code=409,
            correction_hint="重新 inspect 获取当前 revision 与表达后重做计划。")

    # ── 派生展示副本（失败无半引用：CAS/拒绝/幂等命中都会清理）──
    fc = make_derived_fc(frozen, candidate, plan_digest)
    derived_ref = await session_data_manager.store(
        session_id, {"kind": "geojson", "geojson": fc}, prefix="g41display")
    derived_ref = str(derived_ref)
    cleanup_needed = True
    try:
        updated_layer = dict(ctx.layer)
        prev_ev = ctx.layer.get(AUTHORING_KEY) if isinstance(ctx.layer.get(AUTHORING_KEY), dict) else {}
        prev_keys = [k for k in (prev_ev.get("paint_keys") or []) if isinstance(k, str)]
        paint = dict(ctx.layer.get("paint") or {})
        # 切模式/切回单字段时清理本域曾写入的失效键（无幽灵第二通道）。
        for k in prev_keys:
            if k not in candidate.paint_keys and k != "opacity":
                paint.pop(k, None)
        for k, v in candidate.paint.items():
            paint[k] = v
        # 未启用 opacity 第二通道时：若既有 opacity 是**常量**（用户持久透明度
        # 决策），必须原样保留；只有它是表达式或缺失时才写中性 1.0，阻止生命
        # 周期引擎的 ``_preserve_durable_presentation`` 把上一模式的 reliability
        # ``match`` 悄悄带回新图（B30 的真实交互坑）。一律写 1.0 会静默吃掉
        # 用户设定的 0.4（复审 P2）。
        if "opacity" not in candidate.paint:
            prev_opacity = paint.get("opacity")
            if not (_is_constant_number(prev_opacity)):
                paint["opacity"] = 1.0
        updated_layer["paint"] = paint
        updated_layer["legend_spec"] = candidate.legend_spec
        binding = dict(ctx.layer["data_binding"]) if isinstance(ctx.layer.get("data_binding"), dict) else {}
        if plan.mode == "bivariate_matrix" and plan.bivariate is not None:
            binding["field"] = plan.bivariate.axis_a.field
            binding["class_field"] = PAIR_CLASS_FIELD
            updated_layer["bivariate"] = {
                "x_field": plan.bivariate.axis_a.field,
                "y_field": plan.bivariate.axis_b.field,
                "matrix": plan.bivariate.n,
                "class_field": PAIR_CLASS_FIELD,
                "palette_id": plan.bivariate.matrix_id or "custom",
            }
        elif plan.mode == "reliability_dual_channel" and plan.reliability is not None:
            binding["field"] = plan.reliability.estimate_field
            binding["class_field"] = candidate.bucket_field
            updated_layer.pop("bivariate", None)
        updated_layer["data_binding"] = binding
        if plan.title is not None:
            updated_layer["title"] = plan.title
        updated_layer[AUTHORING_KEY] = {
            "schema_version": 1,
            "mode": plan.mode,
            "decided_by": origin,
            "actor": actor,
            "plan_digest": plan_digest,
            "candidate_digest": digest,
            "applied_at": applied_at,
            "algorithm_version": ALGORITHM_VERSION,
            "base_source_ref": frozen.ref_id,
            "base_content_revision": frozen.content_revision,
            "base_content_fingerprint": frozen.content_fingerprint,
            "derived_ref": derived_ref,
            "derived_fields": list(candidate.derived_fields),
            "paint_keys": list(dict.fromkeys(
                list(candidate.paint_keys) + ["opacity"])),
            "stats_basis": candidate.stats_basis,
            "source_has_biv_class": candidate.source_has_biv_class,
            "canonical_legend_digest": expression_fingerprint(candidate.legend_spec),
            "previous_legend_spec": ctx.layer.get("legend_spec"),
            "previous_paint": ctx.layer.get("paint"),
            "previous_data_binding": ctx.layer.get("data_binding"),
            "previous_bivariate": ctx.layer.get("bivariate"),
            "previous_title": ctx.layer.get("title"),
            # 真实上一代 source carrier：paint 可能引用上一代的派生展示字段，
            # 恢复时必须把**同一代** source 一起恢复，否则整层变透明（F2）。
            "previous_source_ref": (
                source_ref_of(ctx.source_entry) or frozen.ref_id),
        }
        source_data = {
            "ref_id": derived_ref,
            "data_fingerprint": frozen.content_fingerprint,
        }
        result = await apply_gis_mutation(
            session_id,
            UpsertLayerIntent(layer=updated_layer, source_data=source_data),  # type: ignore[arg-type]
            origin=origin, actor=actor, expected_revision=int(expected_revision),
            mutation_id=mutation_id)
        if result.superseded:
            raise AuthoringError(
                "CAS_SUPERSEDED", "MapSpec 修订在提交期间变化；nothing was written",
                status_code=409,
                correction_hint="重新读取 revision 与当前表达后重做计划。")
        if result.is_error:
            raise AuthoringError(
                "MUTATION_REJECTED",
                str(result.error_msg or "MapSpec mutation was rejected"),
                status_code=422,
                correction_hint=str(result.correction_hint or ""))
        if getattr(result, "duplicate", False):
            # 幂等命中：库内已提交表达才是真相；本次新建的派生 ref 未被引用。
            await session_data_manager.delete_ref(session_id, derived_ref)
            cleanup_needed = False
            logger.info("[g41] idempotent mutation hit; discarded orphan ref %s", derived_ref)
        cleanup_needed = False
        receipt = _bounded_receipt(
            ctx, plan, frozen, candidate, summary,
            committed=True, candidate_digest=digest,
            revision=int(result.mutation_revision or cur),
            mapspec_fingerprint=result.mapspec_fingerprint,
            derived_ref=None if getattr(result, "duplicate", False) else derived_ref,
            mutations=1)
        receipt["deduplicated"] = bool(getattr(result, "duplicate", False))
        return receipt
    finally:
        if cleanup_needed:
            try:
                await session_data_manager.delete_ref(session_id, derived_ref)
            except Exception as exc:  # noqa: BLE001 - 清理失败只记录，不改变主结果
                logger.warning("[g41] failed to clean orphan derived ref %s: %s",
                               derived_ref, exc)
