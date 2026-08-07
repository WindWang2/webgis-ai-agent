"""
SessionStore Protocol & Result Value Objects (app/services/session_data_protocol.py)

Defines the deep SessionStore seam interface and immutable SessionRefDataResult value object.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Union, runtime_checkable


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

    async def set_map_state(self, session_id: str, key: str, value: Any) -> None:
        ...

    async def update_layer_in_state(self, session_id: str, layer_id: str, updates: dict) -> None:
        ...

    async def remove_layer_from_state(self, session_id: str, layer_id: str) -> None:
        ...

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

    async def list_refs(self, session_id: str) -> dict[str, str]:
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

    async def get_ref_data(
        self,
        session_id: str,
        ref_or_alias: str,
        owner_token: Optional[str] = None,
    ) -> SessionRefDataResult:
        """Deep interface method: resolves alias, validates owner token if present, and returns deserialized data."""
        meta = await self.get_session_metadata(session_id)
        map_state = meta.get("map_state", {}) if meta else {}
        expected_token = (meta.get("owner_token") if meta else None) or map_state.get("owner_token")
        if expected_token and owner_token != expected_token:
            return SessionRefDataResult(
                success=False,
                error="Security token mismatch",
                error_type="PermissionDenied",
            )

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
        from app.services.session_data import create_session_data_manager

        _active_store = create_session_data_manager()
    return _active_store


def set_active_session_store(store: SessionStoreProtocol) -> None:
    """Set custom active session store for testing or alternative providers."""
    global _active_store
    _active_store = store
