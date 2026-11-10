"""G20 领域服务门面 —— UI 与 Pi 唯一共享入口。

回执至少回答四个问题：哪个数据版本 / 哪个表达版本 / 统计来自什么采样口径 /
用户看到的是已提交状态还是临时预览。像元查询与表达提交分开建模：查询不写
MapSpec、不生成分析产物、不推进修订。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.schemas.raster_spec import (
    RASTER_EXPRESSION_SCHEMA_VERSION,
    RasterExpressionError,
    RasterExpressionSpec,
)
from app.services.raster_authoring import descriptor as descriptor_mod
from app.services.raster_authoring.contract import (
    bounded_projection,
    parse_committed_style,
    resolve_expression,
)
from app.services.raster_authoring.legend import build_legend, expression_statement
from app.services.raster_authoring.probe import probe as probe_pixels
from app.services.raster_authoring.source import RasterSource, resolve_source

_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine

        _ENGINE = MapSpecLifecycleEngine()
    return _ENGINE


async def _read_mapspec(session_id: str) -> Dict[str, Any]:
    spec = await _engine().store.get_mapspec(session_id)
    return spec if isinstance(spec, dict) else {}


async def current_revision(session_id: str) -> int:
    from app.services.session_data import session_data_manager

    getter = getattr(session_data_manager, "get_state_field", None)
    raw = await getter(session_id, "_cartographic_mutation_revision") if callable(getter) else None
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def source_id_for(ref_id: str) -> str:
    """稳定、URL 安全的源 id（同源可被多个层以不同样式引用）。"""
    return "raster-" + hashlib.sha256(ref_id.encode("utf-8")).hexdigest()[:12]


def _find_layer(mapspec: Mapping[str, Any], layer_id: str) -> Optional[Dict[str, Any]]:
    for layer in mapspec.get("layers") or []:
        if isinstance(layer, dict) and layer.get("id") == layer_id:
            return layer
    return None


def _source_entry(source: RasterSource) -> Dict[str, Any]:
    bounds = descriptor_mod._epsg4326_bounds(source) or (
        list(source.descriptor.bounds) if source.descriptor.bounds else [0, 0, 0, 0]
    )
    return {
        "type": "numeric_raster",
        "schema_version": RASTER_EXPRESSION_SCHEMA_VERSION,
        "refId": source.ref_id,
        "bounds": bounds,
        "crs": source.crs,
        "dataRevision": source.data_revision,
        "dataFingerprint": source.content_fingerprint,
        "bandCount": source.band_count,
    }


def _layer_payload(layer_id: str, sid: str, spec: RasterExpressionSpec,
                   legend: Dict[str, Any], existing: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    paint = dict(existing.get("paint") or {}) if isinstance(existing, Mapping) else {}
    # 透明度只在服务端瓦片里应用一次；不落 MapLibre raster-opacity。
    paint.pop("opacity", None)
    layer: Dict[str, Any] = {
        "id": layer_id,
        "source": sid,
        "type": "raster",
        "paint": paint,
        "raster_style": spec.model_dump(mode="json"),
        "legend_spec": legend,
    }
    if isinstance(existing, Mapping) and "visible" in existing:
        layer["visible"] = existing["visible"]
    return layer


def _receipt(*, spec: RasterExpressionSpec, source: RasterSource, legend: Dict[str, Any],
             statement: Dict[str, Any], layer_id: str, sid: str, committed: bool,
             revision: Optional[int] = None, mutations: int = 0,
             mapspec_fingerprint: Optional[str] = None) -> Dict[str, Any]:
    return {
        "success": True,
        "committed": committed,
        "state": "committed" if committed else "preview",
        "layer_id": layer_id,
        "source_id": sid,
        "ref_id": source.ref_id,
        "mapspec_revision": revision,
        "mapspec_fingerprint": mapspec_fingerprint,
        "mutations": mutations,
        # ① 数据版本 ② 表达版本 ③ 统计口径 ④ 提交状态
        "data_revision": spec.data_revision,
        "expression_version": spec.schema_version,
        "style_id": spec.style_id,
        "statistics_provenance": {
            "source": spec.stretch.source,
            "method": spec.stretch.method,
            "sample_count": spec.stretch.sample_count,
            "approximate": spec.stretch.approximate,
        },
        "expression": spec.model_dump(mode="json"),
        "legend": legend,
        "expression_statement": statement,
    }


async def describe_raster(
    session_id: str, ref_id: str, *, owner_token: Optional[str] = None,
    histogram_bands: Optional[List[int]] = None, buckets: Optional[int] = None,
    trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    source = await resolve_source(
        session_id, ref_id, owner_token, trusted_dispatch=trusted_dispatch
    )
    return descriptor_mod.describe(source, histogram_bands=histogram_bands, buckets=buckets)


async def probe_raster(
    session_id: str, ref_id: str, points: Sequence[Any], *,
    crs: Optional[str] = "EPSG:4326", bands: Optional[Sequence[int]] = None,
    owner_token: Optional[str] = None,
    expected_data_revision: Optional[str] = None,
    trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    source = await resolve_source(
        session_id, ref_id, owner_token, trusted_dispatch=trusted_dispatch
    )
    return probe_pixels(
        source, points, crs=crs, bands=bands,
        expected_data_revision=expected_data_revision,
    )


async def validate_expression(
    session_id: str, ref_id: str, request: Mapping[str, Any], *,
    layer_id: Optional[str] = None, owner_token: Optional[str] = None,
    trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """预览：完整校验 + 图例，但**不写 MapSpec、不推进修订**。"""
    source = await resolve_source(
        session_id, ref_id, owner_token, trusted_dispatch=trusted_dispatch
    )
    spec = resolve_expression(request, source)
    legend = build_legend(spec, source)
    statement = expression_statement(spec, source)
    lid = layer_id or f"raster-{hashlib.sha256(ref_id.encode()).hexdigest()[:8]}"
    receipt = _receipt(
        spec=spec, source=source, legend=legend, statement=statement,
        layer_id=lid, sid=source_id_for(ref_id), committed=False,
        revision=await current_revision(session_id),
    )
    receipt["projection"] = bounded_projection(spec, source)
    return receipt


def _apply_result_error(result: Any) -> None:
    if result.superseded:
        raise RasterExpressionError(
            "CAS_SUPERSEDED",
            "MapSpec revision changed during the commit; nothing was written",
            status_code=409,
            correction_hint="Re-read the MapSpec revision and resubmit.",
        )
    if result.is_error:
        raise RasterExpressionError(
            "RENDER_FAILED",
            str(result.error_msg or "MapSpec mutation was rejected"),
            status_code=422,
            correction_hint=str(result.correction_hint or ""),
        )


async def commit_expression(
    session_id: str, ref_id: str, layer_id: str, request: Mapping[str, Any],
    *, expected_revision: int, owner_token: Optional[str] = None,
    origin: str = "user", actor: str = "raster_authoring",
    mutation_id: Optional[str] = None, trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """一次可恢复的持久提交（同源双层互不串色；样式+图例原子一致）。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent, UpsertSourceIntent

    source = await resolve_source(
        session_id, ref_id, owner_token, trusted_dispatch=trusted_dispatch
    )
    spec = resolve_expression(request, source)
    legend = build_legend(spec, source)
    statement = expression_statement(spec, source)
    sid = source_id_for(ref_id)
    mapspec = await _read_mapspec(session_id)
    existing_layer = _find_layer(mapspec, layer_id)
    sources = mapspec.get("sources") or {}
    existing_source = sources.get(sid) if isinstance(sources, dict) else None
    entry = _source_entry(source)
    mutations = 0
    revision = int(expected_revision)

    # 首挂载 / 数据修订变化 → 先更新数据身份（不携带样式，不重算数据）。
    if not isinstance(existing_source, dict) or existing_source.get("dataRevision") != source.data_revision:
        src_result = await apply_gis_mutation(
            session_id, UpsertSourceIntent(source_id=sid, source=entry),  # type: ignore[arg-type]
            origin=origin, actor=actor, expected_revision=revision,
            engine=_engine(), mutation_id=(f"{mutation_id}:source" if mutation_id else None),
        )
        _apply_result_error(src_result)
        revision = int(src_result.mutation_revision or revision)
        mutations += 1
        mapspec = await _read_mapspec(session_id)
        existing_layer = _find_layer(mapspec, layer_id)

    layer = _layer_payload(layer_id, sid, spec, legend, existing_layer)
    result = await apply_gis_mutation(
        session_id, UpsertLayerIntent(layer=layer, source_data=None),  # type: ignore[arg-type]
        origin=origin, actor=actor, expected_revision=revision,
        engine=_engine(), mutation_id=(f"{mutation_id}:layer" if mutation_id else None),
    )
    _apply_result_error(result)
    mutations += 1
    return _receipt(
        spec=spec, source=source, legend=legend, statement=statement,
        layer_id=layer_id, sid=sid, committed=True,
        revision=int(result.mutation_revision or revision), mutations=mutations,
        mapspec_fingerprint=result.mapspec_fingerprint,
    )


@dataclass(frozen=True)
class CommittedExpression:
    """已提交表达的解析结果（tile / export / 图例共用的冻结投影）。"""

    layer_id: str
    source_id: str
    ref_id: str
    spec: RasterExpressionSpec
    data_revision: str
    layer: Dict[str, Any]
    mapspec_revision: int


async def resolve_committed_expression(
    session_id: str, layer_id: str, *, style_id: Optional[str] = None,
) -> CommittedExpression:
    """从**当前**权威 MapSpec 校验楼层样式 / 数据绑定。

    ``style_id`` 不匹配 → 409 STALE_STYLE（旧 URL 绝不出旧像素）；
    source 的 dataRevision 与样式绑定不一致 → 409 STALE_BINDING。
    """
    mapspec = await _read_mapspec(session_id)
    layer = _find_layer(mapspec, layer_id)
    if layer is None:
        raise RasterExpressionError(
            "STALE_STYLE", f"layer {layer_id!r} is no longer in the authoritative MapSpec",
            status_code=409,
        )
    spec = parse_committed_style(layer)
    if style_id and style_id != spec.style_id:
        raise RasterExpressionError(
            "STALE_STYLE",
            "tile URL refers to a superseded raster expression",
            field="style_id", status_code=409,
            correction_hint="Recompile the layer from the current MapSpec revision.",
        )
    sources = mapspec.get("sources") or {}
    source_entry = sources.get(layer.get("source")) if isinstance(sources, dict) else None
    if not isinstance(source_entry, dict) or source_entry.get("type") != "numeric_raster":
        raise RasterExpressionError(
            "SOURCE_NOT_NUMERIC_RASTER",
            "layer source is not a numeric raster; baked PNG layers keep their original path",
            status_code=409,
        )
    if source_entry.get("dataRevision") != spec.data_revision:
        raise RasterExpressionError(
            "STALE_BINDING",
            "the committed style is bound to a different data revision",
            status_code=409,
            correction_hint="Rebind the raster expression to the current data revision.",
        )
    return CommittedExpression(
        layer_id=layer_id,
        source_id=str(layer.get("source")),
        ref_id=str(source_entry.get("refId") or ""),
        spec=spec,
        data_revision=str(source_entry.get("dataRevision") or ""),
        layer=dict(layer),
        mapspec_revision=await current_revision(session_id),
    )


async def frozen_layers(session_id: str, expected_revision: Optional[int] = None) -> Dict[str, Any]:
    """一次性冻结 MapSpec 修订；导出期间样式变化不影响已冻结快照。"""
    revision = await current_revision(session_id)
    if expected_revision is not None and int(expected_revision) != revision:
        raise RasterExpressionError(
            "CAS_SUPERSEDED",
            f"export revision {expected_revision} != current MapSpec revision {revision}",
            status_code=409,
            correction_hint="Re-read the MapSpec revision and export the current committed state.",
        )
    mapspec = await _read_mapspec(session_id)
    numeric: List[Dict[str, Any]] = []
    omitted: List[str] = []
    for layer in mapspec.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        lid = str(layer.get("id") or "")
        if not isinstance(layer.get("raster_style"), dict):
            omitted.append(lid)
            continue
        committed = await resolve_committed_expression(session_id, lid)
        numeric.append({
            "layer_id": lid,
            "spec": committed.spec,
            "ref_id": committed.ref_id,
        })
    return {
        "revision": revision,
        "mapspec": mapspec,
        "numeric_layers": numeric,
        "omitted_layers": omitted,
    }


async def export_snapshot(
    session_id: str, *, expected_revision: Optional[int] = None,
    fmt: str = "png", owner_token: Optional[str] = None,
    trusted_dispatch: bool = False,
) -> Dict[str, Any]:
    """冻结修订 → 渲染 → 组合（真实文件字节，调用方负责受权落盘）。"""
    from app.services.raster_authoring.snapshot import build_entry, compose

    frozen = await frozen_layers(session_id, expected_revision)
    entries = []
    for item in frozen["numeric_layers"]:
        source = await resolve_source(
            session_id, item["ref_id"], owner_token,
            trusted_dispatch=trusted_dispatch,
        )
        if source.data_revision != item["spec"].data_revision:
            raise RasterExpressionError(
                "STALE_BINDING",
                "source data revision changed after the export snapshot was frozen",
                status_code=409,
            )
        entries.append(build_entry(item["layer_id"], item["spec"], source))
    artifact = compose(
        entries, fmt=fmt,
        frozen={
            "mapspec_revision": frozen["revision"],
            "omitted_layers": frozen["omitted_layers"],
            "format": fmt,
        },
    )
    artifact["frozen_revision"] = frozen["revision"]
    artifact["frozen"] = frozen
    return artifact
