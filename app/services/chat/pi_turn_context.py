"""Signed, turn-scoped routing context for Pi extension callbacks.

Pi's RPC ``sessionId`` is not exposed to extension tool handlers.  A callback
must therefore carry an independently verifiable capability that was minted
for the user turn; reading a mutable "currently active session" creates a
TOCTOU window where a delayed callback can be attributed to the next turn.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


TURN_CONTEXT_MARKER = "WEBGIS_TURN_CONTEXT"
TURN_CONTEXT_MAX_AGE_SECONDS = 15 * 60


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def issue_turn_token(
    secret: str,
    session_id: str,
    turn_id: str,
    *,
    issued_at: Optional[int] = None,
) -> str:
    """Mint a compact HMAC capability containing only routing identifiers."""
    payload = {
        "sid": session_id,
        "tid": turn_id,
        "iat": int(time.time() if issued_at is None else issued_at),
    }
    encoded = _b64encode(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))
    signature = hmac.new(
        secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{encoded}.{_b64encode(signature)}"


def verify_turn_token(
    secret: str,
    token: str,
    *,
    now: Optional[int] = None,
    max_age_seconds: int = TURN_CONTEXT_MAX_AGE_SECONDS,
) -> Optional[dict[str, Any]]:
    """Return the verified routing payload, or ``None`` for any invalid token."""
    try:
        encoded, supplied_signature = token.split(".", 1)
        expected_signature = _b64encode(hmac.new(
            secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
        ).digest())
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return None
        payload = json.loads(_b64decode(encoded))
        if not isinstance(payload, dict):
            return None
        session_id = payload.get("sid")
        turn_id = payload.get("tid")
        issued_at = payload.get("iat")
        if not isinstance(session_id, str) or not session_id:
            return None
        if not isinstance(turn_id, str) or not turn_id:
            return None
        if isinstance(issued_at, bool) or not isinstance(issued_at, int):
            return None
        current = int(time.time() if now is None else now)
        age = current - issued_at
        if age < -30 or age > max_age_seconds:
            return None
        return {"session_id": session_id, "turn_id": turn_id, "issued_at": issued_at}
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeError):
        return None


def attach_turn_context(
    message: str,
    token: str,
    cartography_block: str = "",
    session_plan_block: str = "",
) -> str:
    """Attach the capability to the turn for the extension's local session view.

    ``cartography_block``（可选）是 harness 制图 verdict 的有界投影。
    ``session_plan_block``（可选）是 SessionPlan 的有界投影，不是 verdict。
    两者都插在用户消息与 turn marker 之间；marker 必须保持最后——扩展的
    ``currentTurnToken`` 取最新 entry 的最后一个匹配。
    """
    parts = [message]
    if cartography_block:
        parts.append(cartography_block)
    if session_plan_block:
        parts.append(session_plan_block)
    parts.append(f"[{TURN_CONTEXT_MARKER}:{token}]")
    parts.append("(Internal routing context; do not quote or modify this marker.)")
    return "\n\n".join(parts)


async def bind_turn_prompt(
    message: str,
    token: str,
    session_id: str,
    cartography_block: str = "",
) -> str:
    """Open the SessionPlan slot and attach verdict + bounded plan + turn marker."""
    plan_block = ""
    if session_id:
        try:
            from app.services.session_plan import (
                ensure_session_plan_slot,
                format_session_plan_projection,
                load_session_plan,
            )
            await ensure_session_plan_slot(session_id)
            plan = await load_session_plan(session_id)
            plan_block = format_session_plan_projection(plan)
        except Exception:
            logger.exception("[PiTurn] SessionPlan projection failed session=%s", session_id)
    return attach_turn_context(message, token, cartography_block, plan_block)
