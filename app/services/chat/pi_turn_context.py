"""Signed, turn-scoped routing context for Pi extension callbacks.

Pi's RPC ``sessionId`` is not exposed to extension tool handlers.  A callback
must therefore carry an independently verifiable capability that was minted
for the user turn; reading a mutable "currently active session" creates a
TOCTOU window where a delayed callback can be attributed to the next turn.
"""

from __future__ import annotations

import asyncio
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
    env_block: str = "",
    surface_block: str = "",
) -> str:
    """Attach the capability to the turn for the extension's local session view.

    ``cartography_block``（可选）是 harness 制图 verdict 的有界投影。
    ``session_plan_block``（可选）是 SessionPlan 的有界投影，不是 verdict。
    ``env_block``/``surface_block``（可选，Pi 兼容补齐）：环境感知有界块
    （用户选中/聚焦/位置/视口——legacy 引擎经 build_map_state_summary 注入，
    Pi 路径此前整块丢失）与工具面偏好行（compile_tool_surface 纯派生）。
    全部插在用户消息与 turn marker 之间；marker 必须保持最后——扩展的
    ``currentTurnToken`` 取最新 entry 的最后一个匹配。
    """
    parts = [message]
    if cartography_block:
        parts.append(cartography_block)
    if session_plan_block:
        parts.append(session_plan_block)
    if env_block:
        parts.append(env_block)
    if surface_block:
        parts.append(surface_block)
    parts.append(f"[{TURN_CONTEXT_MARKER}:{token}]")
    parts.append("(Internal routing context; do not quote or modify this marker.)")
    return "\n\n".join(parts)


async def bind_turn_prompt(
    message: str,
    token: str,
    session_id: str,
    cartography_block: str = "",
    env_block: str = "",
) -> str:
    """Open the SessionPlan slot and attach verdict + bounded plan + turn marker.

    Pi 兼容（V4 工具面）：从同一份 SessionPlan 信封纯派生一条有界的工具面
    偏好行（阶段 + preferred 前门）注入 turn prompt —— Pi 无 per-round schema
    选择（frozen native surface + webgis_execute 代理），偏好只能走 prompt
    引导；这是 compile_tool_surface 的投影消费，不是第二计划真相。
    """
    plan_block = ""
    surface_block = ""
    if session_id:
        try:
            from app.services.session_plan import (
                ensure_session_plan_slot,
                format_session_plan_projection,
                load_session_plan,
            )
            await ensure_session_plan_slot(session_id)
            plan = await load_session_plan(session_id)
            # ADR-0085：产品 facets 投影需要 MapSpec 在场/启用事实（缺省
            # 时 layer facet 状态退化为 pending —— 只影响披露行，不影响真相）。
            spec = None
            try:
                from app.services.mapspec_store import mapspec_store

                spec = await mapspec_store.get_mapspec(session_id)
            except Exception:  # noqa: BLE001 — spec 拉取失败按缺席投影
                spec = None
            plan_block = format_session_plan_projection(plan, spec)
            surface_block = _surface_block_for(plan)
        except Exception:
            logger.exception("[PiTurn] SessionPlan projection failed session=%s", session_id)
    return attach_turn_context(
        message, token, cartography_block, plan_block,
        env_block=env_block, surface_block=surface_block,
    )


def _surface_block_for(plan: Any) -> str:
    """SessionPlan 信封 → 有界工具面提示行（纯派生；失败 → 空串不注入）。"""
    try:
        from app.services.gis_harness.tool_surface import compile_tool_surface

        chapter = getattr(plan, "gis_chapter", None)
        product_status = None
        if isinstance(chapter, dict):
            mp = chapter.get("map_product")
            if isinstance(mp, dict):
                product_status = mp.get("status")
        surface = compile_tool_surface(chapter=chapter, product_status=product_status)
        if not surface.preferred_tools:
            return ""
        names = "、".join(sorted(surface.preferred_tools))
        return (
            f"[工具面提示] 当前产品阶段={surface.phase}；本轮优先：{names}"
            "（原生工具直调，其余经 webgis_execute 执行；提示是偏好不是限制）。"
        )
    except Exception:  # noqa: BLE001 — 提示是增值上下文，绝不阻断 turn
        return ""


class PiTurnRegistry:
    """Encapsulated coordinator for active Pi turn registration (local + Redis)."""

    def __init__(self) -> None:
        self._local_context: Optional[tuple[str, str]] = None
        self._client = None
        self._last_check_s = 0.0

    def _get_redis_client(self):
        import os
        now = time.monotonic()
        if self._client is not None or (now - self._last_check_s) < 60.0:
            return self._client
        self._last_check_s = now
        from app.core.config import settings as _settings
        redis_url = os.getenv("REDIS_URL") or _settings.REDIS_URL or None
        use_redis = os.getenv("USE_REDIS", "").lower() in ("true", "1", "yes") or bool(_settings.USE_REDIS)
        if not redis_url or not use_redis:
            return None
        try:
            import redis.asyncio as aioredis
            self._client = aioredis.from_url(
                redis_url,
                decode_responses=False,
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
            )
        except Exception as e:
            logger.warning("PiTurnRegistry: Redis unavailable: %s", e)
            self._client = None
        return self._client

    async def register_turn(self, session_id: str, turn_id: str) -> None:
        self._local_context = (session_id, turn_id)
        client = self._get_redis_client()
        if client is not None:
            try:
                # #1108: Redis I/O is best-effort; CancelledError is BaseException
                # and was previously uncaught (``except Exception`` only), so a
                # cancel during register could escape past the caller's lock
                # finally. Local ownership is already published above.
                await asyncio.wait_for(
                    asyncio.shield(
                        client.set(
                            f"webgis:pi:active_turn:{session_id}",
                            turn_id,
                            ex=TURN_CONTEXT_MAX_AGE_SECONDS,
                        )
                    ),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "PiTurnRegistry: Redis register timed out for %s", session_id
                )
            except asyncio.CancelledError:
                # Local ownership already published; re-raise so the caller's
                # try/finally can still release the turn lock (#1108).
                logger.warning(
                    "PiTurnRegistry: Redis register cancelled for %s", session_id
                )
                raise
            except Exception as e:
                logger.warning("PiTurnRegistry: Redis register failed for %s: %s", session_id, e)

    async def unregister_turn(self, session_id: str, turn_id: str) -> None:
        if self._local_context == (session_id, turn_id):
            self._local_context = None
        client = self._get_redis_client()
        if client is not None:
            try:
                script = (
                    b"if redis.call('get', KEYS[1]) == ARGV[1] then "
                    b"return redis.call('del', KEYS[1]) else return 0 end"
                )
                # #1108: same CancelledError discipline as register_turn — local
                # ownership is already cleared; Redis delete must not re-raise
                # CancelledError into a caller's finally ahead of lock.release().
                await asyncio.wait_for(
                    asyncio.shield(
                        client.eval(
                            script, 1, f"webgis:pi:active_turn:{session_id}", turn_id
                        )
                    ),
                    timeout=2.0,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.warning(
                    "PiTurnRegistry: Redis unregister interrupted for %s", session_id
                )
            except Exception as e:
                logger.warning("PiTurnRegistry: Redis unregister failed for %s: %s", session_id, e)

    async def is_active(self, session_id: str, turn_id: str) -> bool:
        if self._local_context == (session_id, turn_id):
            return True
        client = self._get_redis_client()
        if client is not None:
            try:
                val = await client.get(f"webgis:pi:active_turn:{session_id}")
                if val is not None:
                    stored = val.decode("utf-8") if isinstance(val, bytes) else str(val)
                    return stored == turn_id
            except Exception as e:
                logger.warning("PiTurnRegistry: Redis check failed for %s: %s", session_id, e)
        return False


pi_turn_registry = PiTurnRegistry()
