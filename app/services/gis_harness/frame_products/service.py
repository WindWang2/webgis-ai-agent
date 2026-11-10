"""G15 frame_products 会话服务 —— bind/plan/update/unbind/status。

全部写入经 ``apply_gis_mutation``（origin=agent；用户面走 REST
SetFrameProductBody origin=user CAS），MapSpec 是唯一 desired state：
- 画幅几何身份 → ``layout.frames``（SetLayoutIntent.frames 写面）；
- 产品语义 → ``frame_product``（SetFrameProductIntent）。

科学诚实语义（与 temporal_products 同门）：
- 计划只来自真实数据桶/真实类别值/真实字段 —— 绝不虚构年份或类别；
- 单期数据不做 time_series 产品（typed refusal）；
- 共享分级资格由 shared_expression.compute 判定，拒绝时不降级伪共享；
- bind 失败不留空壳成功产品（frames 先写、产品后写；产品写失败时
  frames 登记是惰性无害的 —— 无产品引用的画幅不参与任何渲染，回执
  如实披露）。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("gis_harness.frame_products")

#: 计划/绑定的画幅数上界（与 schema MAX_PRODUCT_FRAMES 同口径）。
MAX_PLAN_FRAMES = 12

_FRAME_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _frame_id_slug(prefix: str, value: Any) -> str:
    """值 → 稳定 frame id 片段（ASCII 可读；非拉丁值确定性短哈希保唯一）。"""
    import hashlib

    s = str(value)
    slug = _FRAME_ID_RE.sub("-", s).strip("-")
    if not slug:
        digest = hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]
        return f"{prefix}-{digest}"
    return f"{prefix}-{slug}"[:64]


def _refusal(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"status": "refused", "code": code, "message": message}
    out.update(extra)
    return out


def _extract_fc(data: Any) -> Optional[dict]:
    if isinstance(data, dict):
        nested = data.get("geojson")
        if isinstance(nested, dict) and nested.get("type") == "FeatureCollection":
            return nested
        if data.get("type") == "FeatureCollection":
            return data
    return None


async def _resolve_ref_fc(session_id: str, ref_id: str) -> Tuple[str, Dict, Optional[Dict]]:
    """ref → (resolved, descriptor, fc) —— 鉴权/缺失语义与 REST 路由一致。"""
    from app.services.session_data import session_data_manager

    resolved = await session_data_manager.resolve_alias(session_id, ref_id)
    descriptor = await session_data_manager.get_ref_descriptor(session_id, resolved)
    res = await session_data_manager.get_ref_data_shared(session_id, resolved)
    if not res.success:
        return resolved, descriptor or {}, None
    return resolved, descriptor or {}, _extract_fc(res.data)


async def _committed_spec(session_id: str) -> Dict[str, Any]:
    from app.services.mapspec.lifecycle_engine import mapspec_lifecycle_engine

    spec = await mapspec_lifecycle_engine.store.get_mapspec(session_id)
    return spec if isinstance(spec, dict) else {}


async def _apply(
    session_id: str,
    intent: Any,
    *,
    actor: str = "frame_products",
    expected_revision: Optional[int] = None,
    reason: Optional[str] = None,
):
    from app.services.gis_world_state import apply_gis_mutation

    return await apply_gis_mutation(
        session_id, intent, origin="agent", actor=actor,
        expected_revision=expected_revision, reason=reason,
    )


def _fc_bbox(fc: Dict[str, Any]) -> Optional[List[float]]:
    """FeatureCollection → WSEN bbox（坐标面有界遍历；非有限值跳过）。"""
    import math

    w = s = math.inf
    e = n = -math.inf
    count = 0

    def _walk(coords: Any) -> None:
        nonlocal w, s, e, n, count
        if count > 100_000:
            return
        if isinstance(coords, (list, tuple)) and coords:
            if isinstance(coords[0], (int, float)):
                count += 1
                if len(coords) >= 2:
                    x, y = float(coords[0]), float(coords[1])
                    if math.isfinite(x) and math.isfinite(y):
                        w, e = min(w, x), max(e, x)
                        s, n = min(s, y), max(n, y)
                return
            for c in coords:
                _walk(c)

    for f in (fc.get("features") or []):
        if isinstance(f, dict) and isinstance(f.get("geometry"), dict):
            _walk(f["geometry"].get("coordinates"))
    if count == 0 or not math.isfinite(w):
        return None
    return [w, s, e, n]


def _pad_bbox(bbox: List[float]) -> List[float]:
    pad_lng = max((bbox[2] - bbox[0]) * 0.05, 1e-4)
    pad_lat = max((bbox[3] - bbox[1]) * 0.05, 1e-4)
    return [bbox[0] - pad_lng, bbox[1] - pad_lat, bbox[2] + pad_lng, bbox[3] + pad_lat]


def _finite_field_values(fc: Dict[str, Any], field: str) -> List[float]:
    out: List[float] = []
    for f in (fc.get("features") or []):
        if not isinstance(f, dict):
            continue
        props = f.get("properties")
        if not isinstance(props, dict):
            continue
        v = props.get(field)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        fv = float(v)
        if fv == fv and fv not in (float("inf"), float("-inf")):
            out.append(fv)
    return out


def _filter_fc_where(fc: Dict[str, Any], where: Dict[str, Any]) -> Dict[str, Any]:
    """FC 按 where AST 过滤（evaluate_where 同词表）。"""
    from app.services.feature_pages import evaluate_where

    kept = [
        f for f in (fc.get("features") or [])
        if isinstance(f, dict) and evaluate_where(where, f)
    ]
    return {"type": "FeatureCollection", "features": kept}


# ── 计划（WP03）：真实数据 → 确定性帧计划。───────────────────────────────


def plan_from_data(
    fc: Dict[str, Any],
    *,
    product_kind: str,
    layer_id: str,
    ref_id: str,
    content_revision: Optional[int],
    field: Optional[str] = None,
    split_field: Optional[str] = None,
    fields: Optional[Sequence[str]] = None,
    time_field: Optional[str] = None,
    extents: Optional[Sequence[Sequence[float]]] = None,
    frames_limit: int = MAX_PLAN_FRAMES,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """纯函数：FeatureCollection + 参数 → 帧计划（无 IO、无写入）。

    返回 ``{"ok": True, "frames": [...], "disclosures": [...], "grid": {...}}``
    或 typed refusal dict。frames 每项携带 frame_id/title/binding
    （where 或 time_window）/参与共享分级的过滤后数据。
    """
    if frames_limit < 2 or frames_limit > MAX_PLAN_FRAMES:
        return _refusal(
            "frames_limit_invalid",
            f"frames_limit 必须在 2..{MAX_PLAN_FRAMES}（资源有界声明）",
        )
    disclosures: List[str] = []
    frames: List[Dict[str, Any]] = []

    if product_kind == "time_series":
        from app.services.gis_harness.temporal_products.frames import detect_time_binding

        probe = detect_time_binding(fc, time_field=time_field)
        if not probe.ok:
            return _refusal(
                "time_binding_failed", probe.message, time_field=probe.time_field)
        if "single_period" in probe.quality:
            return _refusal(
                "single_period_unsupported",
                f"数据仅覆盖单一时间期（{probe.buckets[0].label}），"
                "无法构成跨期小倍数产品；静态展示直接使用当前图层。",
                observed_label=probe.buckets[0].label,
            )
        buckets = probe.buckets
        if len(buckets) > frames_limit:
            disclosures.append(
                f"time_frames_truncated:{len(buckets)}→{frames_limit}")
            buckets = buckets[:frames_limit]
        for b in buckets:
            fid = _frame_id_slug("fp-t", b.label)
            b_start_iso = b.start.isoformat()
            b_end_iso = b.end.isoformat()
            frames.append({
                "frame_id": fid,
                "title": str(b.label),
                "time_window": {
                    "frame_id": fid, "label": str(b.label),
                    "start": b_start_iso, "end": b_end_iso,
                    "time_field": probe.time_field,
                    "time_representation": probe.representation,
                    "granularity": probe.granularity,
                    "timezone": "UTC",
                    "layer_id": layer_id,
                },
                "binding_where": {
                    "op": "and",
                    "terms": [
                        {"op": "gte", "field": probe.time_field,
                         "value": _bucket_lo(b_start_iso, probe)},
                        {"op": "lt", "field": probe.time_field,
                         "value": _bucket_hi(b_end_iso, probe)},
                    ],
                },
                "shared_fc": None,
            })

    elif product_kind == "category_compare":
        if not split_field:
            return _refusal("category_compare_split_field_missing",
                            "category_compare 需要 split_field（类别字段名）")
        values: List[str] = []
        for f in (fc.get("features") or []):
            props = f.get("properties") if isinstance(f, dict) else None
            if isinstance(props, dict):
                v = props.get(split_field)
                if v is None or isinstance(v, (dict, list, bool)):
                    continue
                s = str(v)
                if s and s not in values:
                    values.append(s)
        if len(values) < 2:
            return _refusal(
                "category_values_insufficient",
                f"字段 {split_field!r} 的去重类别值不足 2 个，无法构成对比产品",
                observed_values=values[:10],
            )
        if len(values) > frames_limit:
            disclosures.append(f"category_values_truncated:{len(values)}→{frames_limit}")
            values = sorted(values)[:frames_limit]
        for v in sorted(values):
            fid = _frame_id_slug("fp-c", v)
            where = {"op": "eq", "field": split_field, "value": v}
            frames.append({
                "frame_id": fid,
                "title": v,
                "time_window": None,
                "binding_where": where,
                "shared_fc": None,
            })

    elif product_kind == "variable_compare":
        if not fields or len(fields) < 2:
            return _refusal("variable_compare_fields_missing",
                            "variable_compare 需要 ≥2 个对比字段（fields）")
        for fld in fields[:frames_limit]:
            vals = _finite_field_values(fc, str(fld))
            if len(vals) < 2:
                return _refusal(
                    "field_not_quantitative",
                    f"字段 {fld!r} 有限数值不足（{len(vals)}）——不能作为对比变量",
                    field=str(fld),
                )
            fid = _frame_id_slug("fp-v", fld)
            frames.append({
                "frame_id": fid,
                "title": str(fld),
                "time_window": None,
                "binding_where": None,
                "variable_field": str(fld),
                "shared_fc": fc,  # 变量对比默认不共享分级（见 bind）
            })
        if len(fields) > frames_limit:
            disclosures.append(f"variable_fields_truncated:{len(fields)}→{frames_limit}")

    elif product_kind == "detail_frames":
        if not extents or len(extents) < 2:
            return _refusal("detail_frames_extents_missing",
                            "detail_frames 需要 ≥2 个显式 extent（主图+详图）")
        for i, ext in enumerate(extents[:frames_limit]):
            if not (isinstance(ext, (list, tuple)) and len(ext) == 4
                    and all(isinstance(v, (int, float)) for v in ext)
                    and ext[0] < ext[2] and ext[1] < ext[3]):
                return _refusal(
                    "detail_extent_invalid",
                    f"extent[{i}] 不是合法 WSEN（w<e, s<n）",
                )
            fid = _frame_id_slug("fp-d", i + 1)
            frames.append({
                "frame_id": fid,
                "title": f"视图 {i + 1}",
                "time_window": None,
                "binding_where": None,
                "extent": [float(v) for v in ext],
                "shared_fc": fc,
            })
        if len(extents) > frames_limit:
            disclosures.append(f"detail_extents_truncated:{len(extents)}→{frames_limit}")

    else:
        return _refusal("product_kind_unknown", f"未知产品种类 {product_kind!r}")

    if len(frames) < 2:
        return _refusal("plan_frames_insufficient",
                        "计划产出的画幅不足 2 个（小倍数产品的最低要求）")

    # 统一计算每帧参与共享分级的过滤后数据（binding_where 单一路径）。
    for f in frames:
        f["shared_fc"] = (
            _filter_fc_where(fc, f["binding_where"])
            if f.get("binding_where") else fc
        )

    bbox = _fc_bbox(fc)
    default_extent = _pad_bbox(bbox) if bbox else None
    columns = 3 if len(frames) >= 3 else len(frames)
    return {
        "ok": True,
        "product_kind": product_kind,
        "layer_id": layer_id,
        "ref_id": ref_id,
        "content_revision": content_revision,
        "field": field,
        "default_extent": default_extent,
        "frames": frames,
        "disclosures": disclosures,
        "grid": {"columns": columns, "title": title},
    }


def _bucket_lo(start_iso: str, probe: Any) -> Any:
    """桶下界值（按表示归一 —— year_int 转整数年份）。"""
    from app.services.gis_harness.temporal_products.time_filter import (
        normalize_boundary_value,
    )

    return normalize_boundary_value(
        start_iso, probe.representation, probe.granularity, "UTC")


def _bucket_hi(end_iso: str, probe: Any) -> Any:
    from app.services.gis_harness.temporal_products.time_filter import (
        normalize_boundary_value,
    )

    return normalize_boundary_value(
        end_iso, probe.representation, probe.granularity, "UTC")


# ── bind / update / unbind / status。─────────────────────────────────────


async def bind_frame_product(
    session_id: str,
    ref_id: str,
    *,
    product_kind: str,
    layer_id: Optional[str] = None,
    field: Optional[str] = None,
    split_field: Optional[str] = None,
    fields: Optional[Sequence[str]] = None,
    time_field: Optional[str] = None,
    extents: Optional[Sequence[Sequence[float]]] = None,
    frames_limit: int = MAX_PLAN_FRAMES,
    title: Optional[str] = None,
    shared: Optional[bool] = None,
    method: Optional[str] = None,
    k: Optional[int] = None,
    palette: Optional[str] = None,
    expected_revision: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """ref + 参数 → 计划 → 帧登记 + 产品契约 + 共享分级（CAS 写入）。

    成功回执 refs-only：产品摘要 + 逐帧绑定 + 共享分级状态；失败 typed
    refusal（不写 MapSpec，或如披露所述仅惰性 frames 登记残留）。
    """
    resolved, descriptor, fc = await _resolve_ref_fc(session_id, ref_id)
    if fc is None:
        return _refusal(
            "ref_unavailable",
            f"ref {ref_id!r} 不可读或不是 FeatureCollection（resolved={resolved}）",
        )
    spec = await _committed_spec(session_id)
    revision = descriptor.get("content_revision")

    # 目标层：显式给出 → 必须已存在（不静默挂层）；缺省 → 首个引用该
    # ref 的层，找不到则要求先建层（诚实义务）。
    layer_ids = [
        str(l.get("id"))
        for l in (spec.get("layers") or [])
        if isinstance(l, dict) and isinstance(l.get("id"), str)
    ]
    if layer_id:
        if layer_id not in layer_ids:
            return _refusal(
                "layer_not_found",
                f"目标层 {layer_id!r} 不在当前 MapSpec —— 先建/挂载图层再绑定产品",
                available_layers=layer_ids[:8],
            )
    else:
        layer_id = layer_ids[0] if layer_ids else ""
        if not layer_id:
            return _refusal(
                "layer_not_found",
                "当前 MapSpec 没有图层 —— 先创建专题图层再绑定多画幅产品",
            )

    existing = spec.get("frame_product")
    if isinstance(existing, dict) and existing.get("enabled") and not force:
        return _refusal(
            "already_bound",
            "frame_product 已绑定；重新绑定请带 force=true（重建画幅与共享分级）",
            current_frames=len(existing.get("frames") or []),
        )

    plan = plan_from_data(
        fc, product_kind=product_kind, layer_id=layer_id, ref_id=resolved,
        content_revision=revision, field=field, split_field=split_field,
        fields=fields, time_field=time_field, extents=extents,
        frames_limit=frames_limit, title=title,
    )
    if not plan.get("ok"):
        return plan

    # 共享分级（WP05）：time_series/category_compare 默认共享；
    # variable_compare/detail_frames 默认独立（显式 shared=true 才尝试）。
    want_shared = shared
    if want_shared is None:
        want_shared = product_kind in ("time_series", "category_compare")
    shared_cfg: Optional[Dict[str, Any]] = None
    shared_refusal: Optional[Dict[str, Any]] = None
    shared_field = field or plan.get("field")
    if want_shared and shared_field:
        from app.services.gis_harness.frame_products.shared_expression import (
            compute_shared_expression,
        )

        frame_datasets = [
            (f["frame_id"], f["shared_fc"]) for f in plan["frames"]
        ]
        outcome = compute_shared_expression(
            frame_datasets, shared_field,
            method=method, k=k, palette=palette,
        )
        if outcome.refused:
            shared_refusal = {
                "code": outcome.refusal_code, "message": outcome.refusal_message,
            }
            # 资格拒绝 → 不伪共享：回落独立图例（逐帧 legend=independent）。
            want_shared = False
        else:
            shared_cfg = outcome.config
    elif want_shared and not shared_field:
        shared_refusal = {
            "code": "shared_field_missing",
            "message": "共享分级需要 field（跨画幅同一取值字段）",
        }
        want_shared = False

    # 帧登记（layout.frames 整表：保留既有非产品帧 + 新产品帧）。
    layout = spec.get("layout") if isinstance(spec.get("layout"), dict) else {}
    existing_frames = [
        f for f in (layout.get("frames") or [])
        if isinstance(f, dict)
        and str(f.get("id")) not in {fr["frame_id"] for fr in plan["frames"]}
    ]
    default_extent = plan.get("default_extent")
    new_frames: List[Dict[str, Any]] = []
    for f in plan["frames"]:
        entry: Dict[str, Any] = {
            "id": f["frame_id"],
            "title": f["title"],
            "enabled": True,
        }
        extent = f.get("extent") or default_extent
        if extent:
            entry["extent"] = extent
        new_frames.append(entry)
    frames_result = await _apply(
        session_id,
        _setlayout_frames_intent(existing_frames + new_frames),
        reason="frame_product bind: 画幅身份登记",
        expected_revision=expected_revision,
    )
    if frames_result.is_error:
        return _refusal(
            "mutation_rejected",
            f"画幅登记被拒绝：{frames_result.error_msg}",
            correction_hint=frames_result.correction_hint,
        )

    # 产品契约。
    product_frames: List[Dict[str, Any]] = []
    for f in plan["frames"]:
        slot: Dict[str, Any] = {
            "frame_id": f["frame_id"],
            "title": f["title"],
            "legend": "shared" if want_shared else "independent",
        }
        data: Dict[str, Any] = {
            "ref": resolved,
            "layer_id": layer_id,
        }
        if revision is not None:
            data["content_revision"] = int(revision)
        bind_field = f.get("variable_field") or shared_field
        if bind_field:
            data["field"] = bind_field
        if f.get("binding_where"):
            data["where"] = f["binding_where"]
        if f.get("time_window"):
            data["time_window"] = f["time_window"]
        slot["data"] = data
        product_frames.append(slot)

    config: Dict[str, Any] = {
        "schema_version": "frame_product.v1",
        "enabled": True,
        "product_kind": product_kind,
        "grid": plan["grid"],
        "frames": product_frames,
        "active_frame": product_frames[0]["frame_id"],
        "quality": [],
        "disclosures": [d for d in plan["disclosures"]][:8],
    }
    if shared_cfg is not None:
        config["shared_expression"] = shared_cfg
    if shared_refusal is not None:
        config["disclosures"] = (config["disclosures"] + [
            f"shared_refused:{shared_refusal['code']}"])[:8]

    product_result = await _apply(
        session_id,
        _set_frame_product_intent(config=config),
        reason="frame_product bind: 产品契约",
    )
    if product_result.is_error:
        return _refusal(
            "mutation_rejected",
            f"产品契约写入被拒绝：{product_result.error_msg}",
            correction_hint=product_result.correction_hint,
            note="画幅登记已写入（惰性无害 —— 无产品引用不参与渲染）",
        )

    return _product_summary(
        session_id, product_result.mutation_revision
        if hasattr(product_result, "mutation_revision") else None,
        plan=plan, shared_cfg=shared_cfg, shared_refusal=shared_refusal,
        layer_id=layer_id, ref=resolved,
    )


def _setlayout_frames_intent(frames: List[Dict[str, Any]]):
    from app.services.mapspec.intents import SetLayoutIntent

    return SetLayoutIntent(frames=frames)


def _set_frame_product_intent(
    *, config: Optional[Dict[str, Any]] = None,
    patch: Optional[Dict[str, Any]] = None,
    remove: bool = False,
):
    from app.services.mapspec.intents import SetFrameProductIntent

    return SetFrameProductIntent(config=config, patch=patch, remove=remove)


def _product_summary(
    session_id: str,
    revision: Optional[int],
    *,
    plan: Optional[Dict[str, Any]] = None,
    shared_cfg: Optional[Dict[str, Any]] = None,
    shared_refusal: Optional[Dict[str, Any]] = None,
    layer_id: str = "",
    ref: str = "",
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"status": "ok", "session_id": session_id}
    if revision is not None:
        out["mutation_revision"] = revision
    if layer_id:
        out["layer_id"] = layer_id
    if ref:
        out["ref"] = ref
    if plan is not None:
        out["product_kind"] = plan.get("product_kind")
        out["frames"] = [
            {"frame_id": f["frame_id"], "title": f["title"]} for f in plan["frames"]
        ]
        out["disclosures"] = plan.get("disclosures", [])
    if shared_cfg is not None:
        out["shared_expression"] = {
            "domain_status": shared_cfg.get("domain_status"),
            "breaks": shared_cfg.get("breaks"),
            "k": shared_cfg.get("k"),
            "sample_size": shared_cfg.get("sample_size"),
        }
    if shared_refusal is not None:
        out["shared_expression_refused"] = shared_refusal
    return out


async def update_frame_product(
    session_id: str,
    patch: Dict[str, Any],
    *,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """增量控制（键级 patch：active_frame/frames/grid/shared_expression/…）。"""
    if not isinstance(patch, dict) or not patch:
        return _refusal("patch_empty", "patch 不能为空")
    result = await _apply(
        session_id, _set_frame_product_intent(patch=patch),
        reason="frame_product update", expected_revision=expected_revision,
    )
    if result.is_error:
        return _refusal(
            "mutation_rejected", result.error_msg or "patch 被拒绝",
            correction_hint=result.correction_hint,
        )
    out: Dict[str, Any] = {"status": "ok"}
    if getattr(result, "mutation_revision", None) is not None:
        out["mutation_revision"] = result.mutation_revision
    return out


async def unbind_frame_product(
    session_id: str, *, expected_revision: Optional[int] = None
) -> Dict[str, Any]:
    """解绑产品语义（layout.frames 画幅登记保留 —— 身份不随产品消失）。"""
    result = await _apply(
        session_id, _set_frame_product_intent(remove=True),
        reason="frame_product unbind", expected_revision=expected_revision,
    )
    if result.is_error:
        return _refusal("mutation_rejected", result.error_msg or "解绑被拒绝")
    return {
        "status": "ok",
        "note": "产品语义已移除；layout.frames 画幅登记保留（可重新绑定）",
    }


async def frame_product_status(session_id: str) -> Dict[str, Any]:
    """refs-only 产品状态摘要（逐帧绑定/共享分级/资格，无要素载荷）。"""
    spec = await _committed_spec(session_id)
    product = spec.get("frame_product")
    if not isinstance(product, dict) or not product:
        return {"status": "ok", "bound": False}
    from app.services.gis_harness.frame_products.resolver import (
        resolve_frame_document,
    )

    frames_out: List[Dict[str, Any]] = []
    for slot in (product.get("frames") or []):
        if not isinstance(slot, dict):
            continue
        fid = str(slot.get("frame_id") or "")
        entry: Dict[str, Any] = {"frame_id": fid, "title": slot.get("title")}
        data = slot.get("data") if isinstance(slot.get("data"), dict) else {}
        if data:
            entry["binding"] = {
                "ref": data.get("ref"), "layer_id": data.get("layer_id"),
                "field": data.get("field"),
                "content_revision": data.get("content_revision"),
                "has_where": bool(data.get("where")),
                "has_time_window": bool(data.get("time_window")),
            }
        try:
            res = resolve_frame_document(spec, fid)
            entry["resolve"] = {
                "fingerprint": res.fingerprint,
                "applied": list(res.applied),
                "filtered_feature_count": res.filtered_feature_count,
            }
        except Exception as exc:  # noqa: BLE001 — 状态面如实披露解析失败
            entry["resolve"] = {"error": f"{type(exc).__name__}: {exc}"}
        frames_out.append(entry)
    shared = product.get("shared_expression")
    return {
        "status": "ok",
        "bound": True,
        "enabled": product.get("enabled"),
        "product_kind": product.get("product_kind"),
        "grid": product.get("grid"),
        "active_frame": product.get("active_frame"),
        "frames": frames_out,
        "shared_expression": (
            {
                "field": shared.get("field"),
                "domain_status": shared.get("domain_status"),
                "breaks": shared.get("breaks"),
                "k": shared.get("k"),
                "eligibility": shared.get("eligibility"),
            }
            if isinstance(shared, dict) else None
        ),
        "quality": product.get("quality", []),
        "disclosures": product.get("disclosures", []),
    }
    # WP11：逐画幅终验（有界 frame 证据；观察回执缺席 = 诚实 not_observed）。
    from app.services.gis_harness.frame_products.verification import (
        verify_frame_product,
    )

    out["verification"] = verify_frame_product(spec)
    return out
