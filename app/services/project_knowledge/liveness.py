"""权威源 liveness 复核：读权威源的**廉价当前 token**，供 lazy 失效判定。

本模块只读权威表（绝不写）；每个探针都是单行/聚合标量查询（与
``ProjectService.get_project_fingerprint`` 同成本纪律）。token 语义：

- 返回 ``str`` = 权威当前版本指纹（与投影行 token 相等 → live）；
- 返回 ``None`` = 权威行消失/detached → 投影行 ``invalidated``（scope_gone）；
- ``""`` = 权威行存在但无指纹列值（与空 token 行相等 → live）。

作用域口径：每个探针都带 ``project_id`` 条件（与投影侧的索引谓词同一
作用域），查不到即 ``None`` —— 绝不按主键裸查。例外只有
``AS_GIS_MEMORY``：``GISSpatialMemory`` 没有 ``project_id`` 列，其项目
作用域编码在 ``(scope='project', scope_id=project_id)``（模型注释），探针
按该 store 自身口径过滤；``org_id`` 不在探针签名中（本模块所有分支均只带
project_id），而 authority_id 是 uuid4 主键 —— 跨租户 id 碰撞不是现实路径，
故此处显式声明口径而非静默豁免。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Tuple

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
        # GISSpatialMemory 没有 project_id 列（模型 app/models/spatial_memory.py:54-57：
        # 作用域是 (org_id, scope, scope_id) 三元组）。投影侧只索引 project
        # 作用域的记忆（indexer.index_places 的同一谓词），因此探针按该
        # store 自身的作用域口径过滤：``scope == 'project'`` ∧
        # ``scope_id == project_id`` —— 与同函数其余权威分支的 project_id
        # 条件等价，跨项目/跨 scope 的 id 碰撞不再命中他人的行（fail-closed
        # 判 None）。org_id 不在探针签名里（本模块所有分支都只带
        # project_id），且 authority_id 是 uuid4 主键 —— 跨租户碰撞不是现实
        # 路径，此处显式声明该口径而非静默裸查。
        row = db.execute(
            select(GISSpatialMemory).where(
                GISSpatialMemory.id == authority_id,
                GISSpatialMemory.scope == "project",
                GISSpatialMemory.scope_id == project_id,
            )
        ).scalar_one_or_none()
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


def live_version_tokens(
    db: Session,
    *,
    project_id: str,
    probes: Sequence[Tuple[str, str]],
) -> Dict[Tuple[str, str], Optional[str]]:
    """批量读权威源当前 token（#1721：检索热路径的逐条 N+1 探测改批量）。

    与逐条 ``live_version_token`` 语义逐字一致（同表、同谓词、同 token
    规则、同样 fail-closed），仅按 authority_store 分组成批 IN 查询：
    每次调用每类权威至多一次查询（artifact / workflow 各两次：主行 +
    指纹行），未知 store / 畸形 id → ``None`` 且不产生查询。

    返回 ``{(authority_store, authority_id): token}``；探针缺失（键值
    ``None``）= 权威行消失/detached —— 与逐条版的 ``None`` 同义。重复探针
    过去重。
    """
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

    out: Dict[Tuple[str, str], Optional[str]] = {p: None for p in probes}
    by_store: Dict[str, List[str]] = {}
    for store, aid in probes:
        by_store.setdefault(store, []).append(aid)

    if AS_PROJECT_DATASET in by_store:
        rows = db.execute(
            select(ProjectDataset).where(
                ProjectDataset.id.in_(by_store[AS_PROJECT_DATASET]),
                ProjectDataset.project_id == project_id,
            )
        ).scalars().all()
        for row in rows:
            out[(AS_PROJECT_DATASET, row.id)] = (
                None if row.detached_at is not None
                else str(row.version_fingerprint or "")
            )

    if AS_ARTIFACT in by_store:
        arts = db.execute(
            select(Artifact).where(
                Artifact.id.in_(by_store[AS_ARTIFACT]),
                Artifact.project_id == project_id,
            )
        ).scalars().all()
        art_by_id = {a.id: a for a in arts}
        heads: Dict[str, Tuple[int, str]] = {}
        if art_by_id:
            # head = 最大 revision_no 的 content_sha256（逐条版 desc().limit(1)
            # 的集合等价）；空 sha 回退 artifact.content_fingerprint 同逐条版。
            for aid, rev_no, sha in db.execute(
                select(
                    ArtifactRevision.artifact_id,
                    ArtifactRevision.revision_no,
                    ArtifactRevision.content_sha256,
                ).where(ArtifactRevision.artifact_id.in_(list(art_by_id)))
            ).all():
                cur = heads.get(aid)
                if cur is None or rev_no > cur[0]:
                    heads[aid] = (rev_no, sha)
        for aid, art in art_by_id.items():
            head = heads.get(aid)
            out[(AS_ARTIFACT, aid)] = str(
                (head[1] if head is not None else None)
                or art.content_fingerprint or ""
            )

    if AS_MAP_PRODUCT in by_store:
        # authority_id 编码 ``mp:<project_id>:<version_no>``；跨项目/畸形 →
        # None（fail-closed，不查询）。(project_id, version_no) 有唯一约束，
        # 一次 IN 查询即可回填全部探针。
        wanted: Dict[int, List[str]] = {}
        for aid in by_store[AS_MAP_PRODUCT]:
            key = _mp_key(aid)
            if key is not None and key[0] == project_id:
                wanted.setdefault(key[1], []).append(aid)
        if wanted:
            fp_by_no = {
                no: fp
                for no, fp in db.execute(
                    select(
                        MapProductVersion.version_no,
                        MapProductVersion.product_fingerprint,
                    ).where(
                        MapProductVersion.project_id == project_id,
                        MapProductVersion.version_no.in_(list(wanted)),
                    )
                ).all()
            }
            for no, aids in wanted.items():
                fp = fp_by_no.get(no)
                token = None if fp is None else str(fp or "")
                for aid in aids:
                    out[(AS_MAP_PRODUCT, aid)] = token

    if AS_WORKFLOW in by_store:
        wfs = db.execute(
            select(Workflow).where(
                Workflow.id.in_(by_store[AS_WORKFLOW]),
                Workflow.project_id == project_id,
            )
        ).scalars().all()
        rev_ids = [w.current_revision_id for w in wfs if w.current_revision_id]
        fp_by_rev: Dict[str, Optional[str]] = {}
        if rev_ids:
            fp_by_rev = {
                rid: fp
                for rid, fp in db.execute(
                    select(
                        WorkflowRevision.id,
                        WorkflowRevision.graph_fingerprint,
                    ).where(WorkflowRevision.id.in_(rev_ids))
                ).all()
            }
        for w in wfs:
            out[(AS_WORKFLOW, w.id)] = (
                "" if not w.current_revision_id
                else str(fp_by_rev.get(w.current_revision_id) or "")
            )

    if AS_MISSION in by_store:
        rows = db.execute(
            select(GISMissionRow).where(
                GISMissionRow.mission_id.in_(by_store[AS_MISSION]),
                GISMissionRow.project_id == project_id,
            )
        ).scalars().all()
        for row in rows:
            out[(AS_MISSION, row.mission_id)] = str(row.revision or 1)

    if AS_GIS_MEMORY in by_store:
        from app.services.gis_memory.store import _now_naive

        now = _now_naive()
        # 与逐条版同一作用域口径（无 project_id 列 → scope='project' ∧
        # scope_id=project_id；见上方 AS_GIS_MEMORY 分支注释）：批量查询
        # 一次 IN 取回本项目的 project 作用域记忆，其余行按 fail-closed 留
        # None（跨项目/跨 scope 的 id 碰撞不得续命别人的权威行）。
        rows = db.execute(
            select(GISSpatialMemory).where(
                GISSpatialMemory.id.in_(by_store[AS_GIS_MEMORY]),
                GISSpatialMemory.scope == "project",
                GISSpatialMemory.scope_id == project_id,
            )
        ).scalars().all()
        for row in rows:
            if row.status != "active":
                continue
            if row.expires_at is not None and row.expires_at <= now:
                continue
            out[(AS_GIS_MEMORY, row.id)] = str(row.fingerprint or "")

    if AS_CARTO_FACT in by_store:
        rows = db.execute(
            select(CartoProjectFact).where(
                CartoProjectFact.id.in_(by_store[AS_CARTO_FACT]),
                CartoProjectFact.project_id == project_id,
            )
        ).scalars().all()
        for row in rows:
            if row.status not in ("active", "conflicted"):
                continue
            out[(AS_CARTO_FACT, row.id)] = str(row.fingerprint or "")

    return out


__all__ = ["live_version_token", "live_version_tokens"]
