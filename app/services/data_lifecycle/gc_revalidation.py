"""Recheck only frozen GC candidates without discovering new deletion targets."""
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.data_lifecycle import LifecycleObject, LifecyclePolicy
from app.models.db_model import GeoComputeWorkerCache
from app.services.data_lifecycle.policy import classify_tier


def _utc(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def candidate_still_cold(db: Session, entry: Mapping[str, object], path: Path | None) -> tuple[bool, str]:
    kind, object_id = str(entry.get("kind") or ""), str(entry.get("object_id") or "")
    policy = db.query(LifecyclePolicy).filter_by(kind=kind).order_by(
        LifecyclePolicy.enabled.desc(), LifecyclePolicy.created_at.desc()).first()
    if policy is None or not policy.enabled or policy.action not in ("stage_delete", "delete"):
        return False, "policy_changed"
    row = db.query(LifecycleObject).filter_by(kind=kind, object_id=object_id).one_or_none()
    if row is None:
        return False, "freshness_unavailable"
    if str(row.owner_scope or "") != str(entry.get("owner_scope") or ""):
        return False, "owner_changed"
    observations = [_utc(row.last_used_at)]
    if kind == "worker_cache":
        worker, _, key = object_id.partition(":")
        current = db.get(GeoComputeWorkerCache, (worker, key))
        if current is not None:
            if str(current.owner_scope or "") != str(entry.get("owner_scope") or ""):
                return False, "owner_changed"
            observations.append(_utc(current.last_hit_at))
    elif path is not None:
        paths = [path, path.with_suffix(".meta")] if kind == "artifact_cache" else [path]
        for current_path in paths:
            try:
                observations.append(datetime.fromtimestamp(current_path.stat().st_mtime, timezone.utc))
            except FileNotFoundError:
                continue
            except OSError:
                return False, "freshness_unavailable"
    latest = max((stamp for stamp in observations if stamp is not None), default=None)
    thresholds = getattr(policy, "tier_thresholds", None)
    tier = classify_tier(latest, thresholds if isinstance(thresholds, dict) else None)
    return (True, "cold") if tier == "cold" else (False, "revived_" + tier)
