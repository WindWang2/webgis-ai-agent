"""G14 编辑数据集 provisioning —— 空图层创建 / 草图升格 / 只读派生副本。

纪律：
- 复用既有摄入管线（ingest → session ref + descriptor + artifact ledger
  注册 → Pi/数据工具可见），不建第二套数据集登记；
- envelope 兄弟键承载编辑元数据：``geometry_type``（typed 空图层的期望
  基类型）、``topology``（声明制规则）、``read_only``（只读标记）、
  ``derived_from``（派生副本来源）—— 全部随特征编辑 overwrite 保留；
- 原始上传文件/只读源绝不回写：只读源编辑 = 派生新 ref + 明确来源。
"""
from __future__ import annotations

from app.contracts.geojson import GeoJSONFeatureCollection

import re
from typing import Any, Dict, List, Optional

from app.services.feature_edit.contracts import (
    MAX_EDITABLE_FEATURES,
    TOPOLOGY_RULES,
    WFID_PROP,
    finding,
)
from app.services.feature_edit.topology import extract_topology_declaration

__all__ = [
    "qualify_dataset",
    "create_empty_dataset",
    "promote_sketch",
    "create_editable_copy",
    "extract_geometry_type_declaration",
]

_GEOMETRY_BASE_TYPES = frozenset({"Point", "LineString", "Polygon"})
_MAX_PROMOTE_FEATURES = 5_000       # 草图升格单次要素上限（草图是人画的有界物）
_MAX_PROMOTE_BYTES = 8 * 1024 * 1024


def _slug(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "-", str(name)).strip("-")[:32] or "layer"


def _layer_type_for_geometry(geometry_type: str) -> str:
    if geometry_type == "Point":
        return "circle"
    if geometry_type == "LineString":
        return "line"
    return "fill"


async def _mount_layer(session_id: str, ref_id: str, name: str, geometry_type: str) -> Optional[str]:
    """把新数据集挂到 MapSpec（SSE 镜像自动同步 HUD 行；失败不阻断 provisioning）。"""
    try:
        from app.services.mapspec_store import mapspec_store

        layer_id = f"fed-{_slug(name)}-{ref_id.rsplit('-', 1)[-1][:8]}"
        res = await mapspec_store.layer_upsert(
            session_id,
            layer={"id": layer_id, "type": _layer_type_for_geometry(geometry_type), "source": f"src-{layer_id}"},
            source_data={
                "type": "geojson",
                "ref_id": ref_id,
                "profile": {},
                "profile_fingerprint": "p0",
                "data_fingerprint": "d0",
            },
            origin="agent", actor="feature_edit_datasets",
        )
        if res.get("success"):
            return layer_id
        return None
    except Exception:  # noqa: BLE001 — 挂层是附加面，失败由调用方按 None 披露
        return None


def _normalize_topology(topology: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not topology:
        return None
    rules = [str(r) for r in (topology.get("rules") or []) if r in TOPOLOGY_RULES]
    if not rules:
        return None
    out: Dict[str, Any] = {"rules": list(dict.fromkeys(rules))}
    tol = topology.get("tolerance_m")
    if isinstance(tol, (int, float)) and not isinstance(tol, bool) and tol > 0:
        out["tolerance_m"] = float(tol)
    return out


def extract_geometry_type_declaration(payload: Any) -> Optional[str]:
    """envelope 的期望几何基类型（typed 空图层）；缺省 None = 不约束。"""
    if not isinstance(payload, dict):
        return None
    gt = payload.get("geometry_type")
    if isinstance(gt, str) and gt in _GEOMETRY_BASE_TYPES:
        return gt
    return None


def _is_read_only(payload: Any, artifact_metadata: Dict[str, Any]) -> bool:
    if isinstance(payload, dict) and payload.get("read_only") is True:
        return True
    return artifact_metadata.get("read_only") is True


async def _artifact_metadata(session_id: str, ref_id: str) -> Dict[str, Any]:
    try:
        from app.services.artifact_registry import get_artifact

        rec = await get_artifact(session_id, ref_id)
        return rec.get("metadata") if isinstance(rec, dict) else {}
    except Exception:  # noqa: BLE001 — ledger 读失败按无元数据处理
        return {}


async def qualify_dataset(session_id: str, ref_id: str) -> Dict[str, Any]:
    """编辑资格判定（UI「开始编辑」按钮的真实依据）。"""
    from app.services.session_data import session_data_manager

    payload = await session_data_manager.get(session_id, ref_id)
    if payload is None:
        return {"ref_id": ref_id, "editable": False, "reason": "dataset_not_found"}
    fc = _extract_fc(payload)
    if fc is None:
        return {"ref_id": ref_id, "editable": False, "reason": "dataset_not_editable"}
    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id) or {}
    features = fc.get("features") or []
    metadata = await _artifact_metadata(session_id, ref_id)
    read_only = _is_read_only(payload, metadata)
    n = len(features)
    if n > MAX_EDITABLE_FEATURES:
        return {
            "ref_id": ref_id, "editable": False, "reason": "dataset_too_large_to_edit",
            "feature_count": n, "read_only": read_only,
        }
    geometry_types = sorted({(f.get("geometry") or {}).get("type") for f in features if isinstance(f, dict)} - {None})
    return {
        "ref_id": ref_id,
        "editable": not read_only,
        "reason": "dataset_read_only_source" if read_only else None,
        "read_only": read_only,
        "feature_count": n,
        "geometry_types": geometry_types,
        "declared_geometry_type": extract_geometry_type_declaration(payload),
        "content_revision": int(descriptor.get("content_revision") or 1),
        "topology": extract_topology_declaration(payload),
        "has_wfids": bool(features) and all(
            isinstance(f.get("properties"), dict) and WFID_PROP in f["properties"]
            for f in features if isinstance(f, dict)
        ),
    }


def _extract_fc(data: Any) -> Optional[GeoJSONFeatureCollection]:
    fc = data
    if isinstance(data, dict):
        nested = data.get("geojson")
        if isinstance(nested, dict):
            fc = nested
    if isinstance(fc, dict) and fc.get("type") == "FeatureCollection":
        return fc  # type: ignore[return-value]
    return None


async def _ingest_dataset(
    session_id: str,
    data: Dict[str, Any],
    *,
    name: str,
    logical_role: str,
) -> Dict[str, Any]:
    from app.services.data_ingest.pipeline import get_ingest_pipeline

    result = await get_ingest_pipeline().ingest(
        session_id, data, name=name, source_type="inline",
        logical_role=logical_role, prefix="dataset", dedup=False,
    )
    return {
        "ok": bool(result.ok),
        "ref_id": result.ref_id,
        "error": result.error,
        "error_code": result.error_code,
    }


async def create_empty_dataset(
    session_id: str,
    *,
    name: str,
    geometry_type: str,
    topology: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """V01：创建空 typed 矢量数据集（真实 ref + descriptor + ledger + 挂层）。"""
    gt = str(geometry_type or "").strip()
    if gt not in _GEOMETRY_BASE_TYPES:
        return {"ok": False, "error_code": "INVALID_GEOMETRY_TYPE",
                "error": f"geometry_type must be one of {sorted(_GEOMETRY_BASE_TYPES)}"}
    if not name or not str(name).strip():
        return {"ok": False, "error_code": "INVALID_NAME", "error": "name is required"}
    data: Dict[str, Any] = {
        "geojson": {"type": "FeatureCollection", "features": []},
        "geometry_type": gt,
    }
    decl = _normalize_topology(topology)
    if decl:
        data["topology"] = decl
    ingested = await _ingest_dataset(session_id, data, name=str(name).strip(), logical_role="edit_source")
    if not ingested["ok"]:
        return ingested
    ref_id = ingested["ref_id"]
    layer_id = await _mount_layer(session_id, ref_id, str(name), gt)
    from app.services.session_data import session_data_manager

    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id) or {}
    return {
        "ok": True,
        "ref_id": ref_id,
        "layer_id": layer_id,
        "name": str(name).strip(),
        "geometry_type": gt,
        "topology": decl,
        "feature_count": 0,
        "content_revision": int(descriptor.get("content_revision") or 1),
    }


def _validate_promote_features(features: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(features, list) or not features:
        return {"ok": False, "error_code": "INVALID_FEATURES", "error": "features must be a non-empty list"}
    if len(features) > _MAX_PROMOTE_FEATURES:
        return {"ok": False, "error_code": "TOO_MANY_FEATURES",
                "error": f"sketch promotion is bounded to {_MAX_PROMOTE_FEATURES} features"}
    import json as _json

    try:
        blob = _json.dumps(features, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"ok": False, "error_code": "INVALID_FEATURES", "error": "features are not JSON-serializable"}
    if len(blob.encode("utf-8")) > _MAX_PROMOTE_BYTES:
        return {"ok": False, "error_code": "PAYLOAD_TOO_LARGE",
                "error": "sketch payload exceeds the promotion byte budget"}
    geometry_types = set()
    for position, feature in enumerate(features):
        if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
            return {"ok": False, "error_code": "INVALID_FEATURES",
                    "error": "every feature requires a geometry object"}
        geometry_types.add(str(feature["geometry"].get("type") or ""))
        props = feature.get("properties")
        if props is not None and isinstance(props, dict):
            # R3 P2-8：升格属性限标量（与编辑词表同面；非标量入库后不可编辑）
            bad_fields = [
                k for k, v in props.items()
                if isinstance(v, (list, dict))
            ]
            if bad_fields:
                return {"ok": False, "error_code": "INVALID_FEATURES",
                        "error": f"feature[{position}] properties must be scalar; got containers in: {bad_fields[:4]}"}
    # 升格保持单一基类型（多类型草图应先拆分；诚实拒绝而非静默混装）
    bases = {g[5:] if g.startswith("Multi") else g for g in geometry_types}
    if len(bases) != 1 or not bases.intersection(_GEOMETRY_BASE_TYPES):
        return {"ok": False, "error_code": "MIXED_GEOMETRY_TYPES",
                "error": "sketch features must share one geometry base type (Point/LineString/Polygon)"}
    return None


async def promote_sketch(
    session_id: str,
    *,
    features: List[Dict[str, Any]],
    name: str,
    topology: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """V02：临时草图 → 持久项目数据集（有来源、可刷新存活、Pi 可分析）。"""
    if not name or not str(name).strip():
        return {"ok": False, "error_code": "INVALID_NAME", "error": "name is required"}
    bad = _validate_promote_features(features)
    if bad:
        return bad
    clean_features = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = dict(feature.get("properties") or {})
        props.pop("_wfid", None)  # 草图临时身份不入库（首编辑时物化）
        clean_features.append({
            "type": "Feature",
            "geometry": feature["geometry"],
            "properties": props,
        })
    first_geometry = clean_features[0]["geometry"]["type"]
    base = first_geometry[5:] if first_geometry.startswith("Multi") else first_geometry
    # 升格即物化稳定身份：草图要素无既有 id 键，不物化则首个编辑无法寻址。
    # 物化方案与首编辑路径同源（fingerprint:index 决定性）—— 之后的首个
    # plan 看到 has_wfids=True，不再重复物化，链指纹自始在物化后空间。
    from app.services.data_ingest.pipeline import compute_payload_fingerprint
    from app.services.feature_edit.identity import materialize_wfids

    data: Dict[str, Any] = {
        "geojson": {"type": "FeatureCollection", "features": clean_features},
        "geometry_type": base,
        "origin_kind": "sketch",
    }
    fingerprint = compute_payload_fingerprint(data)
    materialized_fc, _assigned = materialize_wfids(
        data["geojson"], fingerprint
    )
    data["geojson"] = materialized_fc
    decl = _normalize_topology(topology)
    if decl:
        data["topology"] = decl
    ingested = await _ingest_dataset(session_id, data, name=str(name).strip(), logical_role="sketch_promotion")
    if not ingested["ok"]:
        return ingested
    ref_id = ingested["ref_id"]
    layer_id = await _mount_layer(session_id, ref_id, str(name), base)
    from app.services.session_data import session_data_manager

    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id) or {}
    return {
        "ok": True,
        "ref_id": ref_id,
        "layer_id": layer_id,
        "name": str(name).strip(),
        "geometry_type": base,
        "topology": decl,
        "feature_count": len(clean_features),
        "content_revision": int(descriptor.get("content_revision") or 1),
        "origin_kind": "sketch",
    }


async def create_editable_copy(
    session_id: str,
    ref_id: str,
    *,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """V20：只读源 → 派生编辑副本（原源零写入；副本带 derived_from 来源）。"""
    from app.services.session_data import session_data_manager

    payload = await session_data_manager.get(session_id, ref_id)
    if payload is None:
        return {"ok": False, "error_code": "DATASET_NOT_FOUND", "error": "source ref not resolvable"}
    fc = _extract_fc(payload)
    if fc is None:
        return {"ok": False, "error_code": "DATASET_NOT_EDITABLE", "error": "source is not a FeatureCollection"}
    import copy as _copy

    data = _copy.deepcopy(payload) if isinstance(payload, dict) else {"geojson": fc}
    if not isinstance(data, dict):
        return {"ok": False, "error_code": "DATASET_NOT_EDITABLE", "error": "unsupported payload shape"}
    original_read_only = data.get("read_only")
    data.pop("read_only", None)  # 副本可编辑
    data["derived_from"] = ref_id
    # 只读标记不是内容：原样复制其它 envelope 键（geometry_type/topology 保留）
    copy_name = str(name or f"编辑副本 {ref_id.rsplit('-', 1)[-1][:8]}").strip()
    metadata = await _artifact_metadata(session_id, ref_id)
    ingested = await _ingest_dataset(
        session_id, data, name=copy_name, logical_role="derived_edit_copy"
    )
    if not ingested["ok"]:
        return ingested
    new_ref = ingested["ref_id"]
    base = extract_geometry_type_declaration(data)
    if base is None:
        features = fc.get("features") or []
        types = {(f.get("geometry") or {}).get("type") for f in features if isinstance(f, dict)}
        types.discard(None)
        first = sorted(types)[0] if types else "Polygon"
        base = first[5:] if first.startswith("Multi") else first
    layer_id = await _mount_layer(session_id, new_ref, copy_name, base)
    descriptor = await session_data_manager.get_ref_descriptor(session_id, new_ref) or {}
    return {
        "ok": True,
        "ref_id": new_ref,
        "layer_id": layer_id,
        "derived_from": ref_id,
        "source_was_read_only": bool(original_read_only) or metadata.get("read_only") is True,
        "name": copy_name,
        "feature_count": len(fc.get("features") or []),
        "content_revision": int(descriptor.get("content_revision") or 1),
    }
