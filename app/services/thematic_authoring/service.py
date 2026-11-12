"""G33 专题作者服务门面（inspect / preview / commit，WP07）。

- inspect：层 + 源身份 + 当前表达（指纹/证据）+ 同版本分布统计。
- preview：完整构建候选表达但**零 mutation**（不写 MapSpec、不推进修订）。
- commit：单笔 ``UpsertLayerIntent``（一次 CAS / 一个 checkpoint / 一条
  provenance）——同 id 整层替换，只改本域键；源/层 id/opacity/visibility/
  标签（G16）/scale_profile（G27）等一律保留。失败时 nothing was written。

跨幅共享（G15 shared_expression / temporal.compare）：inspect 识别层的
共享表达作用域并披露；成员层编辑默认**拒绝**（提示转交产品级入口），
本服务不复制 shared_expression 计算器。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.thematic_spec import is_thematic, thematic_field
from app.services.thematic_authoring import stats as stats_mod
from app.services.thematic_authoring.builder import (
    CandidateExpression,
    build_candidate,
    size_samples,
)
from app.services.thematic_authoring.contract import ThematicAuthoringPlan
from app.services.thematic_authoring.errors import ThematicAuthoringError

logger = logging.getLogger(__name__)

_VECTOR_LAYER_TYPES = {"fill", "circle", "line", "symbol"}

#: G33 证据块键（层上兄弟键；upsert 整层替换时由本服务维护）。
AUTHORING_KEY = "thematic_authoring"


def expression_fingerprint(legend_spec: Any) -> Optional[str]:
    """表达指纹：legend_spec 规范 JSON 的内容寻址摘要（v2 溯源字段参与）。"""
    if not isinstance(legend_spec, dict):
        return None
    payload = json.dumps(
        legend_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False, default=str,
    )
    return "texp-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class LayerContext:
    session_id: str
    mapspec: Dict[str, Any]
    layer: Dict[str, Any]
    source_id: str
    source_entry: Dict[str, Any]

    @property
    def ref_id(self) -> Optional[str]:
        for key in ("ref_id", "ref"):
            v = self.source_entry.get(key)
            if isinstance(v, str) and v.startswith("ref:"):
                return v
        return None

    @property
    def declared_revision(self) -> int:
        v = self.source_entry.get("content_revision")
        return int(v) if isinstance(v, int) else 0


async def _read_mapspec(session_id: str) -> Optional[Dict[str, Any]]:
    from app.services.mapspec_store import mapspec_store

    return await mapspec_store.get_mapspec(session_id)


async def _current_revision(session_id: str) -> int:
    """CAS 令牌 = 会话状态里的权威修订（与 G20 raster_authoring 同口径）。"""
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
        raise ThematicAuthoringError(
            "NO_SESSION_MAPSPEC", f"会话 {session_id!r} 没有权威 MapSpec",
            status_code=404)
    layer = None
    for cand in mapspec.get("layers") or []:
        if isinstance(cand, dict) and cand.get("id") == layer_id:
            layer = cand
            break
    if layer is None:
        raise ThematicAuthoringError(
            "UNKNOWN_LAYER", f"层 {layer_id!r} 不在权威 MapSpec 中", status_code=404)
    if layer.get("type") not in _VECTOR_LAYER_TYPES:
        raise ThematicAuthoringError(
            "NOT_VECTOR_LAYER",
            f"层 {layer_id!r} 类型 {layer.get('type')!r} 不是矢量层（G33 只编辑矢量专题表达）",
            status_code=409)
    if not is_thematic(layer.get("legend_spec")):
        raise ThematicAuthoringError(
            "NOT_THEMATIC_LAYER",
            f"层 {layer_id!r} 没有专题 legend_spec——先由 create_thematic_map 建立专题表达，"
            "再进入作者工作台编辑",
            status_code=409,
            correction_hint="对话中让 agent 用 create_thematic_map 生成该层的专题表达。")
    source_id = layer.get("source")
    sources = mapspec.get("sources") or {}
    entry = sources.get(source_id) if isinstance(source_id, str) else None
    if not isinstance(entry, dict):
        raise ThematicAuthoringError(
            "SOURCE_NOT_FOUND", f"层引用的源 {source_id!r} 不存在", status_code=409)
    return LayerContext(
        session_id=session_id, mapspec=mapspec, layer=layer,
        source_id=source_id, source_entry=entry,
    )


def _shared_expression_scope(mapspec: Dict[str, Any], ctx: LayerContext) -> Optional[Dict[str, Any]]:
    """G15 frame_product / temporal.compare 的共享分级作用域识别（只读）。"""
    frame = mapspec.get("frame_product")
    if not isinstance(frame, dict):
        return None
    shared = frame.get("shared_expressions") or frame.get("shared_expression")
    if isinstance(shared, list):
        for group in shared:
            if isinstance(group, dict) and ctx.layer.get("id") in (group.get("layer_ids") or []):
                return {"kind": "frame_product", "group": group}
    elif isinstance(shared, dict):
        for name, group in shared.items():
            if isinstance(group, dict) and ctx.layer.get("id") in (group.get("layer_ids") or []):
                return {"kind": "frame_product", "group": group, "name": name}
    return None


async def _hydrate_features(
    ctx: LayerContext, *,
    owner_token: Optional[str] = None,
    trusted_dispatch: bool = False,
) -> Tuple[List[Any], Optional[str], int, Optional[str]]:
    """源 → (features, ref_id, live_content_revision, degraded_reason)。

    统计总体 = 提交源全量（ref 水合或 inline 载荷）；无全源（如纯瓦片 URL）
    → degraded，如实返回（绝不用可见瓦片/视窗冒充全域）。

    鉴权双通道（G20 raster_authoring 同款）：HTTP 路由带 owner_token
    （require_owned_session + X-Session-Token）；Pi 工具走 trusted_dispatch
    的服务端内部读（session_id 由 ToolRegistry 从已授权上下文注入——模型
    不能指定别的 session；per-session 隔离不变）。
    """
    entry = ctx.source_entry
    if entry.get("type") == "geojson":
        ref_id = ctx.ref_id
        if ref_id:
            from app.services.session_data import session_data_manager

            resolved = await session_data_manager.resolve_alias(ctx.session_id, ref_id)
            descriptor = await session_data_manager.get_ref_descriptor(ctx.session_id, resolved)
            if trusted_dispatch:
                raw = await session_data_manager.get(ctx.session_id, resolved)
                if isinstance(raw, str):
                    import json as _json
                    try:
                        raw = _json.loads(raw)
                    except ValueError:
                        raw = None
                fc = raw.get("geojson") if isinstance(raw, dict) else None
                if not isinstance(fc, dict):
                    fc = raw if isinstance(raw, dict) and raw.get("type") == "FeatureCollection" else None
            else:
                res = await session_data_manager.get_ref_data_shared(
                    ctx.session_id, resolved, owner_token=owner_token)
                if not getattr(res, "success", False):
                    raise ThematicAuthoringError(
                        "SOURCE_UNAVAILABLE",
                        f"源 ref 不可用：{getattr(res, 'error', 'unknown')}",
                        status_code=409)
                data = getattr(res, "data", None)
                fc = data.get("geojson") if isinstance(data, dict) else None
                if not isinstance(fc, dict):
                    fc = data if isinstance(data, dict) and data.get("type") == "FeatureCollection" else None
            if fc is None:
                raise ThematicAuthoringError(
                    "SOURCE_NOT_FEATURECOLLECTION", "源 ref 不是 FeatureCollection", status_code=409)
            rev = descriptor.get("content_revision") if isinstance(descriptor, dict) else None
            return (
                list(fc.get("features") or []),
                resolved,
                int(rev) if isinstance(rev, int) else 0,
                None,
            )
        inline = entry.get("inlineData")
        if isinstance(inline, dict) and inline.get("type") == "FeatureCollection":
            return list(inline.get("features") or []), None, ctx.declared_revision, None
        url = entry.get("url") or entry.get("dataPath")
        if isinstance(url, str) and url:
            return [], None, ctx.declared_revision, "no_full_source_url_carrier"
    return [], None, ctx.declared_revision, "source_type_not_supported"


def _field_candidates(ctx: LayerContext, features: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """字段清单（有界）：source profile.fields 优先；缺席时从水合要素的
    属性键扫描（有界 ≤200 键，type 未知如实标注 null——不编造）。"""
    profile = ctx.source_entry.get("profile")
    fields: List[Dict[str, Any]] = []
    if isinstance(profile, dict):
        raw_fields = profile.get("fields")
        if isinstance(raw_fields, dict):
            for name, meta in list(raw_fields.items())[:200]:
                if isinstance(meta, dict):
                    fields.append({
                        "name": str(name),
                        "type": meta.get("type"),
                        "min": meta.get("min"),
                        "max": meta.get("max"),
                        "null_count": meta.get("null_count"),
                    })
    if not fields and features:
        seen: Dict[str, List[str]] = {}
        for f in features[:2000]:
            props = (f.get("properties") or {}) if isinstance(f, dict) else {}
            if not isinstance(props, dict):
                continue
            for key, value in props.items():
                kinds = seen.setdefault(str(key), [])
                kind = {
                    bool: "bool", int: "number", float: "number", str: "string",
                }.get(type(value))
                if kind and kind not in kinds:
                    kinds.append(kind)
                if len(seen) >= 200:
                    break
            if len(seen) >= 200:
                break
        for name, kinds in list(seen.items())[:200]:
            fields.append({
                "name": name,
                "type": kinds[0] if len(kinds) == 1 else "mixed",
                "min": None, "max": None, "null_count": None,
            })
    return fields


def _check_source_anchor(plan: ThematicAuthoringPlan, ref_id: Optional[str], live_revision: int) -> None:
    anchor = plan.source_anchor
    if anchor is None:
        return
    if anchor.ref_id and ref_id and anchor.ref_id != ref_id:
        raise ThematicAuthoringError(
            "STALE_SOURCE",
            f"计划锚定的源 {anchor.ref_id!r} ≠ 当前源 {ref_id!r}",
            status_code=409,
            correction_hint="重新 inspect 获取当前源锚后再提交。")
    if anchor.content_revision != live_revision:
        raise ThematicAuthoringError(
            "STALE_SOURCE",
            f"源内容版本已变化（计划锚 rev={anchor.content_revision}，当前 rev={live_revision}）",
            status_code=409,
            correction_hint="数据已被编辑/刷新——重新检查分布并重做计划；不盲目重试覆盖。")


async def inspect_layer(
    session_id: str, layer_id: str, *, field: Optional[str] = None,
    owner_token: Optional[str] = None, trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """作者检查：当前表达 + 同版本分布 + 字段清单 + 能力披露。"""
    ctx = await resolve_layer(session_id, layer_id)
    current_spec = ctx.layer.get("legend_spec")
    target_field = field or thematic_field(current_spec)
    features, ref_id, live_rev, degraded = await _hydrate_features(
        ctx, owner_token=owner_token, trusted_dispatch=trusted_dispatch)
    distribution: Dict[str, Any]
    if degraded or not target_field:
        distribution = {
            "field": target_field,
            "basis": {"mode": "degraded", "reason": degraded or "no_field",
                      "approximate": True, "stats_version": stats_mod.STATS_VERSION},
            "numeric": {}, "categorical": {},
            "disclosure": "无全量源（或未指定字段）——统计不可用；"
                          "绝不从可见瓦片/视窗推断全域分布（MVT 可见 ≠ 统计总体）",
        }
    else:
        if ref_id is not None:
            distribution = stats_mod.cached_distribution(ref_id, live_rev, target_field, features)
        else:
            distribution = stats_mod.field_distribution(stats_mod.scan_field(features, target_field))

    authoring = ctx.layer.get(AUTHORING_KEY) if isinstance(ctx.layer.get(AUTHORING_KEY), dict) else None
    shared = _shared_expression_scope(ctx.mapspec, ctx)
    receipt: Dict[str, Any] = {
        "layer_id": layer_id,
        "layer_type": ctx.layer.get("type"),
        "source": {
            "source_id": ctx.source_id,
            "ref_id": ref_id,
            "content_revision": live_rev,
            "data_fingerprint": ctx.source_entry.get("data_fingerprint"),
            "feature_count": len(features),
        },
        "current": {
            "legend_spec": current_spec,
            "fingerprint": expression_fingerprint(current_spec),
            "paint": (ctx.layer.get("paint") or {}).get("color"),
            "authoring": authoring,
            "size_channel_keys": (authoring or {}).get("size_channel_keys") or [],
        },
        "fields": _field_candidates(ctx, features),
        "distribution": distribution,
        "revision": await _current_revision(session_id),
        "shared_expression_scope": shared,
    }
    if shared:
        receipt["notes"] = [
            "该层参与跨幅共享分级（G15 frame_product）——单成员编辑会破坏"
            "「共享同值同色」口径；请使用多画幅产品级入口更新整组，或先解除共享",
        ]
    return receipt


async def preview_expression(
    session_id: str, layer_id: str, plan: ThematicAuthoringPlan, *,
    owner_token: Optional[str] = None, trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """预览：构建候选（与 commit 同一确定性构建器），零 mutation。"""
    ctx = await resolve_layer(session_id, layer_id)
    if _shared_expression_scope(ctx.mapspec, ctx) is not None:
        raise ThematicAuthoringError(
            "SHARED_EXPRESSION_MEMBER",
            "该层参与跨幅共享分级——单成员编辑会破坏共享口径；"
            "请使用多画幅产品级入口原子更新整组",
            status_code=409,
            correction_hint="在 frame_product 入口更新共享组，或先解除该层共享。")
    current_spec = ctx.layer.get("legend_spec")
    current_field = thematic_field(current_spec)
    target_field = plan.field or current_field
    if not target_field:
        raise ThematicAuthoringError(
            "MISSING_FIELD", "无法确定目标字段", field="field")
    features, ref_id, live_rev, degraded = await _hydrate_features(
        ctx, owner_token=owner_token, trusted_dispatch=trusted_dispatch)
    if degraded:
        raise ThematicAuthoringError(
            "STATS_UNAVAILABLE",
            f"无全量源（{degraded}）——不能在不了解分布的情况下编辑该层表达；"
            "本域拒绝从可见瓦片推断全域",
            status_code=409)
    _check_source_anchor(plan, ref_id, live_rev)
    scan = stats_mod.scan_field(features, target_field)
    candidate = build_candidate(
        plan, scan, current_spec=current_spec, current_field=current_field)
    dist = (
        stats_mod.cached_distribution(ref_id, live_rev, target_field, features)
        if ref_id is not None
        else stats_mod.field_distribution(scan)
    )
    return _receipt(
        ctx, plan, candidate, committed=False,
        ref_id=ref_id, live_rev=live_rev,
        distribution=dist, target_field=target_field,
    )


def _receipt(
    ctx: LayerContext, plan: ThematicAuthoringPlan, candidate: CandidateExpression, *,
    committed: bool, ref_id: Optional[str], live_rev: int,
    distribution: Dict[str, Any], target_field: str,
    revision: Optional[int] = None, mapspec_fingerprint: Optional[str] = None,
    can_undo: bool = False, mutations: int = 0,
) -> Dict[str, Any]:
    receipt = candidate.receipt()
    receipt.update({
        "layer_id": ctx.layer.get("id"),
        "field": target_field,
        "mode": plan.mode,
        "committed": committed,
        "source_anchor": {"ref_id": ref_id, "content_revision": live_rev},
        "current_fingerprint": expression_fingerprint(ctx.layer.get("legend_spec")),
        "candidate_fingerprint": expression_fingerprint(candidate.legend_spec),
        "stats_basis": distribution.get("basis"),
        "distribution": distribution,
        "plan_digest": plan.plan_digest(),
    })
    if committed:
        receipt["revision"] = revision
        receipt["mapspec_fingerprint"] = mapspec_fingerprint
        receipt["can_undo"] = can_undo
        receipt["mutations"] = mutations
    if plan.size_channel is not None:
        receipt["size_samples"] = size_samples(plan.size_channel)
    return receipt


async def commit_expression(
    session_id: str, layer_id: str, plan: ThematicAuthoringPlan, *,
    expected_revision: int, origin: str = "user", actor: str = "thematic_authoring",
    mutation_id: Optional[str] = None, owner_token: Optional[str] = None,
    trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """单笔提交：同 id 整层 upsert（一次 CAS / 一个 checkpoint）。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent

    ctx = await resolve_layer(session_id, layer_id)
    if _shared_expression_scope(ctx.mapspec, ctx) is not None:
        raise ThematicAuthoringError(
            "SHARED_EXPRESSION_MEMBER",
            "该层参与跨幅共享分级——拒绝单成员提交（会破坏共享口径）",
            status_code=409,
            correction_hint="使用多画幅产品级入口原子更新整组，或先解除共享。")
    current_spec = ctx.layer.get("legend_spec")
    current_field = thematic_field(current_spec)
    target_field = plan.field or current_field
    if not target_field:
        raise ThematicAuthoringError("MISSING_FIELD", "无法确定目标字段", field="field")
    features, ref_id, live_rev, degraded = await _hydrate_features(
        ctx, owner_token=owner_token, trusted_dispatch=trusted_dispatch)
    if degraded:
        raise ThematicAuthoringError(
            "STATS_UNAVAILABLE",
            f"无全量源（{degraded}）——提交前必须可核验分布与版本",
            status_code=409)
    _check_source_anchor(plan, ref_id, live_rev)
    scan = stats_mod.scan_field(features, target_field)
    candidate = build_candidate(
        plan, scan, current_spec=current_spec, current_field=current_field)
    dist = (
        stats_mod.cached_distribution(ref_id, live_rev, target_field, features)
        if ref_id is not None
        else stats_mod.field_distribution(scan)
    )

    # ── 同 id 整层载荷：只改本域键，其余逐键保留 ──
    updated_layer = dict(ctx.layer)
    paint = dict(ctx.layer.get("paint") or {})
    paint["color"] = candidate.paint_color
    prev_authoring = ctx.layer.get(AUTHORING_KEY) if isinstance(ctx.layer.get(AUTHORING_KEY), dict) else {}
    prev_size_keys = [k for k in (prev_authoring.get("size_channel_keys") or []) if k in paint]
    if candidate.size_paint is not None:
        for k, v in candidate.size_paint.items():
            paint[k] = v
        size_keys = list(candidate.size_paint.keys())
    else:
        # 无 size_channel（含 revert_auto）：仅移除**本域曾写入**的大小键，
        # 不触碰其他来源的符号键。
        for k in prev_size_keys:
            paint.pop(k, None)
        size_keys = []
    updated_layer["paint"] = paint
    updated_layer["legend_spec"] = candidate.legend_spec
    if isinstance(ctx.layer.get("data_binding"), dict):
        binding = dict(ctx.layer["data_binding"])
        binding["field"] = target_field
        updated_layer["data_binding"] = binding
    if plan.title is not None:
        updated_layer["title"] = plan.title
    if plan.mode == "revert_auto":
        updated_layer[AUTHORING_KEY] = {
            "schema_version": 1,
            "mode": "revert_auto",
            "decided_by": origin,
            "reverted": True,
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "source_anchor": {"ref_id": ref_id, "content_revision": live_rev},
            "stats_basis": dist.get("basis"),
            "previous_fingerprint": expression_fingerprint(current_spec),
            "size_channel_keys": [],
        }
    else:
        updated_layer[AUTHORING_KEY] = {
            "schema_version": 1,
            "mode": plan.mode,
            "decided_by": origin,
            "actor": actor,
            "plan_digest": plan.plan_digest(),
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "source_anchor": {"ref_id": ref_id, "content_revision": live_rev},
            "stats_basis": dist.get("basis"),
            "previous_fingerprint": expression_fingerprint(current_spec),
            "size_channel_keys": size_keys,
            "requested_k": candidate.requested_k,
            "actual_k": candidate.actual_k,
        }

    result = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer=updated_layer, source_data=None),  # type: ignore[arg-type]
        origin=origin, actor=actor, expected_revision=int(expected_revision),
        mutation_id=mutation_id,
    )
    if result.superseded:
        raise ThematicAuthoringError(
            "CAS_SUPERSEDED",
            "MapSpec 修订在提交期间变化；nothing was written",
            status_code=409,
            correction_hint="重新读取 revision 与当前表达，重做计划后重试（不盲目覆盖他人）。",
        )
    if result.is_error:
        raise ThematicAuthoringError(
            "MUTATION_REJECTED",
            str(result.error_msg or "MapSpec mutation was rejected"),
            status_code=422,
            correction_hint=str(result.correction_hint or ""),
        )
    return _receipt(
        ctx, plan, candidate, committed=True,
        ref_id=ref_id, live_rev=live_rev,
        distribution=dist, target_field=target_field,
        revision=int(result.mutation_revision or expected_revision),
        mapspec_fingerprint=result.mapspec_fingerprint,
        can_undo=True, mutations=1,
    )
