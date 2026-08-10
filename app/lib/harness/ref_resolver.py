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
