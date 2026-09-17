"""权威源 liveness 复核：读权威源的**廉价当前 token**，供 lazy 失效判定。

本模块只读权威表（绝不写）；每个探针都是单行/聚合标量查询（与
``ProjectService.get_project_fingerprint`` 同成本纪律）。token 语义：

- 返回 ``str`` = 权威当前版本指纹（与投影行 token 相等 → live）；
- 返回 ``None`` = 权威行消失/detached → 投影行 ``invalidated``（scope_gone）；
- ``""`` = 权威行存在但无指纹列值（与空 token 行相等 → live）。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.project_knowledge.contract import (
    AS_ARTIFACT,
    AS_CARTO_FACT,
    AS_GIS_MEMORY,
    AS_MAP_PRODUCT,
    AS_MISSION,
    AS_PROJECT_DATASET,
    AS_WORKFLOW,
)

logger = logging.getLogger(__name__)


def _mp_key(authority_id: str) -> Optional[tuple[str, int]]:
    """map product 权威 id 编码 ``mp:<project_id>:<version_no>`` → 解析。"""
    parts = str(authority_id or "").split(":")
    if len(parts) != 3 or parts[0] != "mp":
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None


def live_version_token(
    db: Session,
    *,
    project_id: str,
    authority_store: str,
    authority_id: str,
) -> Optional[str]:
    """读权威源当前 token；权威行消失返回 None（不可伪造 live）。"""
    from app.models.project import (
        Artifact,
        ArtifactRevision,
        CartoProjectFact,
        MapProductVersion,
        ProjectDataset,
        Workflow,
        WorkflowRevision,
    )
    from app.models.mission import GISMissionRow
    from app.models.spatial_memory import GISSpatialMemory

    if authority_store == AS_PROJECT_DATASET:
        row = db.execute(
            select(ProjectDataset).where(
                ProjectDataset.id == authority_id,
                ProjectDataset.project_id == project_id,
            )
        ).scalar_one_or_none()
        if row is None or row.detached_at is not None:
            return None
        return str(row.version_fingerprint or "")

    if authority_store == AS_ARTIFACT:
        art = db.execute(
            select(Artifact).where(
                Artifact.id == authority_id,
                Artifact.project_id == project_id,
            )
        ).scalar_one_or_none()
        if art is None:
            return None
        head = db.execute(
            select(ArtifactRevision.content_sha256)
            .where(ArtifactRevision.artifact_id == authority_id)
            .order_by(ArtifactRevision.revision_no.desc())
            .limit(1)
        ).scalar_one_or_none()
        return str(head or art.content_fingerprint or "")

    if authority_store == AS_MAP_PRODUCT:
        key = _mp_key(authority_id)
        if key is None or key[0] != project_id:
            return None
        row = db.execute(
            select(MapProductVersion.product_fingerprint).where(
                MapProductVersion.project_id == project_id,
                MapProductVersion.version_no == key[1],
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return str(row or "")

    if authority_store == AS_WORKFLOW:
        wf = db.execute(
            select(Workflow).where(
                Workflow.id == authority_id,
                Workflow.project_id == project_id,
            )
        ).scalar_one_or_none()
        if wf is None:
            return None
        if not wf.current_revision_id:
            return ""
        fp = db.execute(
            select(WorkflowRevision.graph_fingerprint).where(
                WorkflowRevision.id == wf.current_revision_id
            )
        ).scalar_one_or_none()
        return str(fp or "")

    if authority_store == AS_MISSION:
        row = db.execute(
            select(GISMissionRow).where(
                GISMissionRow.mission_id == authority_id,
                GISMissionRow.project_id == project_id,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return str(row.revision or 1)

    if authority_store == AS_GIS_MEMORY:
        row = db.get(GISSpatialMemory, authority_id)
        if row is None or row.status != "active":
            return None
        if row.expires_at is not None:
            from app.services.gis_memory.store import _now_naive

            if row.expires_at <= _now_naive():
                return None
        return str(row.fingerprint or "")

    if authority_store == AS_CARTO_FACT:
        row = db.execute(
            select(CartoProjectFact).where(
                CartoProjectFact.id == authority_id,
                CartoProjectFact.project_id == project_id,
            )
        ).scalar_one_or_none()
        if row is None or row.status not in ("active", "conflicted"):
            return None
        return str(row.fingerprint or "")

    # session_ref / 未知 store：投影行不可 durable 复核 → fail-closed 判消失。
    return None


__all__ = ["live_version_token"]
