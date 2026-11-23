"""MapSpec Layer Ingestion Pipeline (app/services/mapspec/pipeline.py).

负责图层 Ingestion 规则转换 (Raster PNG 渲染, Spatial Analysis 转换,
GeoJSON 自动 Profiling, DataFabric 源代理与 View 计算)。
"""
from pathlib import Path
import hashlib
import json
import logging
from typing import Any, Dict, Optional, Tuple

from app.services.spatial_meta_profiler import profile_geojson_source
from app.services.mapspec_source import store_data, profile_data, is_raster_entry, is_data_fabric_entry
from app.services.mapspec.store import view_has_center

logger = logging.getLogger(__name__)


def _content_fingerprint(value: Any) -> str:
    """Hash JSON incrementally so identity does not require a second full copy."""
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    for chunk in encoder.iterencode(value):
        digest.update(chunk.encode("utf-8"))
    return "data-sha256:" + digest.hexdigest()


def process_layer_ingestion(
    mapspec: Dict[str, Any],
    layer: Dict[str, Any],
    source_data: Optional[Any] = None,
    session_dir: Optional[Path] = None,
    session_id: Optional[str] = None,
    ref_content_revisions: Optional[Dict[str, int]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
    """处理图层转换与封装逻辑 (支持 GeoJSON, Raster, DataFabric 延迟/实例化源)"""
    processed_layer = dict(layer)
    processed_source_data = source_data
    # result_ref already present on the caller's layer describes the previous
    # generation (a converter may overwrite it with this ingestion's own ref).
    _incoming_provenance = (
        layer.get("provenance") if isinstance(layer.get("provenance"), dict) else {}
    )
    incoming_result_ref = _incoming_provenance.get("result_ref")

    is_raster = False
    if isinstance(processed_source_data, dict):
        from app.services.raster_cartography_converter import is_raster_source
        is_raster = is_raster_source(processed_source_data)

    if is_raster:
        from app.services.raster_cartography_converter import convert_raster_to_mapspec_layer

        raster_layer, legend, png, raster_source_data = convert_raster_to_mapspec_layer(
            processed_source_data, processed_layer, session_dir=session_dir
        )
        processed_layer = raster_layer
        processed_source_data = raster_source_data

    elif isinstance(processed_source_data, dict):
        from app.services.analysis_cartography_converter import (
            is_analysis_result,
            convert_analysis_to_mapspec_layer,
        )
        if is_analysis_result(processed_source_data):
            converted_layer, inline_geojson, _ = convert_analysis_to_mapspec_layer(
                processed_source_data, processed_layer
            )
            processed_layer = converted_layer
            processed_source_data = inline_geojson

    source_id = processed_layer.get("source", "default_source")
    sources = mapspec.get("sources", {})
    existing_entry = sources.get(source_id, {"type": "geojson"})
    source_entry = dict(existing_entry)
    # The carrier this source entry currently resolves to. An upsert that
    # supplies new data must replace it; a provenance.result_ref naming this
    # same prior carrier belongs to the previous generation, not to this
    # ingestion, and must not redirect the entry back to the old ref.
    prior_carrier_ref = existing_entry.get("ref_id") or existing_entry.get("ref")

    if processed_source_data is not None:
        # An explicit upsert replaces the prior source generation.  Keeping an
        # old inlineData/profile beside a new URL/ref made semantic review use
        # stale geometry/field evidence.  Presentation metadata survives; data
        # carrier and its derived profile do not.
        for key in (
            "inlineData", "url", "dataPath", "imageRef", "bounds",
            "imageSize", "profile", "profile_fingerprint", "ref", "ref_id",
            "data_fingerprint",
        ):
            source_entry.pop(key, None)
        store_data(source_entry, processed_source_data)
        if isinstance(processed_source_data, dict):
            source_entry.setdefault(
                "data_fingerprint", _content_fingerprint(processed_source_data)
            )
        elif isinstance(processed_source_data, str):
            source_entry.setdefault(
                "data_fingerprint",
                "url-sha256:" + hashlib.sha256(processed_source_data.encode()).hexdigest(),
            )

    if is_raster and isinstance(source_entry.get("imageRef"), str):
        # The rendered PNG is the display result; source_ref retained by the
        # converter is the input raster. Preserve both roles explicitly.
        raster_provenance = (
            dict(processed_layer.get("provenance"))
            if isinstance(processed_layer.get("provenance"), dict) else {}
        )
        raster_provenance["result_ref"] = source_entry["imageRef"]
        processed_layer["provenance"] = raster_provenance

    provenance = (
        processed_layer.get("provenance")
        if isinstance(processed_layer.get("provenance"), dict) else {}
    )
    # ``source_ref`` names an analysis input, not the displayable output. Only
    # an explicit ``result_ref`` may replace the source carrier; conflating the
    # two can make a layer point at its input while discarding its actual result.
    # A ref supplied by THIS ingestion's source_data is authoritative (e.g. the
    # bivariate derived display ref whose fields the paint references): the
    # provenance result_ref names a PREVIOUS generation and must not clobber it.
    explicit_ref: Optional[str] = None
    if isinstance(processed_source_data, dict):
        _cand = processed_source_data.get("ref_id")
        if isinstance(_cand, str) and _cand.startswith("ref:"):
            explicit_ref = _cand
    elif isinstance(processed_source_data, str) and processed_source_data.startswith("ref:"):
        explicit_ref = processed_source_data
    result_ref = provenance.get("result_ref")
    # A layer dict carrying the previous generation's result_ref would
    # otherwise overwrite the freshly supplied inline/url carrier below with
    # the old ref (map silently renders stale data and the new data is lost).
    stale_result_ref = (
        explicit_ref is None
        and processed_source_data is not None
        and isinstance(result_ref, str)
        and bool(result_ref)
        and result_ref == prior_carrier_ref
        and result_ref == incoming_result_ref
    )
    result_ref_applied = False
    if (
        explicit_ref is None
        and isinstance(result_ref, str) and result_ref
        and not stale_result_ref
    ):
        source_entry["ref"] = result_ref
        source_entry["ref_id"] = result_ref
        source_entry["data_fingerprint"] = (
            "ref-sha256:" + hashlib.sha256(result_ref.encode()).hexdigest()
        )
        result_ref_applied = True

    # P3-1: stamp the ref's content_revision (V5-E) onto ref-carrying source
    # entries so the frontend session-restore/mirror path can build revisioned
    # tile URLs (v=<revision>) even for layers reconstructed purely from a
    # committed MapSpec — same contract as the SSE descriptor path.
    _ref_for_rev = source_entry.get("ref_id") or source_entry.get("ref")
    if (
        isinstance(_ref_for_rev, str)
        and _ref_for_rev.startswith("ref:")
        and ref_content_revisions
        and _ref_for_rev in ref_content_revisions
    ):
        # Sync function (runs in to_thread): the async caller pre-fetches the
        # revisions for the refs it knows about and passes them in.
        source_entry["content_revision"] = int(ref_content_revisions[_ref_for_rev])

    suggested_view: Optional[Dict[str, Any]] = None
    if is_raster_entry(source_entry) or is_data_fabric_entry(source_entry):
        data_to_profile = source_entry.get("inlineData")
    else:
        # Profile only what the ENTRY actually carries (inline/url/path —
        # store_data already normalized the carrier). Never re-profile the
        # raw source_data dict: for ref-carried sources it is a metadata
        # carrier (no features), and profiling it overwrote the
        # descriptor-derived profile with an empty one.
        data_to_profile = profile_data(source_entry)

    if data_to_profile and isinstance(data_to_profile, dict) and (
        processed_source_data is not None or "profile" not in source_entry
    ):
        try:
            profile = profile_geojson_source(data_to_profile)
            source_entry["profile"] = profile
            profile_payload = json.dumps(
                profile,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            source_entry["profile_fingerprint"] = (
                "profile-sha256:" + hashlib.sha256(profile_payload).hexdigest()
            )

            if not view_has_center(mapspec) and profile.get("suggestedView"):
                suggested_view = {
                    "center": profile["suggestedView"]["center"],
                    "zoom": profile["suggestedView"]["zoom"],
                }
        except Exception as e:
            logger.warning(f"Auto-profiling failed for layer {processed_layer.get('id')}: {e}")

    # A session ref is the authoritative carrier. Profiling above may inspect
    # caller-owned inline data once during ingestion, but the persisted MapSpec
    # must not duplicate a 100k-feature payload merely to describe the map.
    # Only drop the new carrier when a ref actually won (explicit source ref or
    # this generation's result_ref) — a stale result_ref must leave it intact.
    if (
        isinstance(result_ref, str)
        and result_ref
        and (explicit_ref is not None or result_ref_applied)
    ):
        source_entry.pop("inlineData", None)
        source_entry.pop("url", None)
        # #1403: dataPath must not survive beside ref — mixed carrier mounts
        # raster tiles at a non-tile URL and breaks MapLibre layer types.
        source_entry.pop("dataPath", None)

    return processed_layer, source_entry, suggested_view
