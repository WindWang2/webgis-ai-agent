"""Resettable, thread-safe skill instances; mutable evidence is scoped to a session."""
from collections import OrderedDict
from threading import RLock
from typing import Callable, TypeVar, cast

T = TypeVar('T')
_instances: OrderedDict[tuple[str, str], object] = OrderedDict()
_lock = RLock()


def _scope() -> str:
    from app.lib.runtime.context import current_runtime_context
    context = current_runtime_context()
    return str(getattr(context, 'session_id', '') or 'unbound')


def skill_instance(name: str, factory: Callable[[], T], *, mutable: bool = True) -> T:
    key = (name, _scope() if mutable else 'catalog')
    with _lock:
        existing = _instances.pop(key, None)
        if existing is None:
            existing = factory()
        _instances[key] = existing
        if len(_instances) > 512:
            _instances.popitem(last=False)
        return cast(T, existing)


def reset_skill_instance(name: str) -> None:
    with _lock:
        for key in [key for key in _instances if key[0] == name]:
            del _instances[key]
