"""Value types for the project-context block (chat hot path).

These are intentionally tiny, immutable, and have no SQLAlchemy
dependency so they can be cached, compared, and hashed without pulling
the ORM into the cache key.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple


def _dt_to_us(value: Optional[datetime]) -> int:
    """Coerce a possibly-None datetime to a microsecond integer.

    ``None`` ⇒ ``0`` so that projects with no datasets/workflows hash
    deterministically. The DB only ever stores UTC-aware datetimes so
    we can use ``.timestamp()`` safely.
    """
    if value is None:
        return 0
    # Use ``.timestamp()`` which honours tzinfo; the ORM maps
    # DateTime(timezone=True) and the test fixture uses
    # ``datetime.now(timezone.utc)``.
    return int(value.timestamp() * 1_000_000)


@dataclass(frozen=True)
class ProjectFingerprint:
    """Cheap-to-read identity of the project state used for caching.

    Two ``ProjectFingerprint`` instances are equal iff the underlying
    state would produce the same rendered ``<active_project_workspace>``
    block. ``hash(fingerprint)`` is the cache key.
    """

    project_id: str
    project_updated_at: Optional[datetime]
    dataset_count: int
    dataset_max_modified: Optional[datetime]
    workflow_count: int
    workflow_max_updated: Optional[datetime]

    def cache_key(self) -> int:
        # Tuple of hashable primitives; microsecond integer is fine
        # because the DB's on-update keeps ``updated_at`` strictly
        # increasing and Python's hash is stable within a process.
        return hash((
            self.project_id,
            _dt_to_us(self.project_updated_at),
            self.dataset_count,
            _dt_to_us(self.dataset_max_modified),
            self.workflow_count,
            _dt_to_us(self.workflow_max_updated),
        ))


@dataclass(frozen=True)
class ProjectContextSummary:
    """Full slim read of the project used to render the context block."""

    project_id: str
    project_name: str
    dataset_count: int
    workflow_count: int
    dataset_names: Tuple[str, ...]
    workflow_names: Tuple[str, ...]
    project_updated_at: Optional[datetime]
    dataset_max_modified: Optional[datetime]
    workflow_max_updated: Optional[datetime]

    def fingerprint(self) -> ProjectFingerprint:
        return ProjectFingerprint(
            project_id=self.project_id,
            project_updated_at=self.project_updated_at,
            dataset_count=self.dataset_count,
            dataset_max_modified=self.dataset_max_modified,
            workflow_count=self.workflow_count,
            workflow_max_updated=self.workflow_max_updated,
        )

    def render(self) -> str:
        """Render the exact ``<active_project_workspace>`` block.

        Byte-stable contract: keep this function side-effect free and
        pure. The block is appended verbatim to the env_summary, so any
        change to the wording is a context-text change visible to the
        LLM. The existing tests in
        ``tests/perf/test_project_context_cache.py`` assert the
        exact substring.
        """
        return (
            f"\n<active_project_workspace>\n"
            f"Project: {self.project_name} (ID: {self.project_id})\n"
            f"Datasets attached ({self.dataset_count}): {', '.join(self.dataset_names[:5])}\n"
            f"Workflows ({self.workflow_count}): {', '.join(self.workflow_names[:5])}\n"
            f"</active_project_workspace>"
        )


__all__ = ["ProjectFingerprint", "ProjectContextSummary"]
