"""Real ref-cursor resolver backed by the SessionStore.

V2 闭环（HARNESS-V2）：把 ref cursor 的"解析"从字符串前缀检查升级为真实
SessionStore 验证：存在 + 归属正确 session + payload 类型匹配。跨 session /
不存在的 ref 返回 NOT_FOUND / WRONG_SESSION，绝不计为 resolved。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.lib.harness.evidence import RefResolution, RefResolutionStatus

logger = logging.getLogger(__name__)

_REF_RE = re.compile(r"^ref:([a-z][a-z0-9]*)-([a-zA-Z0-9_-]+)$")

# Map ref type prefix to the expected top-level payload shape keyword. A resolved
# payload whose shape contradicts the prefix is a TYPE_MISMATCH (not resolved).
# Only the typed prefixes are type-checked; "data" / "redis-unavailable" etc.
# resolve on existence alone (no type contract).
_TYPED_PREFIXES = {
    "geojson": ("type", "FeatureCollection"),
    "raster": ("raster", True),
    "table": ("columns", None),
}


def make_session_store_resolver(session_store: Any):
    """Build an async ref resolver ``(session_id, ref) -> RefResolution``.

    ``session_store`` is any object implementing the SessionStore protocol
    (``async get(session_id, ref)``). Production wires ``session_data_manager``;
    tests wire a fakeredis-backed manager or a fake.
    """

    async def resolve(session_id: str, ref: str) -> RefResolution:
        m = _REF_RE.match(ref or "")
        if not m:
            return RefResolution(
                ref=ref or "",
                session_id=session_id,
                status=RefResolutionStatus.MALFORMED,
                detail="does not match ref:<type>:<id>",
            )
        expected_type, _seg = m.group(1), m.group(2)
        resolution = RefResolution(
            ref=ref,
            session_id=session_id,
            status=RefResolutionStatus.SYNTACTICALLY_VALID,
            expected_type=expected_type,
        )

        # #794: 廉价路径 —— 存在性（EXISTS）+ descriptor 类型判定即可满足
        # 解析契约，无需全量 payload get + json.loads（评估在 session 锁内
        # 逐 cursor 串行执行，25 refs×200KiB 实测 168ms/次评估）。descriptor
        # 无法判定类型（table 前缀、descriptor 缺失）或 store 不提供廉价 API
        # 时回退既有全量路径 —— 语义不变。
        ref_exists = getattr(session_store, "ref_exists", None)
        get_descriptor = getattr(session_store, "get_ref_descriptor", None)
        if ref_exists is not None and get_descriptor is not None:
            try:
                if not await ref_exists(session_id, ref):
                    resolution.status = RefResolutionStatus.NOT_FOUND
                    resolution.detail = "ref not present in session store"
                    return resolution
                descriptor = await get_descriptor(session_id, ref)
                inferred = _infer_type_from_descriptor(descriptor)
                if inferred is not None:
                    if expected_type in _TYPED_PREFIXES and inferred != expected_type:
                        resolution.status = RefResolutionStatus.TYPE_MISMATCH
                        resolution.detail = f"expected {expected_type}, got {inferred}"
                        return resolution
                    resolution.actual_type = inferred
                    resolution.status = RefResolutionStatus.RESOLVED
                    return resolution
                # descriptor 不存在或无法判定 → 全量路径兜底
            except Exception as e:  # noqa: BLE001 — 廉价面故障不静默成功 → 回退
                logger.debug("cheap ref resolution failed for %s: %s", ref, e)

        try:
            payload = await session_store.get(session_id, ref)
        except Exception as e:  # store error → observable, not silent success
            resolution.status = RefResolutionStatus.NOT_FOUND
            resolution.detail = f"store error: {e}"
            return resolution

        if payload is None:
            resolution.status = RefResolutionStatus.NOT_FOUND
            resolution.detail = "ref not present in session store"
            return resolution

        # The store is session-scoped (get takes session_id), so a hit here
        # already proves session ownership. A ref owned by another session
        # would have returned None above.
        actual_type = _infer_payload_type(payload)
        resolution.actual_type = actual_type
        # Only typed prefixes (geojson/raster/table) carry a type contract.
        if expected_type in _TYPED_PREFIXES and actual_type is not None and actual_type != expected_type:
            resolution.status = RefResolutionStatus.TYPE_MISMATCH
            resolution.detail = f"expected {expected_type}, got {actual_type}"
            return resolution

        resolution.status = RefResolutionStatus.RESOLVED
        return resolution

    return resolve


def _infer_type_from_descriptor(descriptor: Any) -> Optional[str]:
    """#794: 从 O(1) ref descriptor 判定 payload 类型；不可判定返回 None。

    只有 descriptor 能**确定性**证明类型时才返回 —— raster_capable 即 raster；
    有要素几何即 geojson。table / 空 FC / 缺 descriptor 都交给全量路径判定，
    避免把不确定当成 TYPE_MISMATCH。
    """
    if not isinstance(descriptor, dict):
        return None
    if descriptor.get("raster_capable"):
        return "raster"
    if descriptor.get("mvt_capable") or descriptor.get("geometry_types") or (
        isinstance(descriptor.get("feature_count"), (int, float))
        and descriptor.get("feature_count") > 0
    ):
        return "geojson"
    return None


def _infer_payload_type(payload: Any) -> Optional[str]:
    """Best-effort payload type inference for type-match checking."""
    if isinstance(payload, dict):
        if payload.get("type") == "FeatureCollection":
            return "geojson"
        if "raster" in payload or payload.get("raster") is True or "band_data" in payload:
            return "raster"
        if "columns" in payload:
            return "table"
    return None
