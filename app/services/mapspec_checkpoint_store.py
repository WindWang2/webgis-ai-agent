"""CheckpointStore (Compatibility Re-export).

Re-exports `snapshot` and `rollback` from `app.services.mapspec.checkpoint`.
"""
from app.services.mapspec.checkpoint import snapshot, rollback

__all__ = ["snapshot", "rollback"]
