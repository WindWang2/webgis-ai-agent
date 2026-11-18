"""Neutral export bytes/owner-receipt persistence shared by routes and services.

Authorization and signed URLs remain with the callers. This module does not
import API code or infer a principal: it persists the already validated owner.
The optional cache arguments preserve private route-fixture compatibility;
production callers share the same bounded cache and call-time export root.
"""
from __future__ import annotations

from collections import OrderedDict
import os
from pathlib import Path
import tempfile
from typing import Optional
import uuid

from app.services import export_paths

EXPORT_OWNERS: OrderedDict[str, str] = OrderedDict()
EXPORT_OWNERS_MAX = 2048


def remember_export_owner(
    filename: str, owner: str, *,
    owners: Optional[OrderedDict[str, str]] = None,
    max_entries: int = EXPORT_OWNERS_MAX,
) -> None:
    cache = EXPORT_OWNERS if owners is None else owners
    cache[filename] = owner
    cache.move_to_end(filename)
    while len(cache) > max_entries:
        cache.popitem(last=False)


def set_export_owner(
    filename: str, user_id: str, *,
    owners: Optional[OrderedDict[str, str]] = None,
    max_entries: int = EXPORT_OWNERS_MAX,
) -> None:
    """Atomically persist a durable receipt before updating the shared cache."""
    path = Path(export_paths.ensure_exports_root()) / f"{filename}.owner"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(user_id)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    remember_export_owner(filename, user_id, owners=owners, max_entries=max_entries)


def get_export_owner(
    filename: str, *, owners: Optional[OrderedDict[str, str]] = None,
    max_entries: int = EXPORT_OWNERS_MAX,
) -> Optional[str]:
    """Cache fast path, then durable sidecar fallback; unreadable means unknown."""
    cache = EXPORT_OWNERS if owners is None else owners
    if filename in cache:
        return cache[filename]
    meta_path = os.path.join(str(export_paths.exports_root()), f"{filename}.owner")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                owner = handle.read().strip()
                remember_export_owner(filename, owner, owners=cache, max_entries=max_entries)
                return owner
        except Exception:
            pass
    return None


def persist_export_file(filename: str, content: bytes, ext: str) -> None:
    """Synchronous temporary write + atomic replace; async callers offload it."""
    with tempfile.NamedTemporaryFile(
        dir=export_paths.ensure_exports_root(), delete=False, suffix=ext,
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    os.replace(tmp_path, os.path.join(str(export_paths.ensure_exports_root()), filename))
