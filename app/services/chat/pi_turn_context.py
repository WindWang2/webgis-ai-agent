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

#: per-turn 动态工具面激活名单 marker（ADR-0103；扩展在 before_agent_start
#: 扫描并 pi.setActiveTools —— 名单是投影决策，不是第二注册中心）。
ACTIVE_TOOLS_MARKER = "WEBGIS_ACTIVE_TOOLS"


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


def _neutralize_active_tools_markers(message: str) -> str:
    """中和用户/数据自带的同形激活 marker（review M1）。

    ACTIVE_TOOLS 是 Python→extension 的**带外控制面**：若不消毒，用户消息或
    其引用的数据里出现 ``[WEBGIS_ACTIVE_TOOLS:[...]]`` 就能绕过
    PI_DYNAMIC_TOOL_SURFACE kill-switch 与 k_max 投影约束。只消毒用户原文，
    不触碰 Python 自己拼接的块（它们在同函数后续 append）。
    """
    if not message:
        return message
    return message.replace(f"[{ACTIVE_TOOLS_MARKER}:", f"[{ACTIVE_TOOLS_MARKER}_NEUTRALIZED:")


def attach_turn_context(
    message: str,
    token: str,
    cartography_block: str = "",
    session_plan_block: str = "",
    env_block: str = "",
    surface_block: str = "",
    active_tools_block: str = "",
    evicted_refs_block: str = "",
) -> str:
    """Attach the capability to the turn for the extension's local session view.

    ``cartography_block``（可选）是 harness 制图 verdict 的有界投影。
    ``session_plan_block``（可选）是 SessionPlan 的有界投影，不是 verdict。
    ``env_block``/``surface_block``（可选，Pi 兼容补齐）：环境感知有界块
    （用户选中/聚焦/位置/视口——legacy 引擎经 build_map_state_summary 注入，
    Pi 路径此前整块丢失）与工具面偏好行（compile_tool_surface 纯派生）。
    ``active_tools_block``（可选，ADR-0103）：动态激活名单 marker，扩展据此
    per-turn setActiveTools。
    ``evicted_refs_block``（可选，ADR-0104 #6）：用户消息引用的 ref 已被逐出时
    的有界诚实 tombstone（可重载 vs 已失效）——与 legacy 组装路径同一策略。
    全部插在用户消息与 turn marker 之间；marker 必须保持最后——扩展的
    ``currentTurnToken`` 取最新 entry 的最后一个匹配。
    """
    parts = [_neutralize_active_tools_markers(message)]
    if cartography_block:
        parts.append(cartography_block)
    if session_plan_block:
        parts.append(session_plan_block)
    if env_block:
        parts.append(env_block)
    if surface_block:
        parts.append(surface_block)
    if active_tools_block:
        parts.append(active_tools_block)
    if evicted_refs_block:
        parts.append(evicted_refs_block)
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
    偏好行（阶段 + preferred 前门）注入 turn prompt。
    ADR-0103（动态面 Phase 3）：同一份 plan 的 capability 进度 + 用户消息
    → DynamicToolSurface 激活名单 marker，扩展在 before_agent_start
    setActiveTools —— 模型可见工具从冻结 7 + proxy 升级为 10-30 个相关工具；
    执行仍全部回到 ToolRegistry（单一执行真相）。
    """
    plan_block = ""
    surface_block = ""
    active_tools_block = ""
    evicted_refs_block = ""
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
            surface = _compile_surface(plan)
            if surface is not None:
                surface_block = _render_surface_block(surface)
                active_tools_block = _active_tools_block_for(message, surface, plan)
        except Exception:
            logger.exception("[PiTurn] SessionPlan projection failed session=%s", session_id)
        # ADR-0104 #6：逐出 ref 的诚实 tombstone（有界、廉价、绝不阻断）。
        # 复用 legacy 组装路径的同一构建器（单一策略实现）。
        try:
            from app.services.chat.context_policy import (
                build_evicted_refs_tombstone,
                policy_enabled,
            )
            from app.services.session_data import session_data_manager

            if policy_enabled():
                evicted_refs_block = await build_evicted_refs_tombstone(
                    session_id,
                    [{"role": "user", "content": message}],
                    session_data_manager,
                )
        except Exception:  # noqa: BLE001 — tombstone 是增值披露，绝不阻断 turn
            evicted_refs_block = ""
    return attach_turn_context(
        message, token, cartography_block, plan_block,
        env_block=env_block, surface_block=surface_block,
        active_tools_block=active_tools_block,
        evicted_refs_block=evicted_refs_block,
    )


def _compile_surface(plan: Any):
    """SessionPlan → gis_harness ToolSurface（失败返回 None，不阻断）。"""
    try:
        from app.services.gis_harness.tool_surface import compile_tool_surface

        chapter = getattr(plan, "gis_chapter", None)
        product_status = None
        if isinstance(chapter, dict):
            mp = chapter.get("map_product")
            if isinstance(mp, dict):
                product_status = mp.get("status")
        return compile_tool_surface(chapter=chapter, product_status=product_status)
    except Exception:  # noqa: BLE001 — 提示是增值上下文，绝不阻断 turn
        return None


def _render_surface_block(surface: Any) -> str:
    try:
        if not surface.preferred_tools:
            return ""
        names = "、".join(sorted(surface.preferred_tools))
        return (
            f"[工具面提示] 当前产品阶段={surface.phase}；本轮优先：{names}"
            "（激活工具直调，其余经 webgis_execute 执行；提示是偏好不是限制）。"
        )
    except Exception:  # noqa: BLE001
        return ""


def _active_tools_block_for(message: str, surface: Any, plan: Any) -> str:
    """动态激活名单 marker（ADR-0103）。失败/关闭 → 空串，扩展保持既有面。"""
    try:
        from app.services.chat.pi_native_surface import compute_turn_active_tools

        capabilities = tuple(
            row.capability
            for row in (getattr(plan, "progress", None) or ())
            if getattr(row, "capability", "")
        )
        names = compute_turn_active_tools(
            message or "",
            active_capabilities=capabilities,
            workflow_stage=str(getattr(surface, "phase", "") or ""),
        )
        if not names:
            return ""
        return f"[{ACTIVE_TOOLS_MARKER}:{json.dumps(names, ensure_ascii=False, separators=(',', ':'))}]"
    except Exception:  # noqa: BLE001 — 动态面绝不阻断 turn
        return ""


class PiTurnRegistry:
    """Encapsulated coordinator for active Pi turn registration (local + Redis)."""

    def __init__(self) -> None:
        self._local_context: Optional[dict[str, str]] = {}
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
        if not isinstance(self._local_context, dict):
            self._local_context = {}
        self._local_context[session_id] = turn_id
        client = self._get_redis_client()
        if client is not None:
            try:
                await client.set(
                    f"webgis:pi:active_turn:{session_id}",
                    turn_id,
                    ex=TURN_CONTEXT_MAX_AGE_SECONDS,
                )
            except Exception as e:
                logger.warning("PiTurnRegistry: Redis register failed for %s: %s", session_id, e)

    async def unregister_turn(self, session_id: str, turn_id: str) -> None:
        if isinstance(self._local_context, dict):
            if self._local_context.get(session_id) == turn_id:
                self._local_context.pop(session_id, None)
        elif isinstance(self._local_context, tuple):
            if self._local_context == (session_id, turn_id):
                self._local_context = None
        client = self._get_redis_client()
        if client is not None:
            try:
                script = (
                    b"if redis.call('get', KEYS[1]) == ARGV[1] then "
                    b"return redis.call('del', KEYS[1]) else return 0 end"
                )
                await client.eval(script, 1, f"webgis:pi:active_turn:{session_id}", turn_id)
            except Exception as e:
                logger.warning("PiTurnRegistry: Redis unregister failed for %s: %s", session_id, e)

    async def is_active(self, session_id: str, turn_id: str) -> bool:
        if isinstance(self._local_context, dict):
            if self._local_context.get(session_id) == turn_id:
                return True
        elif isinstance(self._local_context, tuple):
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
