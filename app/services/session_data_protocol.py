"""
SessionStore Protocol & Result Value Objects (app/services/session_data_protocol.py)

Defines the deep SessionStore seam interface and immutable SessionRefDataResult value object.
"""

import hmac
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Union, runtime_checkable


# Sentinel prefix minted by the Redis backend when the store is unavailable,
# so a transient Redis timeout does NOT crash the chat dispatch loop (the store
# returns this marker instead of raising — see audit C3). Consumers that require
# a *retrievable* ref (e.g. Data Fabric materialization) MUST detect it via
# ``is_unavailable_ref`` and report failure rather than treating it as a real ref.
# Invariant: a ref exists iff its payload is retrievable.
UNAVAILABLE_REF_PREFIX = "ref:redis-unavailable-"


def is_unavailable_ref(ref_id: Optional[str]) -> bool:
    """True iff ``ref_id`` is a non-retrievable store-unavailability sentinel.

    A ref minted under this prefix has NO payload stored anywhere; ``get`` will
    always miss. Any caller that persists or returns a ref to a downstream
    consumer must treat this as failure, not success.
    """
    return bool(ref_id) and ref_id.startswith(UNAVAILABLE_REF_PREFIX)


@dataclass(frozen=True)
class SessionRefDataResult:
    """Immutable value object returned by SessionStore.get_ref_data()."""
    success: bool
    data: Optional[Union[Dict[str, Any], List[Any], str, int, float, bool]] = None
    error: Optional[str] = None
    error_type: Optional[str] = None


@runtime_checkable
class SessionStoreProtocol(Protocol):
    """Protocol defining the deep SessionStore interface for memory and Redis adapters."""

    async def get(self, session_id: str, ref_id: str) -> Optional[Any]:
        ...

    async def store(self, session_id: str, data: Any, prefix: str = "data") -> str:
        ...

    async def overwrite(self, session_id: str, ref_id: str, data: Any) -> None:
        ...

    async def delete_ref(self, session_id: str, ref_id: str) -> bool:
        """Drop one stored ref (and alias/descriptor). False if missing."""
        ...

    async def set_alias(self, session_id: str, ref_id: str, alias: str) -> None:
        ...

    async def resolve_alias(self, session_id: str, ref_or_alias: str) -> str:
        ...

    async def resolve_aliases(self, session_id: str, strings: List[str]) -> Dict[str, str]:
        """Batch alias resolution: maps each input string to its canonical
        ref_id, or the input unchanged when it is not an alias.

        Must cost a single round-trip (one HMGET) for the whole list — the
        registry's reference-resolution hot path calls this once per dispatch
        instead of one resolve_alias RTT per string argument.
        """
        ...

    async def get_ref_data(
        self,
        session_id: str,
        ref_or_alias: str,
        owner_token: Optional[str] = None,
    ) -> SessionRefDataResult:
        """Deep interface method: resolves alias, validates security token, and returns deserialized data."""
        ...

    async def get_map_state(self, session_id: str) -> Dict[str, Any]:
        ...

    def invalidate_local_cache(self, session_id: str) -> None:
        """Discard process-local state before a distributed critical read."""
        ...

    async def set_map_state(self, session_id: str, key: str, value: Any, seq: Optional[int] = None) -> bool:
        """Persist one map-state key; ``seq`` (F4) is a monotonic per-key
        sequence number — a write is applied only when its seq is strictly
        newer than the stored one (stale out-of-order writes are rejected and
        return False). Returns True when the write was applied."""
        ...

    async def update_layer_in_state(self, session_id: str, layer_id: str, updates: dict) -> bool:
        ...

    async def remove_layer_from_state(self, session_id: str, layer_id: str) -> bool:
        ...

    async def set_session_clearing(self, session_id: str, ttl_s: int = 30) -> None:
        """#750: publish a short-lived cross-replica "clearing" marker so an
        in-flight turn on ANOTHER pod suppresses its DB writes for a session
        being evicted/deleted here (the in-process set is pod-local)."""

    async def is_session_clearing(self, session_id: str) -> bool:
        """Whether a clearing marker is currently live for the session."""
        return False

    async def clear_session(self, session_id: str) -> None:
        ...

    async def get_session_metadata(self, session_id: str) -> Dict[str, Any]:
        ...

    async def get_started_at(self, session_id: str) -> Optional[str]:
        ...

    async def append_event(self, session_id: str, event: str, data: dict) -> None:
        ...

    async def get_event_log(self, session_id: str) -> list[dict]:
        ...

    async def append_map_action_event(self, session_id: str, event: dict) -> bool:
        """Append a terminal map-action ACK (Harness–Map Interaction V3).
        Idempotent by ``action_id`` — first terminal state wins, duplicates return
        False. Bounded per session (MAX_MAP_ACTION_EVENTS), oldest evicted first."""
        ...

    async def get_map_action_events(self, session_id: str) -> list[dict]:
        """Return all current map-action ACKs for a session, in arrival order."""
        ...

    async def list_refs(self, session_id: str) -> dict[str, str]:
        ...

    async def get_ref_descriptor(self, session_id: str, ref_id: str) -> "Optional[Dict[str, Any]]":
        """V3 Performance: Return pre-computed descriptor metadata for a ref without
        reading or scanning the full data payload. None if ref not found."""
        ...

    async def get_ref_descriptor_authorized(
        self,
        session_id: str,
        ref_id: str,
        owner_token: Optional[str] = None,
    ) -> SessionRefDataResult:
        """Metadata-only variant of get_ref_data for the descriptor fast path.

        Validates the owner token and ref existence and returns the pre-computed
        descriptor WITHOUT reading or deserializing the full data payload
        (no get() call). error_type semantics match get_ref_data:
        PermissionDenied on token mismatch, NotFound when there is no descriptor
        or the underlying ref payload is gone.

        Alias 输入会解析为 canonical ref_id（与 get_ref_data 一致，遵循
        resolve_alias / resolve_aliases 的已有模式），403/404 语义对真正
        缺失的 ref 保持不变。

        Caveat: the "never hydrates" promise assumes the descriptor key exists.
        RedisSessionStore.get_ref_descriptor has a pre-existing on-the-fly
        fallback — when the descriptor/meta key is missing (pre-V3 ref, or the
        meta key's TTL expired before the data key's) it reads the full payload,
        recomputes and caches the descriptor, so this method hydrates in that
        case. This is pre-existing behaviour affecting only legacy refs.
        """
        ...

    async def ref_exists(self, session_id: str, ref_id: str) -> bool:
        """O(1) existence check for a ref payload — does not read or deserialize it."""
        ...

    async def cleanup_idle_sessions(self, max_sessions: int = 100) -> None:
        ...


class BaseSessionStore:
    """Abstract base class providing unified domain logic for SessionStore implementations.

    Subclasses must implement: `get`, `store`, `overwrite`, `set_alias`,
    `get_map_state`, `set_map_state`, `get_session_metadata`, etc.
    """

    async def resolve_alias(self, session_id: str, ref_or_alias: str) -> str:
        """Default fallback alias resolution. Overridden by subclasses if alias map is separate."""
        return ref_or_alias

    def _validate_owner_token(self, meta: Optional[Dict[str, Any]], owner_token: Optional[str]) -> Optional[SessionRefDataResult]:
        """Shared owner-token check for get_ref_data / get_ref_descriptor_authorized.
        Returns a PermissionDenied result if the token mismatches, else None.

        The expected credential is stored as a SHA-256 DIGEST
        (``owner_token_digest`` in map_state — map_state is echoed to clients,
        so only a one-way form is persisted). A raw-token form is still
        honored for back-compat if a legacy writer ever supplied one."""
        import hashlib

        meta = meta or {}
        map_state = meta.get("map_state", {})
        expected_digest = meta.get("owner_token_digest") or map_state.get("owner_token_digest")
        if expected_digest:
            presented_digest = (
                hashlib.sha256(str(owner_token).encode()).hexdigest()
                if owner_token else None
            )
            if not presented_digest or not hmac.compare_digest(
                presented_digest, str(expected_digest)
            ):
                return SessionRefDataResult(
                    success=False,
                    error="Security token mismatch",
                    error_type="PermissionDenied",
                )
            return None
        # Legacy raw-token form (defensive; no current writer).
        expected_token = meta.get("owner_token") or map_state.get("owner_token")
        if expected_token and (
            not owner_token
            or not hmac.compare_digest(str(owner_token), str(expected_token))
        ):
            return SessionRefDataResult(
                success=False,
                error="Security token mismatch",
                error_type="PermissionDenied",
            )
        return None

    async def get_ref_data(
        self,
        session_id: str,
        ref_or_alias: str,
        owner_token: Optional[str] = None,
    ) -> SessionRefDataResult:
        """Deep interface method: resolves alias, validates owner token if present, and returns deserialized data."""
        meta = await self.get_session_metadata(session_id)
        denied = self._validate_owner_token(meta, owner_token)
        if denied is not None:
            return denied

        raw_data = await self.get(session_id, ref_or_alias)
        if raw_data is None:
            return SessionRefDataResult(
                success=False,
                error="Referenced data expired or not found",
                error_type="NotFound",
            )

        if isinstance(raw_data, str):
            try:
                import json
                parsed = json.loads(raw_data)
                return SessionRefDataResult(success=True, data=parsed)
            except Exception:
                return SessionRefDataResult(success=True, data=raw_data)

        return SessionRefDataResult(success=True, data=raw_data)

    async def get_ref_descriptor_authorized(
        self,
        session_id: str,
        ref_id: str,
        owner_token: Optional[str] = None,
    ) -> SessionRefDataResult:
        """Metadata-only variant of get_ref_data for the descriptor fast path.

        Validates the owner token and ref existence and returns the pre-computed
        descriptor WITHOUT reading or deserializing the full data payload
        (no get() call). error_type semantics match get_ref_data:
        PermissionDenied on token mismatch, NotFound when there is no descriptor
        or the underlying ref payload is gone.

        Alias 输入会解析为 canonical ref_id（与 get_ref_data 一致，遵循
        resolve_alias 的已有模式），403/404 语义对真正缺失的 ref 保持不变。

        Caveat: the "never hydrates" promise assumes the descriptor key exists.
        RedisSessionStore.get_ref_descriptor has a pre-existing on-the-fly
        fallback — when the descriptor/meta key is missing (pre-V3 ref, or the
        meta key's TTL expired before the data key's) it reads the full payload,
        recomputes and caches the descriptor, so this method hydrates in that
        case. This is pre-existing behaviour affecting only legacy refs.
        """
        meta = await self.get_session_metadata(session_id)
        denied = self._validate_owner_token(meta, owner_token)
        if denied is not None:
            return denied

        # Alias resolution — follow the same pattern as get_ref_data → get() which
        # resolves alias internally; keep 403/404 unchanged for genuinely missing refs.
        canonical_ref = await self.resolve_alias(session_id, ref_id)

        descriptor = await self.get_ref_descriptor(session_id, canonical_ref)
        if not descriptor:
            return SessionRefDataResult(
                success=False,
                error="Referenced data expired or not found",
                error_type="NotFound",
            )

        exists = await self.ref_exists(session_id, canonical_ref)
        if not exists:
            return SessionRefDataResult(
                success=False,
                error="Referenced data expired or not found",
                error_type="NotFound",
            )

        return SessionRefDataResult(success=True, data=descriptor)


# Backward compatibility aliases
SessionDataProtocol = SessionStoreProtocol


_active_store: Optional[SessionStoreProtocol] = None


def get_session_store() -> SessionStoreProtocol:
    """Return active SessionStore singleton instance.

    REVIEW-P1-6: this seam had two latent faults that always landed in the
    `except Exception` fallback to memory:
      (a) it gates on `settings.REDIS_ENABLED` — that field does not exist;
          the real config field is `USE_REDIS` (app/core/config.py:98).
      (b) the Redis branch tries to import `session_data_manager` from
          `session_data_redis`, but that module never defines it (it has
          `RedisSessionStore` and `RedisSessionDataManager`, not
          `session_data_manager`); the ImportError is swallowed and the
          memory fallback runs.

    Both are silent in any environment that has `USE_REDIS=True` in
    settings, because the fallback path *works* — it just isn't the Redis
    backend, defeating the protocol-parity contract ADR-0035 set out to
    guarantee.

    Delegate to `create_session_data_manager()`, which already implements
    the right config-gate + Redis-or-memory selection with a narrower
    `ImportError`-only fallback.
    """
    global _active_store
    if _active_store is None:
        # P2: 与 `session_data_manager`（session_data.py 模块单例）共用同一个
        # 实例，而不是各建一个 —— 原来两个独立 RedisSessionStore 各自持有 L1
        # 缓存：引擎经 session_data_manager 的写不会失效 explorer 经
        # get_session_store() 的 L1（反之亦然），同 id 会话存在 ≤L1_TTL 的
        # 陈旧读取。共享实例后 L1 写失效对所有消费方可见。
        from app.services.session_data import session_data_manager

        _active_store = session_data_manager
    return _active_store


def set_active_session_store(store: SessionStoreProtocol) -> None:
    """Set custom active session store for testing or alternative providers."""
    global _active_store
    _active_store = store
