"""多尺度表达档案服务（scale_profile.v1，GOAL 04 M7）。

三个 Agent 可发现面（全部经 ToolRegistry 注册，见
``app/tools/scale_profile_tools.py``）：

- ``inspect_map_scale_profile``：只读 —— 图层 + ref 时点 + 重要性证据
  （descriptor.field_schema × 聚合值分布 × 词表）+ 带级取舍预览；
- ``preview_scale_representation``：零突变 —— 完整 build_scale_profile
  （带统计口径/披露/语义检查），不落任何 ref、不改 MapSpec；
- ``apply_scale_representation``：preview 同上；commit —— 工件存**新 ref**
  （非破坏，D2）→ UpsertSource×N + UpsertLayer×1 经 ``apply_gis_mutation_batch``
  单事务 CAS 落 MapSpec（representation windows + layer.scale_profile +
  v1.9 window.source）→ provenance + 回执环。

纪律（DECISIONS D7-D9）：
- 统计/分类口径恒 original_source：apply 不触碰 legend_spec/classify 面
  （legend breaks 指纹记录进档案供跨视图一致性审计）；
- 幂等：operation_id 重放返回存证回执（回执环查命中即原样返回）；
- 诚实降级：无可信重要性字段 → 披露并只做几何综合（不伪造主干）；
  工件生成失败 → 结构化 error，绝不半提交。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.multiscale.importance import (
    DEFAULT_KEEP_RANKS,
    derive_importance_model,
)
from app.lib.cartography.multiscale.plan import (
    ProfileBuildResult,
    build_scale_profile,
    profile_with_artifact_refs,
)
from app.lib.cartography.multiscale.profile import (
    ScaleRepresentationProfile,
    SourceRevision,
    UserScaleOverride,
    representation_windows_of_profile,
    to_mapspec_layer_block,
    validate_profile,
)
from app.lib.cartography.representation import normalize_geometry_family
from app.services.feature_aggregate import aggregate_features

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "scale_profile.v1"
RECEIPTS_KEY = "_scale_profile_receipts"
MAX_RECEIPT_RING = 8

#: 值分布探测的候选字段上限（防自由文本宽表扫描放大）。
_MAX_CANDIDATE_FIELDS = 8
#: 聚合组上限（与 IMPORTANCE MAX_DISTINCT_VALUES 同量级）。
_AGG_MAX_GROUPS = 32


class ScaleProfileError(Exception):
    """服务级错误（工具面转 {"error": ...}）。"""


# ── 目标解析（层 → 源 ref → descriptor/FC）──────────────────────────────


async def _load_spec(session_id: str) -> Dict[str, Any]:
    from app.services.mapspec.store import mapspec_store_instance

    spec = await mapspec_store_instance.get_mapspec(session_id)
    if not isinstance(spec, dict) or not spec:
        raise ScaleProfileError("会话暂无已提交 MapSpec（先出图再调本工具）")
    return spec


def _match_layer(spec: Dict[str, Any], target: str) -> Dict[str, Any]:
    layers = [lyr for lyr in (spec.get("layers") or []) if isinstance(lyr, dict)]
    exact = [lyr for lyr in layers if str(lyr.get("id") or "") == target]
    if len(exact) == 1:
        return exact[0]
    suffix = [lyr for lyr in layers if str(lyr.get("id") or "").endswith(target)]
    if len(suffix) == 1:
        return suffix[0]
    ids = [str(lyr.get("id")) for lyr in layers]
    raise ScaleProfileError(
        f"未唯一命中图层 {target!r}；候选：{', '.join(ids[:12])}")


def _layer_source(
    spec: Dict[str, Any], layer: Dict[str, Any]
) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """层 → (anchor, ref|None, source entry)。两种合法载体：

    - ref 载荷（``ref_id``/``ref``）：descriptor 身份 + 会话数据面；
    - inline 载荷（``inlineData``）：FC 就在 spec 内 —— anchor 用
      ``inline:<source_id>``（内容指纹由 descriptor 侧即时计算），
      原始数据同样永不被修改。
    """
    src_id = layer.get("source")
    sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}
    src = sources.get(src_id) if isinstance(src_id, str) else None
    if not isinstance(src, dict):
        raise ScaleProfileError(
            f"图层 {layer.get('id')} 的 source {src_id!r} 不在 spec.sources")
    ref = src.get("ref_id") or src.get("ref")
    if isinstance(ref, str) and ref.startswith("ref:"):
        return ref, ref, src
    if _unwrap_inline_fc(src) is not None:
        return f"inline:{src_id}", None, src
    raise ScaleProfileError(
        f"图层 {layer.get('id')} 的源既非 ref 载荷也非 inline GeoJSON"
        "（瓦片/栅格源不走多尺度综合通道）")


def _unwrap_inline_fc(src: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """source entry → inline FeatureCollection（容忍一层包装内嵌）。

    规范载体是 entry.inlineData = FC（mapspec_source.store_data 约定）；
    手工/历史 spec 可能内嵌整只 source_data（{type,inlineData}）—— 最多
    下钻一层，不猜更深（诚实失败优于静默误读）。
    """
    inline = src.get("inlineData")
    if isinstance(inline, dict) and inline.get("type") == "geojson" \
            and isinstance(inline.get("inlineData"), dict):
        inline = inline["inlineData"]
    if isinstance(inline, dict) and inline.get("type") == "FeatureCollection":
        return inline
    return None


async def _resolve_fc(
    session_id: str, ref: Optional[str], src_entry: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """载体 → (descriptor 形状, FeatureCollection)（鉴权路径与 REST 一致）。"""
    if ref is not None:
        from app.services.session_data import session_data_manager

        resolved = await session_data_manager.resolve_alias(session_id, ref)
        descriptor = await session_data_manager.get_ref_descriptor(
            session_id, resolved)
        if not isinstance(descriptor, dict):
            raise ScaleProfileError(f"ref {ref} 缺失或不属于本会话")
        res = await session_data_manager.get_ref_data_shared(session_id, resolved)
        if not getattr(res, "success", False):
            raise ScaleProfileError(
                f"ref {ref} 数据不可用：{getattr(res, 'error', '')}")
        data = res.data
        fc = data.get("data") if isinstance(data, dict) and isinstance(
            data.get("data"), dict) else data
        if not (isinstance(fc, dict) and fc.get("type") == "FeatureCollection"):
            raise ScaleProfileError(f"ref {ref} 不是 FeatureCollection")
        return descriptor, fc

    fc = _unwrap_inline_fc(src_entry)
    assert isinstance(fc, dict)  # _layer_source 已保证 inline 载荷可解
    # inline 载荷：即时投影 descriptor 形状（collect_field_schema 是
    # descriptor 生产方的同一实现 —— 不建第二 schema 扫描器）。
    from app.schemas.ref_descriptor import collect_field_schema

    features = fc.get("features") or []
    field_schema, complete = collect_field_schema(features)
    blob = json.dumps(_canonical_fc_digest_input(fc), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"), allow_nan=False)
    descriptor = {
        "feature_count": len(features),
        "geometry_types": sorted({
            str((f.get("geometry") or {}).get("type") or "Unknown")
            for f in features if isinstance(f, dict)}),
        "field_schema": dict(field_schema),
        "field_schema_complete": complete,
        "content_revision": None,
        "data_fingerprint": "inline-sha256:" + hashlib.sha256(
            blob.encode("utf-8")).hexdigest()[:16],
        "bbox": _fc_bbox(features),
    }
    return descriptor, fc


def _canonical_fc_digest_input(fc: Dict[str, Any]) -> Dict[str, Any]:
    """inline FC → 指纹输入（几何坐标降精度 1e-7，稳定于浮点序列化）。"""
    import copy as _copy

    doc = _copy.deepcopy({"type": fc.get("type"), "features": fc.get("features")})

    def _rnd(v: Any) -> Any:
        return round(float(v), 7) if isinstance(v, (int, float)) else v

    for f in doc.get("features") or []:
        geom = f.get("geometry") if isinstance(f, dict) else None
        if not isinstance(geom, dict):
            continue
        gtype = geom.get("type")

        def _walk(node: Any) -> None:
            if isinstance(node, list):
                for i, item in enumerate(node):
                    if isinstance(item, (int, float)):
                        node[i] = _rnd(item)
                    else:
                        _walk(item)

        if gtype == "Point":
            _walk(geom.get("coordinates"))
        else:
            _walk(geom.get("coordinates"))
    return doc


def _fc_bbox(features: List[Any]) -> Optional[List[float]]:
    xs: List[float] = []
    ys: List[float] = []

    def _visit(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if (len(node) >= 2 and isinstance(node[0], (int, float))
                    and isinstance(node[1], (int, float))):
                xs.append(float(node[0]))
                ys.append(float(node[1]))
            else:
                for item in node:
                    _visit(item)

    for f in features:
        if isinstance(f, dict):
            _visit(f.get("geometry", {}).get("coordinates") if isinstance(
                f.get("geometry"), dict) else None)
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _candidate_value_counts(
    field_schema: Dict[str, Any], fc: Dict[str, Any], feature_count: int
) -> Dict[str, Dict[Any, int]]:
    """候选字段（schema 类型 string/number）→ 真实值分布（聚合通道）。"""
    fields = [
        name for name, spec in (field_schema or {}).items()
        if isinstance(spec, dict) and spec.get("type") in ("string", "number")
    ]
    counts: Dict[str, Dict[Any, int]] = {}
    for name in fields[:_MAX_CANDIDATE_FIELDS]:
        try:
            agg = aggregate_features(
                fc, group_by=name, agg="count", max_groups=_AGG_MAX_GROUPS)
        except Exception:  # noqa: BLE001 — 单字段聚合失败不阻断证据收集
            continue
        if agg.get("truncated"):
            continue  # 组溢出 = 自由文本宽表，不是等级词表
        dist = {
            row.get("key"): int(row.get("count") or 0)
            for row in agg.get("rows", [])
            if row.get("key") is not None
        }
        if len(dist) >= 2:
            counts[name] = dist
    return counts


def _latitude_hint(descriptor: Dict[str, Any]) -> float:
    bbox = descriptor.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            lat = (float(bbox[1]) + float(bbox[3])) / 2.0
            return max(-85.0, min(85.0, lat))
        except (TypeError, ValueError):
            logger.debug("Invalid bbox latitude; using equatorial scale", exc_info=True)
    return 0.0


def _geometry_family_of(descriptor: Dict[str, Any], layer: Dict[str, Any]) -> str:
    gtypes = descriptor.get("geometry_types")
    if isinstance(gtypes, list) and gtypes:
        return normalize_geometry_family(str(gtypes[0]))
    layer_type = str(layer.get("type") or "")
    return {"circle": "point", "heatmap": "point", "line": "line",
            "fill": "polygon", "fill-extrusion": "polygon"}.get(layer_type, "unknown")


def _fc_content_fingerprint(fc: Dict[str, Any]) -> str:
    """工件内容指纹（规范化 JSON：sort_keys + 坐标浮点经序列化稳定化）。"""
    blob = json.dumps(fc, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "gen-sha256:" + hashlib.sha256(blob).hexdigest()[:16]


def _legend_breaks_fingerprint(layer: Dict[str, Any]) -> Optional[str]:
    legend = layer.get("legend_spec")
    if not isinstance(legend, dict) or not legend:
        return None
    try:
        blob = json.dumps(legend, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode()
        return "legend-sha256:" + hashlib.sha256(blob).hexdigest()[:16]
    except (TypeError, ValueError):
        return None


async def _prepare(
    session_id: str, layer_target: str
) -> Dict[str, Any]:
    """公共解析段：spec/layer/载体/descriptor/fc/重要性证据。"""
    spec = await _load_spec(session_id)
    layer = _match_layer(spec, layer_target)
    anchor, ref, src_entry = _layer_source(spec, layer)
    descriptor, fc = await _resolve_fc(session_id, ref, src_entry)
    field_schema = descriptor.get("field_schema") if isinstance(
        descriptor.get("field_schema"), dict) else {}
    feature_count = int(descriptor.get("feature_count") or len(fc.get("features") or []))
    value_counts = _candidate_value_counts(field_schema, fc, feature_count)
    derivation = derive_importance_model(field_schema, value_counts, feature_count)
    return {
        "spec": spec, "layer": layer, "anchor": anchor, "ref": ref,
        "src_entry": src_entry,
        "descriptor": descriptor, "fc": fc, "field_schema": field_schema,
        "feature_count": feature_count, "value_counts": value_counts,
        "derivation": derivation,
    }


def _source_revision(ctx: Dict[str, Any]) -> SourceRevision:
    d = ctx["descriptor"]
    return SourceRevision(
        ref=ctx["anchor"],
        content_revision=d.get("content_revision") if isinstance(
            d.get("content_revision"), int) else None,
        data_fingerprint=d.get("data_fingerprint"),
        feature_count=ctx["feature_count"],
        geometry_family=_geometry_family_of(d, ctx["layer"]),
    )


def _band_preview_rows(profile: ScaleRepresentationProfile) -> List[Dict[str, Any]]:
    return [
        {
            "tier": b.scale.tier,
            "min_zoom": b.scale.min_zoom, "max_zoom": b.scale.max_zoom,
            "display_denominator_at_min": b.scale.display_denominator_at_min,
            "representation_mode": b.representation_mode,
            "kept_values": b.selection.kept_values,
            "importance_evidence": b.selection.importance_evidence,
            "decluttering": b.decluttering.mode,
            "original": b.stats.original_feature_count,
            "displayed": b.stats.displayed_feature_count,
            "suppressed": b.stats.suppressed_feature_count,
            "degrade_reasons": b.stats.degrade_reasons[:6],
        }
        for b in profile.bands
    ]


def _parse_overrides(rows: Optional[List[Dict[str, Any]]]) -> List[UserScaleOverride]:
    out: List[UserScaleOverride] = []
    for row in rows or []:
        if isinstance(row, dict) and row.get("kind") and row.get("value") is not None:
            out.append(UserScaleOverride.model_validate(row))
    return out[:8]


async def _build_profile(
    session_id: str, layer_target: str, *, keep_ranks: Optional[Dict[str, int]],
    overrides: List[UserScaleOverride],
) -> Tuple[Dict[str, Any], ProfileBuildResult]:
    ctx = await _prepare(session_id, layer_target)
    result = build_scale_profile(
        layer_id=str(ctx["layer"].get("id")),
        feature_collection=ctx["fc"],
        source=_source_revision(ctx),
        geometry_family=None,  # descriptor 权威（family 归一在 build 内）
        latitude_hint=_latitude_hint(ctx["descriptor"]),
        importance=ctx["derivation"],
        user_overrides=overrides,
        keep_ranks=keep_ranks or DEFAULT_KEEP_RANKS,
        generated_by="webgis_apply_scale_representation",
    )
    return ctx, result


# ── inspect：只读证据面 ──────────────────────────────────────────────────


async def inspect_map_scale_profile(session_id: str, layer_target: str) -> Dict[str, Any]:
    ctx = await _prepare(session_id, layer_target)
    derivation = ctx["derivation"]
    kept_preview: Dict[str, List[Any]] = {}
    if derivation.selected is not None:
        from app.lib.cartography.multiscale.importance import kept_values_for_band

        for tier in DEFAULT_KEEP_RANKS:
            kept_preview[tier] = kept_values_for_band(
                derivation.selected, tier, DEFAULT_KEEP_RANKS)
    existing = ctx["layer"].get("scale_profile")
    return {
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "layer_id": ctx["layer"].get("id"),
        "source": {
            "ref": ctx["anchor"],
            "content_revision": ctx["descriptor"].get("content_revision"),
            "feature_count": ctx["feature_count"],
            "geometry_family": _geometry_family_of(ctx["descriptor"], ctx["layer"]),
        },
        "importance": {
            "selected": derivation.selected.model_dump() if derivation.selected else None,
            "candidates": [c.model_dump() for c in derivation.candidates][:8],
            "reason_codes": derivation.reason_codes[:8],
        },
        "value_distributions": {
            k: dict(sorted(v.items(), key=lambda kv: -kv[1])[:_AGG_MAX_GROUPS])
            for k, v in ctx["value_counts"].items()
        },
        "band_selection_preview": kept_preview,
        "existing_scale_profile": existing if isinstance(existing, dict) else None,
        "disclosures": (
            ["统计口径恒 original_source；综合只作用于显示副本"] +
            ([] if derivation.selected else
             ["无可信重要性字段 —— 不做要素取舍（不伪造主干道路）"])),
    }


# ── preview：零突变完整计划 ──────────────────────────────────────────────


async def preview_scale_representation(
    session_id: str,
    layer_target: str,
    *,
    keep_ranks: Optional[Dict[str, int]] = None,
    overrides: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    ctx, result = await _build_profile(
        session_id, layer_target, keep_ranks=keep_ranks,
        overrides=_parse_overrides(overrides))
    profile = result.profile
    return {
        "status": "preview",
        "schema_version": SCHEMA_VERSION,
        "layer_id": profile.layer_id,
        "profile_id": profile.profile_id,
        "fingerprint": to_mapspec_layer_block(profile)["fingerprint"],
        "bands": _band_preview_rows(profile),
        "artifact_tiers": sorted(result.artifacts.keys()),
        "semantic_checks": [c.model_dump() for c in profile.semantic_checks],
        "disclosures": profile.disclosures,
        "windows": representation_windows_of_profile(profile),
        "note": "零突变预览：未存 ref、未改 MapSpec",
    }


# ── apply：preview / commit ──────────────────────────────────────────────


async def apply_scale_representation(
    session_id: str,
    layer_target: str,
    *,
    mode: str = "preview",
    keep_ranks: Optional[Dict[str, int]] = None,
    overrides: Optional[List[Dict[str, Any]]] = None,
    operation_id: Optional[str] = None,
    expected_revision: Optional[int] = None,
    note: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> Dict[str, Any]:
    op_id = operation_id or f"msp:{int(time.time() * 1000)}:{layer_target}"
    prior = await _find_receipt(session_id, op_id)
    if prior is not None:
        prior["replayed"] = True
        return prior

    if mode != "commit":
        receipt = await preview_scale_representation(
            session_id, layer_target, keep_ranks=keep_ranks, overrides=overrides)
        receipt.update({
            "operation_id": op_id, "mode": "preview",
            "note": (note or "")[:160] or None,
            "commit_hint": "同参 + mode=commit 提交（工件将存为新 ref，源数据不动）",
        })
        return receipt

    ctx, result = await _build_profile(
        session_id, layer_target, keep_ranks=keep_ranks,
        overrides=_parse_overrides(overrides))
    # 断点续链（评审 R1-P2-3）：同 op_id 的 partial 回执携带已提交 slot
    # 与已存工件 ref —— 重放复用（不重跑已提交 intent、不产孤儿 ref）。
    partial = await _find_receipt(session_id, op_id, statuses=("partial",))
    resume_slots = int(partial.get("committed_slots") or 0) if partial else 0
    resume_refs = partial.get("artifact_refs") if partial else None
    resume_source_ids = partial.get("artifact_source_ids") if partial else None
    profile = result.profile
    if not result.artifacts:
        # 平凡计划（无取舍无简化）：落声明但不产工件（诚实 no-op commit）
        profile2 = profile
        ref_map: Dict[str, Tuple[str, str]] = {}
    else:
        from app.services.session_data import session_data_manager

        ref_map = {}
        for tier, gen in result.artifacts.items():
            if isinstance(resume_refs, dict) and tier in resume_refs and \
                    isinstance(resume_source_ids, dict) and \
                    tier in resume_source_ids:
                ref_map[tier] = (resume_refs[tier], resume_source_ids[tier])
                continue
            ref_id = await session_data_manager.store(
                session_id, gen.feature_collection, prefix="gen")
            source_id = f"{profile.layer_id}__gen@{tier}"
            ref_map[tier] = (ref_id, source_id)
        profile2 = profile_with_artifact_refs(profile, ref_map)

    errors = validate_profile(profile2, require_artifact_refs=bool(ref_map))
    if errors:
        raise ScaleProfileError(f"档案校验失败：{errors}")

    # 落笔 intents：工件源 ×N + 层 patch ×1 —— 顺序 CAS 链（batch 门面
    # 只收 presentation intent；非 presentation 组走 apply_gis_mutation
    # 逐笔携带链上 revision，superseded 即停链 —— 与 map_operation 同款
    # 语义，不虚构强原子）。
    from app.services.mapspec.intents import (
        UpsertLayerIntent,
        UpsertSourceIntent,
    )
    from app.services.gis_world_state.mutation import apply_gis_mutation

    intents: List[Any] = []
    for tier, (ref_id, source_id) in ref_map.items():
        gen_fc = result.artifacts[tier].feature_collection
        intents.append(UpsertSourceIntent(source_id=source_id, source={
            "type": "geojson",
            "ref": ref_id,
            "ref_id": ref_id,
            # 工件**内容**指纹（评审 R1-P2-2：不能哈希含服务端 ref_id 的
            # ref_map —— 同内容重跑会假阳性「变更」）
            "data_fingerprint": _fc_content_fingerprint(gen_fc),
            "profile": {
                "featureCount": len(gen_fc.get("features") or []),
                "generated_by": "scale_profile.v1",
                "derived_from": profile.source.ref,
            },
        }))
    new_layer = dict(ctx["layer"])
    windows = representation_windows_of_profile(profile2)
    if any(w.get("mode") != "raw" for w in windows):
        new_layer["representation"] = {"windows": windows}
    new_layer["scale_profile"] = to_mapspec_layer_block(profile2)
    new_layer["scale_profile"]["classification_breaks_fingerprint"] = (
        _legend_breaks_fingerprint(ctx["layer"]))
    intents.append(UpsertLayerIntent(layer=new_layer))

    # CAS 基线（R2 新发现 1 修正）：显式 expected_revision 恒优先；断点
    # 续链未给基线时**重读当前 revision**（断点前 slot 已持久，重读即下一
    # intent 的正确 CAS 对照 —— 既不被存量 0 的 falsy 吞掉、也不被过期
    # 存量值永久压制；外部写者若已推进，首个续链 intent 诚实 superseded）。
    if expected_revision is not None:
        chain_rev: Optional[int] = int(expected_revision)
    elif partial is not None:
        chain_rev = await _current_revision(session_id)
    else:
        chain_rev = None
    last_res = None
    for slot, intent in enumerate(
            intents[resume_slots:], start=resume_slots):
        res = await apply_gis_mutation(
            session_id, intent,
            origin="agent", actor="webgis_apply_scale_representation",
            expected_revision=chain_rev,
            mutation_id=f"op:{op_id}:{slot}",
            turn_id=turn_id,
            reason=note or "apply scale representation (GOAL 04)",
        )
        if res.is_error or res.superseded:
            # superseded 的 is_error=False（引擎语义：不是校验错也不是提交）
            # —— 链上外部写者推进了 revision，本操作显式停链不盲目重试。
            # 已提交 slot 持久化部分回执（评审 R1-P2-3：重放复用已存工件
            # ref、从断点续链 —— 不重跑已提交 intent、不产孤儿 ref）。
            partial = {
                "status": "partial",
                "schema_version": SCHEMA_VERSION,
                "operation_id": op_id,
                "layer_id": profile.layer_id,
                "profile_id": profile2.profile_id,
                "committed_slots": slot,
                "chain_revision": chain_rev,
                "artifact_refs": {t: r[0] for t, r in ref_map.items()},
                "artifact_source_ids": {t: r[1] for t, r in ref_map.items()},
                "error": res.error_msg or (
                    "superseded: MapSpec revision moved during apply"),
                "at": round(time.time(), 3),
            }
            await _persist_receipt(session_id, partial)
            return {
                **partial,
                "hint": "部分 intent 已提交；同 operation_id 重放将从断点续链",
            }
        # 链基线 = 前一步提交后的当前 revision（CAS 对照 current，非 +1）
        chain_rev = int(res.mutation_revision) if res.mutation_revision else None
        last_res = res
    if last_res is None:
        # 续链切片为空（重放参数与原计划不一致 —— intents 变少）：不是
        # committed，诚实报错（R2 新发现 3）。
        return {
            "status": "error",
            "error": "resume produced no runnable intents（重放参数与"
                     "原计划不一致；partial 回执基于不同 profile）",
            "operation_id": op_id,
            "committed_slots": resume_slots,
        }
    res = last_res

    receipt = {
        "status": "committed",
        "schema_version": SCHEMA_VERSION,
        "operation_id": op_id,
        "layer_id": profile.layer_id,
        "profile_id": profile2.profile_id,
        "mapspec_revision": int(res.mutation_revision) if res else None,
        "bands": _band_preview_rows(profile2),
        "artifact_refs": {t: r[0] for t, r in ref_map.items()},
        "source_untouched": profile2.source.ref,
        "classification_basis": profile2.classification_basis,
        "semantic_checks": [c.model_dump() for c in profile2.semantic_checks],
        "disclosures": profile2.disclosures,
        "note": (note or "")[:160] or None,
        "at": round(time.time(), 3),
    }
    await _persist_receipt(session_id, receipt)
    return receipt


# ── 回执环（幂等重放面；与 map_operations 同纪律）────────────────────────


async def _current_revision(session_id: str) -> Optional[int]:
    """当前 mutation revision（engine 印章的只读回读；失败 None=无 CAS）。"""
    from app.services.session_data import session_data_manager

    try:
        state = await session_data_manager.get_map_state(session_id) or {}
        raw = state.get("_cartographic_mutation_revision")
        return int(raw) if isinstance(raw, int) else None
    except Exception:  # noqa: BLE001 — 读失败按无基线（engine CAS 仍权威）
        return None


async def _find_receipt(
    session_id: str, operation_id: str,
    *, statuses: Tuple[str, ...] = ("committed",),
) -> Optional[Dict[str, Any]]:
    from app.services.session_data import session_data_manager

    try:
        state = await session_data_manager.get_map_state(session_id) or {}
    except Exception:  # noqa: BLE001
        return None
    ring = state.get(RECEIPTS_KEY)
    if isinstance(ring, list):
        for row in ring:
            if isinstance(row, dict) and row.get("operation_id") == operation_id \
                    and row.get("status") in statuses:
                return dict(row)
    return None


async def _persist_receipt(session_id: str, receipt: Dict[str, Any]) -> None:
    from app.services.session_data import session_data_manager

    try:
        state = await session_data_manager.get_map_state(session_id) or {}
        ring = [r for r in (state.get(RECEIPTS_KEY) or [])
                if isinstance(r, dict)]
        op_id = receipt.get("operation_id")
        if isinstance(op_id, str):
            # 同 op 替换（committed 覆盖 partial —— 回执环每 op 单条）
            ring = [r for r in ring if r.get("operation_id") != op_id]
        ring.insert(0, dict(receipt))
        await session_data_manager.set_map_state(
            session_id, RECEIPTS_KEY, ring[:MAX_RECEIPT_RING])
    except Exception:  # noqa: BLE001 — 回执 best-effort 不阻断提交
        logger.warning("[scale_profile] receipt persist failed", exc_info=True)


__all__ = [
    "SCHEMA_VERSION",
    "ScaleProfileError",
    "inspect_map_scale_profile",
    "preview_scale_representation",
    "apply_scale_representation",
]
