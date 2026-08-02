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


# Backward compatibility aliases
SessionDataProtocol = SessionStoreProtocol

_active_store: Optional[SessionStoreProtocol] = None


def get_session_store() -> SessionStoreProtocol:
    """Return active SessionStore singleton instance."""
    global _active_store
    if _active_store is None:
        try:
            from app.core.config import settings
            if getattr(settings, "REDIS_ENABLED", False):
                from app.services.session_data_redis import session_data_manager as redis_mgr
                _active_store = redis_mgr
            else:
                from app.services.session_data import session_data_manager
                _active_store = session_data_manager
        except Exception:
            from app.services.session_data import session_data_manager
            _active_store = session_data_manager
    return _active_store


def set_active_session_store(store: SessionStoreProtocol) -> None:
    """Set custom active session store for testing or alternative providers."""
    global _active_store
    _active_store = store
