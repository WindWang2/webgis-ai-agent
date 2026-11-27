from __future__ import annotations

import copy
from typing import List

from app.services.mapspec.mutation_contracts import MapSpecResult, MutationContext, MutationPlan
from app.services.mapspec.mutation_primitives import _preserve_durable_presentation
from app.services.session_data import session_data_manager


def flow_layer_ids(family_id: str) -> List[str]:
    return [f"flow-{family_id}-{part}" for part in ("edges", "direction", "nodes")]


def flow_source_ids(family_id: str) -> List[str]:
    return [f"flow-{family_id}-{part}-source" for part in ("edges", "nodes")]


def _refusal(ctx: MutationContext, code: str, message: str, hint: str) -> MapSpecResult:
    return MapSpecResult(
        is_error=True,
        origin=ctx.origin,
        error_code=code,
        error_msg=message,
        correction_hint=hint,
    )


async def apply_flow_product(ctx: MutationContext) -> MutationPlan | MapSpecResult:
    intent = ctx.intent
    product = await session_data_manager.get(ctx.session_id, intent.product_ref)
    if not isinstance(product, dict) or product.get("schema_version") != "od_flow_product.v1":
        return _refusal(
            ctx, "FLOW_PRODUCT_NOT_FOUND", "OD flow preview was not found in this session.",
            "Create a fresh preview in the same session, then apply it.",
        )
    family_id = str(product.get("family_id") or "")
    if not family_id or family_id != intent.family_id:
        return _refusal(
            ctx, "FLOW_PRODUCT_FAMILY_MISMATCH", "OD flow product family does not match the apply request.",
            "Use the family_id returned by the preview; do not substitute another product.",
        )
    source_ref = str(product.get("source_ref") or "")
    source_descriptor = await session_data_manager.get_ref_descriptor(ctx.session_id, source_ref)
    if source_descriptor is None:
        return _refusal(
            ctx, "FLOW_SOURCE_NOT_FOUND", "The original OD source is no longer readable.",
            "Reattach the OD table and create a fresh preview.",
        )
    current_revision = int(source_descriptor.get("content_revision") or 0)
    expected_source_revision = int(product.get("source_revision") or 0)
    if current_revision != expected_source_revision:
        return _refusal(
            ctx, "FLOW_SOURCE_STALE", "The OD source changed after this preview was created.",
            "Create a fresh preview against the current source revision; the old preview cannot overwrite newer settings.",
        )

    fragment = product.get("mapspec")
    if not isinstance(fragment, dict):
        return _refusal(ctx, "FLOW_PRODUCT_INVALID", "OD flow preview has no map fragment.", "Create a new preview.")
    raw_layers = fragment.get("layers")
    raw_sources = fragment.get("sources")
    if not isinstance(raw_layers, list) or not isinstance(raw_sources, dict):
        return _refusal(ctx, "FLOW_PRODUCT_INVALID", "OD flow preview has an invalid map fragment.", "Create a new preview.")

    layers = copy.deepcopy(raw_layers)
    sources = copy.deepcopy(raw_sources)
    expected_layer_ids = set(flow_layer_ids(family_id))
    expected_source_ids = set(flow_source_ids(family_id))
    layer_ids = {str(layer.get("id") or "") for layer in layers if isinstance(layer, dict)}
    if f"flow-{family_id}-edges" not in layer_ids or not layer_ids <= expected_layer_ids:
        return _refusal(ctx, "FLOW_PRODUCT_INVALID", "OD flow preview contains unexpected layers.", "Create a new preview.")
    if set(raw_sources) != {str(layer.get("source")) for layer in layers}:
        return _refusal(ctx, "FLOW_PRODUCT_SOURCE_MISMATCH", "OD flow sources and layers are inconsistent.", "Create a new preview.")
    if not set(raw_sources) <= expected_source_ids:
        return _refusal(ctx, "FLOW_PRODUCT_INVALID", "OD flow preview contains unexpected sources.", "Create a new preview.")

    primary = next(layer for layer in layers if layer.get("id") == f"flow-{family_id}-edges")
    flow = primary.get("flow")
    if (
        not isinstance(flow, dict)
        or flow.get("product_ref") != intent.product_ref
        or flow.get("family_id") != family_id
        or flow.get("source_ref") != source_ref
        or int(flow.get("source_revision") or 0) != expected_source_revision
    ):
        return _refusal(ctx, "FLOW_PRODUCT_INVALID", "OD flow product binding is inconsistent.", "Create a new preview.")
    direction_required = bool(flow.get("show_arrows")) and flow.get("direction_mode") != "undirected-total"
    if direction_required and f"flow-{family_id}-direction" not in layer_ids:
        return _refusal(
            ctx, "FLOW_DIRECTION_MISSING", "Directed OD flow is missing its direction-mark layer.",
            "Create a complete preview; an incomplete directed product cannot be applied.",
        )

    for source_id, source in sources.items():
        if not isinstance(source, dict) or source.get("type") != "geojson":
            return _refusal(ctx, "FLOW_PRODUCT_INVALID", f"OD flow source {source_id} is invalid.", "Create a new preview.")
        ref = source.get("url") or source.get("ref_id")
        if not isinstance(ref, str) or not ref.startswith("ref:"):
            return _refusal(ctx, "FLOW_PRODUCT_INVALID", f"OD flow source {source_id} has no session ref.", "Create a new preview.")
        descriptor = await session_data_manager.get_ref_descriptor(ctx.session_id, ref)
        if descriptor is None:
            return _refusal(
                ctx, "FLOW_ARTIFACT_NOT_FOUND", f"OD flow artifact {ref} is unavailable.",
                "Create a fresh preview; no partial edge/node/direction product was applied.",
            )
        source["ref_id"] = ref
        source.pop("url", None)
        source["content_revision"] = int(descriptor.get("content_revision") or 0)
        from app.services.spatial_meta_profiler import profile_from_descriptor
        import hashlib
        import json

        profile = profile_from_descriptor(descriptor) or {
            "bbox": descriptor.get("bbox"),
            "crs": None,
            "crs_status": "unknown",
            "featureCount": descriptor.get("feature_count"),
            "geometryTypes": descriptor.get("geometry_types") or [],
            "fields": {},
            "fields_status": "unknown",
            "suggestedView": {},
            "temporalProfile": None,
        }
        source["profile"] = profile
        try:
            profile_json = json.dumps(
                profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode()
        except ValueError:
            return _refusal(
                ctx, "FLOW_PRODUCT_INVALID",
                f"OD flow artifact {ref} profile contains non-finite numbers.",
                "Create a fresh preview; no partial edge/node/direction product was applied.",
            )
        source["profile_fingerprint"] = "profile-sha256:" + hashlib.sha256(profile_json).hexdigest()

    loaded = ctx.loaded or {}
    mapspec = {**loaded}
    mapspec["version"] = "1.10"
    existing_sources = dict(loaded.get("sources") or {})
    existing_layers = list(loaded.get("layers") or [])
    old_by_id = {
        str(layer.get("id") or ""): layer
        for layer in existing_layers
        if isinstance(layer, dict)
    }
    insert_at = next((
        index for index, layer in enumerate(existing_layers)
        if isinstance(layer, dict) and (
            layer.get("id") in expected_layer_ids
            or (layer.get("metadata") or {}).get("flow_family_id") == family_id
        )
    ), len(existing_layers))
    retained = [
        layer for layer in existing_layers
        if not isinstance(layer, dict) or (
            layer.get("id") not in expected_layer_ids
            and (layer.get("metadata") or {}).get("flow_family_id") != family_id
        )
    ]
    for layer in layers:
        existing = old_by_id.get(str(layer.get("id")))
        if existing is not None:
            _preserve_durable_presentation(existing, layer)

    used_sources = {
        str(layer.get("source"))
        for layer in retained + layers
        if isinstance(layer, dict)
    }
    for source_id in expected_source_ids:
        if source_id not in used_sources:
            existing_sources.pop(source_id, None)
    existing_sources.update(sources)
    retained[insert_at:insert_at] = layers
    mapspec["sources"] = existing_sources
    mapspec["layers"] = retained
    return MutationPlan(
        mapspec=mapspec,
        auto_checkpoint=True,
        pending_layer_op=("replace", "", list(retained)),
    )
