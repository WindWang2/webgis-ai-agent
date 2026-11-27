"""G39 输入快照、身份与授权 — 冻结即计划绑定（WP03）。

纪律：
- 每次读取/执行/下载重新核 actor 与 session/project 权限（owner_token 由
  路由/工具层注入；后台任务不信任客户端字符串）；
- G24 选择句柄只在**准备阶段**消费：materialize 成会话 ref 后进计划，
  不把 10 分钟 TTL 的 handle 当长期作业身份（S22）；
- 点/线输入绑定完整源内容 digest（特征 id + 坐标 + 有界属性）—— 修订
  后执行按 STALE_BINDING 拒绝，不沿用旧站位（S20）；
- 源栅格沿 RasterSource 解析；`stat_fingerprint`（dev/ino/mtime_ns/size）
  与 data_revision 一起入绑定。弱指纹（mtime/首尾摘要）不被当作不可变
  证明 —— 完整可重现执行依赖执行期逐批 stat 复核（reader_schedule），
  复核不过即弃件（S31/S34）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional, Tuple

from app.services.raster_sampling import reader_schedule
from app.services.raster_sampling.contracts import (
    RasterSamplingError,
    SourceBinding,
)

_MAX_FC_FEATURES_FOR_HASH = 50_000
_MAX_SELECTION_FEATURES = 10_000


def _canonical_fc_digest(fc: Dict[str, Any]) -> str:
    """FeatureCollection 内容 digest（id+坐标+属性键值；顺序敏感）。"""
    h = hashlib.sha256()
    features = fc.get("features") if isinstance(fc, dict) else None
    h.update(b"fc|v1|")
    for f in features or []:  # type: ignore[union-attr]
        if not isinstance(f, dict):
            continue
        geom = f.get("geometry")
        if not isinstance(geom, dict):
            geom = {}
        coords = geom.get("coordinates")
        fid = f.get("id")
        props = f.get("properties") if isinstance(f.get("properties"), dict) else {}
        bounded = {str(k): str(v)[:128] for k, v in list(props.items())[:16]}
        blob = json.dumps([fid, str(geom.get("type")), coords, bounded],
                          ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)
        h.update(blob.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()[:24]


async def load_fc_ref(
    session_id: str,
    ref_id: str,
    owner_token: Optional[str],
    *,
    trusted_dispatch: bool = False,
) -> Tuple[Dict[str, Any], str]:
    """授权读取会话 FC ref → (payload, content digest)。"""
    if not ref_id or len(ref_id) > 128 or any(c.isspace() for c in ref_id):
        raise RasterSamplingError(
            "SOURCE_UNAVAILABLE", "invalid points/lines ref_id",
            field="ref_id", status_code=404)
    from app.services.session_data import session_data_manager

    if trusted_dispatch:
        data = await session_data_manager.get(session_id, ref_id)
        if data is None:
            raise RasterSamplingError(
                "SOURCE_UNAVAILABLE",
                f"ref {ref_id} is not available for this session owner",
                field="ref_id", status_code=404)
    else:
        res = await session_data_manager.get_ref_data(
            session_id, ref_id, owner_token=owner_token)
        if not res.success or not res.data:
            raise RasterSamplingError(
                "SOURCE_UNAVAILABLE",
                f"ref {ref_id} is not available for this session owner",
                field="ref_id",
                status_code=403 if res.error_type == "PermissionDenied" else 404)
        data = res.data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            data = {"type": "FeatureCollection", "features": []}
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise RasterSamplingError(
            "INVALID_GEOMETRY",
            f"ref {ref_id} does not carry a GeoJSON FeatureCollection",
            field="ref_id", status_code=422)
    features = data.get("features")
    if not isinstance(features, list):
        raise RasterSamplingError(
            "INVALID_GEOMETRY", f"ref {ref_id} features list is missing",
            field="ref_id", status_code=422)
    if len(features) > _MAX_FC_FEATURES_FOR_HASH:
        raise RasterSamplingError(
            "BUDGET_EXCEEDED",
            f"ref {ref_id} carries {len(features)} features (cap "
            f"{_MAX_FC_FEATURES_FOR_HASH})",
            field="ref_id", status_code=413)
    return data, _canonical_fc_digest(data)


async def materialize_selection_input(
    session_id: str,
    layer_ref_id: str,
    handle: str,
    owner_token: Optional[str],
) -> Dict[str, Any]:
    """G24 handle → 精确冻结 scope（materialize 成会话 ref，一次性）。

    >50 点 / 截断 ids 的选择不在准备时被「只采前 50」—— materialize 的
    over_budget typed 拒绝直接传导给调用方（S22）。
    """
    from app.services.selection_handles import (
        SelectionHandleError,
        handle_store,
        materialize_selection,
    )
    from app.services.session_data import session_data_manager

    try:
        entry = handle_store.get(session_id, layer_ref_id, handle)
    except SelectionHandleError as exc:
        raise RasterSamplingError(
            "SOURCE_UNAVAILABLE",
            f"selection handle is not usable: {getattr(exc, 'code', 'not_found')}",
            field="selection", status_code=404) from exc
    _fc, _digest = await load_fc_ref(session_id, layer_ref_id, owner_token)

    async def _store(fc: Dict[str, Any]) -> str:
        ref_id = await session_data_manager.store(session_id, fc)
        if not ref_id:
            raise RasterSamplingError(
                "CONTENT_WRITE_FAILED", "selection materialization failed",
                field="selection")
        return str(ref_id)

    try:
        out = await materialize_selection(entry, _fc, store_call=_store)
    except SelectionHandleError as exc:
        code = getattr(exc, "code", "bad_request")
        status = 413 if code == "over_budget" else 422
        raise RasterSamplingError(
            "BUDGET_EXCEEDED" if code == "over_budget" else "INVALID_PLAN",
            f"selection could not be frozen: {code}",
            field="selection", status_code=status,
            correction_hint="Narrow the selection or export it as its own "
                            "layer ref first.") from exc
    return {
        "ref_id": out["ref_id"],
        "feature_count": out["feature_count"],
        "coarse_bbox": out["coarse_bbox"],
        "revision": out["revision"],
    }


def freeze_source_binding(
    source: Any, binding_overrides: Optional[Dict[str, Any]] = None
) -> SourceBinding:
    """Resolved RasterSource → 不可变绑定（data_revision + stat 代次）。

    band_semantics 冻结**有效语义**（用户声明 > 数据集 classification
    元数据 > 缺省不写入）—— 15b：早前只存用户 overrides，而
    ``_early_method_check`` 又拿来源标记（"declared"/"metadata"）比对
    "continuous"，元数据标记的连续栅格恒被判 undeclared 拒绝插值。
    """
    overrides = binding_overrides or {}
    bands = overrides.get("bands") or [1]
    declared_sem = {str(k): str(v) for k, v in
                    (overrides.get("band_semantics") or {}).items()}
    from app.services.raster_sampling import kernel as _kernel

    resolved_semantics: Dict[str, str] = {}
    for b in bands:
        facts = _kernel.band_facts_from_source(
            source, int(b), declared_semantics=declared_sem.get(str(b)))
        if facts.semantic in ("categorical", "continuous"):
            resolved_semantics[str(b)] = facts.semantic
    stat = reader_schedule.stat_fingerprint(source.path)
    return SourceBinding(
        ref_id=source.ref_id,
        label=str(overrides.get("label") or source.ref_id),
        data_revision=source.data_revision,
        stat_fingerprint=json.dumps(stat, sort_keys=True, separators=(",", ":")),
        bands=[int(b) for b in bands],
        band_labels={str(k): str(v) for k, v in
                     (overrides.get("band_labels") or {}).items()},
        band_units={str(k): str(v) for k, v in
                    (overrides.get("band_units") or {}).items()},
        band_semantics=resolved_semantics,
        band_semantics_source=(
            "declared" if declared_sem else "metadata"),
        crs=source.crs,
    )


def verify_binding_generation(
    binding: SourceBinding, source: Any
) -> None:
    """执行前源绑定复核：data_revision 或 stat 代次漂移 → typed 拒绝。"""
    if binding.data_revision and source.data_revision != binding.data_revision:
        raise RasterSamplingError(
            "SOURCE_BINDING_CHANGED",
            f"source {binding.ref_id} data revision moved "
            f"({binding.data_revision} → {source.data_revision}); the plan "
            "binds older content — refresh the plan (S20)",
            field="sources", status_code=409,
            correction_hint="Update the plan to the current source revision "
                            "and re-preview.")
    now_stat = reader_schedule.stat_fingerprint(source.path)
    try:
        frozen = json.loads(binding.stat_fingerprint or "{}")
    except ValueError:
        frozen = {}
    if frozen and not reader_schedule.same_generation(frozen, now_stat):
        raise RasterSamplingError(
            "SOURCE_BINDING_CHANGED",
            f"source {binding.ref_id} file generation changed since the plan "
            "was frozen (mtime/size/inode mismatch)",
            field="sources", status_code=409,
            correction_hint="Re-resolve the raster and refresh the plan.")


def pick_line_feature(
    fc: Dict[str, Any], feature_id: str
) -> Tuple[Dict[str, Any], str]:
    """线路 FC → 单一绑定 feature（id 精确匹配；未指明时必须恰一条线）。"""
    features = [f for f in (fc.get("features") or [])
                if isinstance(f, dict)
                and isinstance(f.get("geometry"), dict)
                and f["geometry"].get("type")
                in ("LineString", "MultiLineString")]
    if not features:
        raise RasterSamplingError(
            "INVALID_GEOMETRY",
            "lines ref carries no LineString/MultiLineString features",
            field="lines_ref", status_code=422)
    if feature_id:
        for f in features:
            fid = f.get("id")
            if fid is None and isinstance(f.get("properties"), dict):
                fid = f["properties"].get("id")
            if str(fid) == str(feature_id):
                return f, str(fid)
        raise RasterSamplingError(
            "INVALID_GEOMETRY",
            f"line feature {feature_id!r} not found in lines ref",
            field="line_feature_id", status_code=404)
    if len(features) != 1:
        raise RasterSamplingError(
            "INVALID_GEOMETRY",
            f"lines ref carries {len(features)} line features — name one via "
            "line_feature_id",
            field="line_feature_id", status_code=422)
    f = features[0]
    fid = f.get("id")
    if fid is None and isinstance(f.get("properties"), dict):
        fid = f["properties"].get("id")
    return f, str(fid or "line-0")
