"""Atomic flock-protected session JSON store & CAS persistence for G48 Georeferencing."""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from pydantic import ValidationError

from app.schemas.georeferencing_schema import (
    FrozenExecutionSnapshot,
    GeorefDomainError,
    GeorefDraftPlan,
    GeorefErrorCode,
    GeorefSourceRecord,
)


logger = logging.getLogger(__name__)


import re
_SAFE_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_RESERVED_NAMES = {"uploads", "projects", "modelops", "content", "blobs", "cache", "exports", "tmp"}


def _data_base() -> Path:
    try:
        from app.core.config import settings

        base = getattr(settings, "DATA_DIR", "") or "./data"
    except Exception:  # noqa: BLE001
        base = "./data"
    return Path(base).resolve()


def _session_dir_for(session_id: str) -> Path:
    if _SAFE_SESSION_RE.fullmatch(session_id) and session_id.lower() not in _RESERVED_NAMES:
        return _data_base() / session_id / "georeferencing"
    return _data_base() / "georeferencing" / hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]


def georef_session_dir(session_id: str) -> Path:
    path = _session_dir_for(session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(session_id: str) -> Path:
    return georef_session_dir(session_id) / "state.json"


def _lock_path(session_id: str) -> Path:
    return georef_session_dir(session_id) / ".state.lock"


@contextmanager
def _session_file_lock(session_id: str) -> Iterator[None]:
    lp = _lock_path(session_id)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lp, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _empty_state(session_id: str) -> Dict[str, Any]:
    return {"session_id": session_id, "sources": {}, "plans": {}, "idempotency": {}}


def _read_state_unlocked(session_id: str) -> Dict[str, Any]:
    sp = _state_path(session_id)
    if not sp.is_file():
        return _empty_state(session_id)
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state root is not a JSON object")
        data.setdefault("sources", {})
        data.setdefault("plans", {})
        data.setdefault("idempotency", {})
        return data
    except Exception as exc:  # noqa: BLE001
        # Fail closed: returning an empty state here would let the next
        # save_source / mutate_plan_cas write it back and silently erase every
        # source/plan record of this session.
        raise GeorefDomainError(
            GeorefErrorCode.SOURCE_CORRUPT_OR_TAMPERED,
            f"Georeference state for session '{session_id}' is unreadable ({exc}); "
            "refusing to treat it as empty or overwrite it.",
            status_code=500,
            details={"session_id": session_id},
        ) from exc


def _write_state_unlocked(session_id: str, state: Dict[str, Any]) -> None:
    sp = _state_path(session_id)
    sp.parent.mkdir(parents=True, exist_ok=True)
    tmp = sp.with_name(f".state.json.tmp-{uuid.uuid4().hex[:8]}")
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, sp)
    finally:
        tmp.unlink(missing_ok=True)


def save_source(record: GeorefSourceRecord) -> GeorefSourceRecord:
    with _session_file_lock(record.session_id):
        state = _read_state_unlocked(record.session_id)
        state["sources"][record.source_id] = record.model_dump(mode="json")
        _write_state_unlocked(record.session_id, state)
    return record


def get_source(session_id: str, source_id: str) -> Optional[GeorefSourceRecord]:
    with _session_file_lock(session_id):
        state = _read_state_unlocked(session_id)
        raw = state["sources"].get(source_id)
    return GeorefSourceRecord.model_validate(raw) if raw else None


def list_sources(session_id: str) -> List[GeorefSourceRecord]:
    with _session_file_lock(session_id):
        state = _read_state_unlocked(session_id)
        items = list(state["sources"].values())
    return [GeorefSourceRecord.model_validate(x) for x in items]


def save_plan_new(plan: GeorefDraftPlan) -> GeorefDraftPlan:
    with _session_file_lock(plan.session_id):
        state = _read_state_unlocked(plan.session_id)
        state["plans"][plan.plan_id] = plan.model_dump(mode="json")
        _write_state_unlocked(plan.session_id, state)
    return plan


def get_plan(session_id: str, plan_id: str) -> Optional[GeorefDraftPlan]:
    with _session_file_lock(session_id):
        state = _read_state_unlocked(session_id)
        raw = state["plans"].get(plan_id)
    if not raw:
        return None
    try:
        return GeorefDraftPlan.model_validate(raw)
    except ValidationError as exc:
        raise GeorefDomainError(
            GeorefErrorCode.SOURCE_CORRUPT_OR_TAMPERED,
            f"Georeference plan '{plan_id}' failed schema validation: {exc}",
            status_code=500,
            details={"session_id": session_id, "plan_id": plan_id},
        ) from exc


def list_plans(session_id: str) -> List[GeorefDraftPlan]:
    with _session_file_lock(session_id):
        state = _read_state_unlocked(session_id)
        items = list(state["plans"].values())
    plans: List[GeorefDraftPlan] = []
    for raw in items:
        try:
            plans.append(GeorefDraftPlan.model_validate(raw))
        except ValidationError:
            # Per-record isolation: one unparseable plan (schema drift/manual
            # edit) must not break listing every other plan in the session.
            logger.warning(
                "[georef] skipping unparseable plan record in session %s", session_id, exc_info=True
            )
    return plans


def mutate_plan_cas(
    session_id: str,
    plan_id: str,
    *,
    expected_revision: Optional[int],
    mutator: Callable[[GeorefDraftPlan], GeorefDraftPlan],
) -> GeorefDraftPlan:
    """Atomically load, CAS-check ``expected_revision`` if provided, apply mutator, and persist."""
    with _session_file_lock(session_id):
        state = _read_state_unlocked(session_id)
        raw = state["plans"].get(plan_id)
        if raw is None:
            raise GeorefDomainError(
                GeorefErrorCode.FORBIDDEN_OR_NOT_FOUND,
                f"Georeference plan '{plan_id}' not found.",
                status_code=404,
            )
        current = GeorefDraftPlan.model_validate(raw)
        if expected_revision is not None and current.revision != int(expected_revision):
            raise GeorefDomainError(
                GeorefErrorCode.CAS_CONFLICT,
                f"Revision mismatch for plan '{plan_id}': expected {expected_revision}, current is {current.revision}.",
                status_code=409,
                details={"expected_revision": expected_revision, "current_revision": current.revision},
            )
        updated = mutator(current)
        state["plans"][plan_id] = updated.model_dump(mode="json")
        _write_state_unlocked(session_id, state)
        return updated


def freeze_plan_for_execution(
    session_id: str,
    plan_id: str,
    *,
    expected_revision: int,
    snapshot: FrozenExecutionSnapshot,
    execution_id: str,
    job_id: str,
    conflict_checker: Callable[[GeorefDraftPlan], None],
    idempotency_key: Optional[str] = None,
    idempotency_payload: Optional[Dict[str, Any]] = None,
) -> GeorefDraftPlan:
    """Atomically freeze the plan, attach the active job, and record the idempotency entry.

    单把 flock 关键区 —— 关闭 freeze→attach 之间的 TOCTOU 窗口（issue #2192）：
    并发 execute（含跨进程）在此要么见到存活冲突（conflict_checker 抛 409），
    要么原子落下 snapshot/execution_id/active_job_id/幂等登记，绝无中间态。
    """
    with _session_file_lock(session_id):
        state = _read_state_unlocked(session_id)
        raw = state["plans"].get(plan_id)
        if raw is None:
            raise GeorefDomainError(
                GeorefErrorCode.FORBIDDEN_OR_NOT_FOUND,
                f"Georeference plan '{plan_id}' not found.",
                status_code=404,
            )
        current = GeorefDraftPlan.model_validate(raw)
        if current.revision != int(expected_revision):
            raise GeorefDomainError(
                GeorefErrorCode.CAS_CONFLICT,
                f"Revision mismatch for plan '{plan_id}': expected {expected_revision}, current is {current.revision}.",
                status_code=409,
                details={"expected_revision": expected_revision, "current_revision": current.revision},
            )
        conflict_checker(current)
        current.frozen_snapshot = snapshot
        current.active_execution_id = execution_id
        current.active_job_id = str(job_id)
        current.updated_at = datetime.now(timezone.utc).isoformat()
        state["plans"][plan_id] = current.model_dump(mode="json")
        if idempotency_key:
            idem = state.setdefault("idempotency", {})
            if len(idem) > 128:
                oldest = next(iter(idem))
                idem.pop(oldest, None)
            idem[idempotency_key] = idempotency_payload or {}
        _write_state_unlocked(session_id, state)
        return current


def record_idempotency_entry(
    session_id: str,
    key: str,
    payload: Dict[str, Any],
) -> None:
    if not key:
        return
    with _session_file_lock(session_id):
        state = _read_state_unlocked(session_id)
        idem = state.setdefault("idempotency", {})
        if len(idem) > 128:
            oldest = next(iter(idem))
            idem.pop(oldest, None)
        idem[key] = payload
        _write_state_unlocked(session_id, state)


def get_idempotency_entry(session_id: str, key: str) -> Optional[Dict[str, Any]]:
    if not key:
        return None
    with _session_file_lock(session_id):
        state = _read_state_unlocked(session_id)
        return state.get("idempotency", {}).get(key)


def delete_source_and_plans(session_id: str, source_id: str) -> Tuple[bool, List[str]]:
    """Remove a source and dependent plans from session state, returning released sha256 blobs."""
    shas_to_release: List[str] = []
    with _session_file_lock(session_id):
        state = _read_state_unlocked(session_id)
        raw_src = state["sources"].pop(source_id, None)
        if raw_src is None:
            return False, []
        src = GeorefSourceRecord.model_validate(raw_src)
        shas_to_release.extend([src.original_sha256, src.normalized_sha256])

        # Clean source files on disk cleanly.
        sdir = georef_session_dir(session_id)
        for p in (Path(src.normalized_path), sdir / "sources" / f"{source_id}_viewer.png"):
            p.unlink(missing_ok=True)

        # Clean dependent plans & artifacts.
        remove_pids = [pid for pid, rp in state["plans"].items() if rp.get("source_id") == source_id]
        for pid in remove_pids:
            rp = GeorefDraftPlan.model_validate(state["plans"].pop(pid))
            if rp.preview_path:
                Path(rp.preview_path).unlink(missing_ok=True)
            for art in [rp.latest_artifact, *rp.superseded_artifacts]:
                if art is not None:
                    shas_to_release.append(art.sha256)
                    Path(art.file_path).unlink(missing_ok=True)
        _write_state_unlocked(session_id, state)
    return True, [s for s in shas_to_release if s]


def purge_session_files(session_id: str) -> int:
    """Release all BlobStore refs and delete local session directory for ``session_id``."""
    from app.services.georeferencing.refs import release_blob_ref

    released = 0
    shas: List[str] = []
    with _session_file_lock(session_id):
        state = _read_state_unlocked(session_id)
        for raw_src in state["sources"].values():
            src = GeorefSourceRecord.model_validate(raw_src)
            shas.extend([src.original_sha256, src.normalized_sha256])
        for raw_plan in state["plans"].values():
            plan = GeorefDraftPlan.model_validate(raw_plan)
            for art in [plan.latest_artifact, *plan.superseded_artifacts]:
                if art is not None:
                    shas.append(art.sha256)
    for sha in shas:
        if release_blob_ref(sha, session_id):
            released += 1
    sdir = _session_dir_for(session_id)
    if sdir.exists():
        shutil.rmtree(sdir, ignore_errors=True)
    return released


def iter_all_session_states() -> List[Dict[str, Any]]:
    """Enumerate persisted session state files (used ONLY by startup recovery)."""
    base = _data_base()
    if not base.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    patterns = (
        base.glob("*/georeferencing/state.json"),
        (base / "georeferencing").glob("*/state.json"),
    )
    for group in patterns:
        for sp in group:
            try:
                data = json.loads(sp.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("session_id"):
                    out.append(data)
            except Exception:  # noqa: BLE001
                continue
    return out
