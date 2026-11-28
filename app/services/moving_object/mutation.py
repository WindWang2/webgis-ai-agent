"""MapSpec mutation for trajectory products (mirrors flow_product/mutation).

The apply intent validates the stored product against the session (family
match, source freshness, ref liveness) and yields one MutationPlan that
atomically replaces the trajectory layer family. No partial application:
any refusal leaves the map untouched.
"""
from __future__ import annotations

import copy

from app.services.mapspec.mutation_contracts import (
    MapSpecResult,
    MutationContext,
    MutationPlan,
)
from app.services.moving_object.service import SCHEMA_VERSION
from app.services.moving_object.presentation import (
    trajectory_layer_ids,
    trajectory_source_ids,
)
from app.services.session_data import session_data_manager


def _refusal(ctx: MutationContext, code: str, message: str, hint: str) -> MapSpecResult:
    return MapSpecResult(
        is_error=True,
        origin=ctx.origin,
        error_code=code,
        error_msg=message,
        correction_hint=hint,
    )


async def apply_trajectory_product(
    ctx: MutationContext,
) -> MutationPlan | MapSpecResult:
    intent = ctx.intent
    product = await session_data_manager.get(ctx.session_id, intent.product_ref)
    if not isinstance(product, dict) or product.get("schema_version") != SCHEMA_VERSION:
        return _refusal(
            ctx, "TRAJECTORY_PRODUCT_NOT_FOUND",
            "Trajectory preview was not found in this session.",
            "Create a fresh preview in the same session, then apply it.",
        )
    family_id = str(product.get("family_id") or "")
    if not family_id or family_id != intent.family_id:
        return _refusal(
            ctx, "TRAJECTORY_PRODUCT_FAMILY_MISMATCH",
            "Trajectory product family does not match the apply request.",
            "Use the family_id returned by the preview; do not substitute "
            "another product.",
        )
    source_ref = str(product.get("source_ref") or "")
    source_descriptor = await session_data_manager.get_ref_descriptor(
        ctx.session_id, source_ref
    )
    if source_descriptor is None:
        return _refusal(
            ctx, "TRAJECTORY_SOURCE_NOT_FOUND",
            "The original dataset is no longer readable.",
            "Reattach the dataset and create a fresh preview.",
        )
    current_revision = int(source_descriptor.get("content_revision") or 0)
    expected_source_revision = int(product.get("source_revision") or 0)
    if current_revision != expected_source_revision:
        return _refusal(
            ctx, "TRAJECTORY_SOURCE_STALE",
            "The dataset changed after this preview was created.",
            "Create a fresh preview against the current source revision; "
            "the old preview cannot overwrite newer settings.",
        )

    fragment = product.get("mapspec")
    if not isinstance(fragment, dict):
        return _refusal(
            ctx, "TRAJECTORY_PRODUCT_INVALID",
            "Trajectory preview has no map fragment.",
            "Create a new preview.",
        )
    raw_layers = fragment.get("layers")
    raw_sources = fragment.get("sources")
    if not isinstance(raw_layers, list) or not isinstance(raw_sources, dict):
        return _refusal(
            ctx, "TRAJECTORY_PRODUCT_INVALID",
            "Trajectory preview has an invalid map fragment.",
            "Create a new preview.",
        )

    layers = copy.deepcopy(raw_layers)
    sources = copy.deepcopy(raw_sources)
    expected_layer_ids = set(trajectory_layer_ids(family_id))
    expected_source_ids = set(trajectory_source_ids(family_id))
    layer_ids = {str(layer.get("id") or "") for layer in layers if isinstance(layer, dict)}
    if f"traj-{family_id}-lines" not in layer_ids or not layer_ids <= expected_layer_ids:
        return _refusal(
            ctx, "TRAJECTORY_PRODUCT_INVALID",
            "Trajectory preview contains unexpected layers.",
            "Create a new preview.",
        )
    # Every layer must reference a declared source (the direction symbol
    # layer legitimately reuses the lines source).
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        if str(layer.get("source") or "") not in set(raw_sources):
            return _refusal(
                ctx, "TRAJECTORY_PRODUCT_INVALID",
                "Trajectory sources and layers are inconsistent.",
                "Create a new preview.",
            )
    if not set(raw_sources) <= expected_source_ids:
        return _refusal(
            ctx, "TRAJECTORY_PRODUCT_INVALID",
            "Trajectory sources and layers are inconsistent.",
            "Create a new preview.",
        )

    primary = next(layer for layer in layers if layer.get("id") == f"traj-{family_id}-lines")
    block = primary.get("trajectory")
    if (
        not isinstance(block, dict)
        or block.get("product_ref") != intent.product_ref
        or block.get("family_id") != family_id
        or block.get("source_ref") != source_ref
        or int(block.get("source_revision") or 0) != expected_source_revision
    ):
        return _refusal(
            ctx, "TRAJECTORY_PRODUCT_INVALID",
            "Trajectory product binding is inconsistent.",
            "Create a new preview.",
        )

    for source_id, source in sources.items():
        if not isinstance(source, dict) or source.get("type") != "geojson":
            return _refusal(
                ctx, "TRAJECTORY_PRODUCT_INVALID",
                f"Trajectory source {source_id} is invalid.",
                "Create a new preview.",
            )
        ref = source.get("ref_id")
        if not isinstance(ref, str) or not ref.startswith("ref:"):
            return _refusal(
                ctx, "TRAJECTORY_PRODUCT_INVALID",
                f"Trajectory source {source_id} has no session ref.",
                "Create a new preview.",
            )
        descriptor = await session_data_manager.get_ref_descriptor(ctx.session_id, ref)
        if descriptor is None:
            return _refusal(
                ctx, "TRAJECTORY_ARTIFACT_NOT_FOUND",
                f"Trajectory artifact {ref} is unavailable.",
                "Create a fresh preview; no partial product was applied.",
            )
        source["content_revision"] = int(descriptor.get("content_revision") or 0)

    loaded = ctx.loaded or {}
    mapspec = {**loaded}
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
            or (layer.get("metadata") or {}).get("trajectory_family_id") == family_id
        )
    ), len(existing_layers))
    retained = [
        layer for layer in existing_layers
        if not isinstance(layer, dict) or (
            layer.get("id") not in expected_layer_ids
            and (layer.get("metadata") or {}).get("trajectory_family_id") != family_id
        )
    ]
    for layer in layers:
        existing = old_by_id.get(str(layer.get("id")))
        if existing is not None:
            _preserve_presentation(existing, layer)

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


def _preserve_presentation(existing: dict, layer: dict) -> None:
    """Keep user-owned presentation keys across re-apply (user-wins)."""
    from app.services.mapspec.mutation_primitives import (
        _preserve_durable_presentation,
    )
    _preserve_durable_presentation(existing, layer)
