"""Selection scoping shared by spatial analysis tools."""

from typing import Any, List, Optional


def _filter_features_by_ids(source: Any, feature_ids: List[str]) -> Any:
    """Keep only features whose resolved id is in ``feature_ids``.

    Returns an error dict when the caller named ids and none survive. Does
    not fall back to the full collection.
    """
    from app.services.feature_edit.identity import resolve_feature_id

    wanted = {str(item) for item in feature_ids if item is not None and str(item)}
    if not wanted:
        return source
    if not isinstance(source, dict):
        return {
            "success": False,
            "code": "selection_unusable",
            "error": "无法按选择过滤源点：输入不是 FeatureCollection",
        }
    features = source.get("features")
    if not isinstance(features, list):
        return {
            "success": False,
            "code": "selection_unusable",
            "error": "无法按选择过滤源点：缺少 features",
        }
    kept = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        fid = resolve_feature_id(feature)
        if fid and fid in wanted:
            kept.append(feature)
    if not kept:
        return {
            "success": False,
            "code": "selection_miss",
            "error": "选择中的要素不在源点集合内，未对整层做距离分析",
        }
    filtered = dict(source)
    filtered["features"] = kept
    filtered["count"] = len(kept)
    return filtered


async def _scope_source_points(
    source: Any,
    feature_ids: Optional[List[str]],
    session_id: Optional[str],
) -> Any:
    """Apply an explicit id list, or the session's validated selection.

    No selection and no ids: return the source unchanged. A live selection
    that fails validation is an error, not a silent full-layer analysis.
    """
    ids = [str(item) for item in (feature_ids or []) if item]
    if not ids and session_id:
        from app.services.gis_situation.analysis_scope import (
            derive_claimed_scope,
            validate_analysis_scope,
        )
        from app.services.gis_harness.interaction_ir.projection import (
            interaction_snapshot_via_store,
        )
        from app.services.session_data import session_data_manager

        try:
            snapshot = await interaction_snapshot_via_store(
                session_data_manager, session_id,
            )
        except Exception:  # noqa: BLE001 — 读失败按无选择，不扩大分析面
            snapshot = {}
        pre_turn = None
        try:
            map_state = await session_data_manager.get_map_state(session_id)
            observation = map_state.get("_cartographic_context_observation")
            if isinstance(observation, dict):
                selected = observation.get("selected_feature")
                pre_turn = selected if isinstance(selected, dict) else None
                selection = observation.get("selection")
                if pre_turn is None and isinstance(selection, dict):
                    pre_turn = selection
                elif isinstance(selection, dict) and not pre_turn.get("selected_ids"):
                    merged = dict(pre_turn)
                    merged["selected_ids"] = selection.get("selected_ids")
                    pre_turn = merged
        except Exception:  # noqa: BLE001
            pre_turn = None
        claimed = derive_claimed_scope(snapshot, pre_turn)
        claimed_ids = claimed.get("feature_ids") or []
        if claimed.get("kind") != "none" and claimed_ids:
            validated = await validate_analysis_scope(session_id, claimed)
            if validated.get("status") != "usable":
                return {
                    "success": False,
                    "code": "selection_unusable",
                    "error": "当前选择未通过归属校验，未对整层做距离分析",
                    "reason": validated.get("reason"),
                }
            ids = [str(item) for item in (validated.get("feature_ids") or claimed_ids)]
    if not ids:
        return source
    return _filter_features_by_ids(source, ids)
