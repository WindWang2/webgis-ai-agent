"""ProjectKnowledge 索引器：从权威源重建项目知识投影。

纪律：
- **只读权威表**（project_datasets / artifacts+lineages+revisions /
  map_products / workflows / gis_missions / gis_spatial_memories /
  carto_project_facts），唯一的写面是本投影表（store.upsert_entry）；
- 有界采样（missions ≤32、map products ≤8、每个权威源 LIMIT 明确）；
- 幂等：同 token 的 upsert = 原行刷新，rebuild 两遍 active 集合不变；
- 身份全借：每行 authority_store/authority_id/version_token 回指权威；
- 摘要 ref-first 无 payload；bbox/temporal/method 只取权威源显式携带的值
  （缺失即 NULL —— 绝不从 created_at 猜测时间或编造范围）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session

from app.models.project import (
    Artifact,
    ArtifactLineage,
    CartoProjectFact,
    MapProductVersion,
    Project,
    ProjectDataset,
    Workflow,
)
from app.models.mission import GISMissionRow
from app.models.spatial_memory import GISSpatialMemory
from app.services.project_knowledge import store as ks
from app.services.project_knowledge.liveness import live_version_token, live_version_tokens
from app.services.project_knowledge.contract import (
    AS_ARTIFACT,
    AS_CARTO_FACT,
    AS_GIS_MEMORY,
    AS_MAP_PRODUCT,
    AS_MISSION,
    AS_PROJECT_DATASET,
    AS_WORKFLOW,
    EK_ARTIFACT,
    EK_DATASET_VERSION,
    EK_FAILURE_PATTERN,
    EK_MAP_PRODUCT,
    EK_MISSION,
    EK_PLACE,
    EK_PREFERENCE,
    EK_WORKFLOW,
    REBUILD_MAP_PRODUCT_MAX,
    REBUILD_MISSION_MAX,
    REBUILD_DATASET_MAX, REBUILD_ARTIFACT_MAX, REBUILD_WORKFLOW_MAX,
    REBUILD_PLACE_MAX, REBUILD_PREFERENCE_MAX,
    RefTag,
    KnowledgeUpsert,
    validate_bbox,
)

logger = logging.getLogger(__name__)

_MISSION_TERMINAL_FAILED = "failed"
_MEM_PLACE_KIND = "resolved_place"
_CARTO_PREFERENCE_KIND = "preference"


@dataclass
class RebuildReport:
    """一次 rebuild 的计数（ref-only，可观测，无 payload）。"""

    org_id: str = ""
    project_id: str = ""
    upserted: Dict[str, int] = field(default_factory=dict)
    rejected: int = 0
    errors: int = 0
    failed_sources: List[str] = field(default_factory=list)
    entry_ids: set[str] = field(default_factory=set, repr=False)

    def bump(self, kind: str, entry_id: str) -> None:
        self.upserted[kind] = self.upserted.get(kind, 0) + 1
        self.entry_ids.add(entry_id)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "org_id": self.org_id,
            "project_id": self.project_id,
            "upserted": dict(self.upserted),
            "rejected": self.rejected,
            "errors": self.errors,
            "failed_sources": list(self.failed_sources),
        }


def _dataset_summary(row: ProjectDataset) -> str:
    return f"{row.name}（{row.source_type}，质量 {row.quality_status}）"


def _artifact_summary(row: Artifact) -> str:
    fmt = f"，{row.format}" if row.format else ""
    return f"{row.name}（{row.artifact_type}{fmt}）"


def index_datasets(
    db: Session, *, project_id: str, org_id: str, report: RebuildReport
) -> None:
    rows = list(db.execute(
        select(ProjectDataset)
        .where(
            ProjectDataset.project_id == project_id,
            ProjectDataset.detached_at.is_(None),
        )
        .order_by(ProjectDataset.created_at.desc())
        .limit(REBUILD_DATASET_MAX)
    ).scalars().all())
    for row in rows:
        entry = ks.upsert_entry(db, KnowledgeUpsert(
            org_id=org_id,
            project_id=project_id,
            entity_kind=EK_DATASET_VERSION,
            authority_store=AS_PROJECT_DATASET,
            authority_id=str(row.id),
            subject=str(row.name or "")[:255],
            version_token=str(row.version_fingerprint or ""),
            summary=_dataset_summary(row),
        ), apply_budget=False)
        if entry is None:
            report.rejected += 1
        else:
            report.bump(EK_DATASET_VERSION, str(entry.id))


def _lineage_refs(
    db: Session, *, artifact_id: str, project_id: str,
    lineage_rows: Optional[List[Any]] = None,
    tokens: Optional[Dict[Any, Any]] = None,
) -> tuple[List[RefTag], Optional[str], Optional[Any]]:
    """artifact 的血缘投影：derived_from 数据集/父产物 + 方法键。"""
    rows = lineage_rows if lineage_rows is not None else list(db.execute(
        select(ArtifactLineage)
        .where(ArtifactLineage.artifact_id == artifact_id)
        .order_by(ArtifactLineage.created_at.desc())
        .limit(8)
    ).scalars().all())
    refs: List[RefTag] = []
    parent_tokens = tokens if tokens is not None else live_version_tokens(db, project_id=project_id, probes=[
        (AS_ARTIFACT, str(edge.parent_artifact_id)) for edge in rows if edge.parent_artifact_id
    ])
    method_key: Optional[str] = None
    bbox: Optional[Any] = None
    for edge in rows:
        if edge.source_dataset_id:
            refs.append(RefTag(
                relation="derived_from",
                authority=AS_PROJECT_DATASET,
                id=str(edge.source_dataset_id)[:128],
                token=str(edge.source_dataset_fingerprint or "")[:128],
            ))
        if edge.parent_artifact_id:
            refs.append(RefTag(
                relation="derived_from",
                authority=AS_ARTIFACT,
                id=str(edge.parent_artifact_id)[:128],
                token=str(parent_tokens.get((AS_ARTIFACT, str(edge.parent_artifact_id))) or "")[:128],
            ))
        if method_key is None and (edge.producing_capability or edge.producing_algorithm):
            cap = str(edge.producing_capability or "").strip()
            alg = str(edge.producing_algorithm or "").strip()
            method_key = f"{cap}:{alg}"[:200]
        if bbox is None and isinstance(edge.repair_evidence, dict):
            bbox = edge.repair_evidence.get("bbox")
    return refs, method_key, bbox


def index_artifacts(
    db: Session, *, project_id: str, org_id: str, report: RebuildReport
) -> None:
    rows = list(db.execute(
        select(Artifact)
        .where(Artifact.project_id == project_id)
        .order_by(Artifact.created_at.desc())
        .limit(REBUILD_ARTIFACT_MAX)
    ).scalars().all())
    artifact_ids = [str(row.id) for row in rows]
    ranked = select(
        ArtifactLineage.id.label("lineage_id"),
        func.row_number().over(partition_by=ArtifactLineage.artifact_id,
                               order_by=ArtifactLineage.created_at.desc()).label("rank"),
    ).where(ArtifactLineage.artifact_id.in_(artifact_ids)).subquery()
    lineage_by_artifact: Dict[str, List[Any]] = {}
    for edge in db.execute(select(ArtifactLineage).join(
        ranked, ranked.c.lineage_id == ArtifactLineage.id,
    ).where(ranked.c.rank <= 8).order_by(ArtifactLineage.created_at.desc())).scalars():
        lineage_by_artifact.setdefault(str(edge.artifact_id), []).append(edge)
    probes = [(AS_ARTIFACT, aid) for aid in artifact_ids]
    probes.extend((AS_ARTIFACT, str(edge.parent_artifact_id))
                  for edges in lineage_by_artifact.values() for edge in edges
                  if edge.parent_artifact_id)
    tokens = live_version_tokens(db, project_id=project_id, probes=probes)
    for row in rows:
        meta = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        lineage_refs, method_key, lineage_bbox = _lineage_refs(
            db, artifact_id=str(row.id), project_id=project_id,
            lineage_rows=lineage_by_artifact.get(str(row.id), []), tokens=tokens,
        )
        bbox = validate_bbox(meta.get("bbox")) or validate_bbox(lineage_bbox)
        token = tokens.get((AS_ARTIFACT, str(row.id)))
        entry = ks.upsert_entry(db, KnowledgeUpsert(
            org_id=org_id,
            project_id=project_id,
            entity_kind=EK_ARTIFACT,
            authority_store=AS_ARTIFACT,
            authority_id=str(row.id),
            subject=str(row.name or "")[:255],
            version_token=str(token or ""),
            summary=_artifact_summary(row),
            bbox=list(bbox) if bbox else None,
            temporal_label=(
                str(meta.get("temporal_label"))[:64]
                if meta.get("temporal_label") else None
            ),
            method_key=method_key,
            refs=lineage_refs,
        ), apply_budget=False)
        if entry is None:
            report.rejected += 1
        else:
            report.bump(EK_ARTIFACT, str(entry.id))


def index_map_products(
    db: Session, *, project_id: str, org_id: str, report: RebuildReport
) -> None:
    rows = list(db.execute(
        select(MapProductVersion)
        .where(MapProductVersion.project_id == project_id)
        .order_by(MapProductVersion.version_no.desc())
        .limit(REBUILD_MAP_PRODUCT_MAX)
    ).scalars().all())
    for row in rows:
        artifact_refs = [
            RefTag(
                relation="produced",
                authority=AS_ARTIFACT,
                id=str(aid)[:128],
            )
            for aid in (row.artifact_ids or [])[:4]
            if isinstance(aid, str)
        ]
        entry = ks.upsert_entry(db, KnowledgeUpsert(
            org_id=org_id,
            project_id=project_id,
            entity_kind=EK_MAP_PRODUCT,
            authority_store=AS_MAP_PRODUCT,
            authority_id=f"mp:{project_id}:{int(row.version_no)}",
            subject=f"地图成品 v{int(row.version_no)}"
            + (f"（{row.label}）" if row.label else ""),
            version_token=str(row.product_fingerprint or ""),
            summary=f"地图成品版本 {int(row.version_no)}"
            + (f"，谱系 {row.lineage_kind}" if row.lineage_kind else ""),
            refs=artifact_refs,
        ), apply_budget=False)
        if entry is None:
            report.rejected += 1
        else:
            report.bump(EK_MAP_PRODUCT, str(entry.id))


def index_workflows(
    db: Session, *, project_id: str, org_id: str, report: RebuildReport
) -> None:
    rows = list(db.execute(
        select(Workflow)
        .where(Workflow.project_id == project_id)
        .order_by(Workflow.updated_at.desc())
        .limit(REBUILD_WORKFLOW_MAX)
    ).scalars().all())
    for row in rows:
        token = live_version_token(
            db, project_id=project_id,
            authority_store=AS_WORKFLOW, authority_id=str(row.id),
        )
        entry = ks.upsert_entry(db, KnowledgeUpsert(
            org_id=org_id,
            project_id=project_id,
            entity_kind=EK_WORKFLOW,
            authority_store=AS_WORKFLOW,
            authority_id=str(row.id),
            subject=str(row.name or "")[:255],
            version_token=str(token or ""),
            summary=f"工作流 v{int(row.version or 1)}",
        ), apply_budget=False)
        if entry is None:
            report.rejected += 1
        else:
            report.bump(EK_WORKFLOW, str(entry.id))


def index_missions(
    db: Session, *, project_id: str, org_id: str, report: RebuildReport
) -> None:
    rows = list(db.execute(
        select(GISMissionRow)
        .where(
            GISMissionRow.org_id == str(org_id),
            GISMissionRow.project_id == project_id,
        )
        .order_by(GISMissionRow.created_at.desc())
        .limit(REBUILD_MISSION_MAX)
    ).scalars().all())
    for row in rows:
        refs = row.refs if isinstance(row.refs, dict) else {}
        ref_tags: List[RefTag] = []
        for aid in (refs.get("artifact_refs") or [])[:4]:
            ref_tags.append(RefTag(
                relation="produced", authority=AS_ARTIFACT, id=str(aid)[:128],
            ))
        for mid in (refs.get("map_product_refs") or [])[:2]:
            ref_tags.append(RefTag(
                relation="produced", authority=AS_MAP_PRODUCT, id=str(mid)[:128],
            ))
        for cid in (refs.get("evidence_refs") or [])[:2]:
            ref_tags.append(RefTag(
                relation="verified_by", authority="session_ref", id=str(cid)[:128],
            ))
        entry = ks.upsert_entry(db, KnowledgeUpsert(
            org_id=org_id,
            project_id=project_id,
            entity_kind=EK_MISSION,
            authority_store=AS_MISSION,
            authority_id=str(row.mission_id),
            subject=str(row.root_goal or "")[:255] or str(row.mission_id),
            version_token=str(row.revision or 1),
            summary=f"Mission {row.state}",
            refs=ref_tags,
        ), apply_budget=False)
        if entry is None:
            report.rejected += 1
        else:
            report.bump(EK_MISSION, str(entry.id))

        if row.state == _MISSION_TERMINAL_FAILED:
            failure = row.failure_state if isinstance(row.failure_state, dict) else {}
            error_code = str(failure.get("error_code") or "unknown_error")[:255]
            fp_entry = ks.upsert_entry(db, KnowledgeUpsert(
                org_id=org_id,
                project_id=project_id,
                entity_kind=EK_FAILURE_PATTERN,
                authority_store=AS_MISSION,
                authority_id=str(row.mission_id),
                subject=error_code,
                version_token=str(row.revision or 1),
                summary=str(failure.get("detail") or row.root_goal or "")[:300],
                refs=[RefTag(
                    relation="failed_with", authority=AS_MISSION,
                    id=str(row.mission_id)[:128],
                )],
            ), apply_budget=False)
            if fp_entry is None:
                report.rejected += 1
            else:
                report.bump(EK_FAILURE_PATTERN, str(fp_entry.id))


def index_places(
    db: Session, *, project_id: str, org_id: str, report: RebuildReport
) -> None:
    """project 作用域的 resolved_place 记忆 → place 实体（敏感行绝不投影）。"""
    rows = list(db.execute(
        select(GISSpatialMemory)
        .where(
            GISSpatialMemory.org_id == str(org_id),
            GISSpatialMemory.scope == "project",
            GISSpatialMemory.scope_id == project_id,
            GISSpatialMemory.kind == _MEM_PLACE_KIND,
            GISSpatialMemory.status == "active",
            GISSpatialMemory.sensitive.is_(False),
            or_(GISSpatialMemory.expires_at.is_(None), GISSpatialMemory.expires_at > ks._now_naive()),
        )
        .order_by(GISSpatialMemory.last_validated_at.desc())
        .limit(REBUILD_PLACE_MAX)
    ).scalars().all())
    for row in rows:
        value = row.value if isinstance(row.value, dict) else {}
        bbox = validate_bbox(value.get("bbox"))
        entry = ks.upsert_entry(db, KnowledgeUpsert(
            org_id=org_id,
            project_id=project_id,
            entity_kind=EK_PLACE,
            authority_store=AS_GIS_MEMORY,
            authority_id=str(row.id),
            subject=str(row.subject or "")[:255],
            version_token=str(row.fingerprint or ""),
            summary=str(row.subject or "")[:300],
            bbox=list(bbox) if bbox else None,
        ), apply_budget=False)
        if entry is None:
            report.rejected += 1
        else:
            report.bump(EK_PLACE, str(entry.id))


def index_preferences(
    db: Session, *, project_id: str, org_id: str, report: RebuildReport
) -> None:
    """ADR-0069 制图偏好账本 → preference 实体（单一 preference 真相的投影）。"""
    rows = list(db.execute(
        select(CartoProjectFact)
        .where(
            CartoProjectFact.project_id == project_id,
            CartoProjectFact.kind == _CARTO_PREFERENCE_KIND,
            CartoProjectFact.status == "active",
            or_(CartoProjectFact.expires_at.is_(None), CartoProjectFact.expires_at > ks._now_naive()),
        )
        .order_by(CartoProjectFact.last_verified_at.desc())
        .limit(REBUILD_PREFERENCE_MAX)
    ).scalars().all())
    for row in rows:
        entry = ks.upsert_entry(db, KnowledgeUpsert(
            org_id=org_id,
            project_id=project_id,
            entity_kind=EK_PREFERENCE,
            authority_store=AS_CARTO_FACT,
            authority_id=str(row.id),
            subject=str(row.subject or "")[:255],
            version_token=str(row.fingerprint or ""),
            summary=str(row.subject or "")[:300],
        ), apply_budget=False)
        if entry is None:
            report.rejected += 1
        else:
            report.bump(EK_PREFERENCE, str(entry.id))


def rebuild_project_knowledge(
    db: Session, *, project: Project, org_id: str
) -> RebuildReport:
    """幂等重建项目知识投影（caller commit）。

    org_id 是**调用方经项目 auth 门确认过的 effective org 字符串**；
    本函数只读权威表 + 写投影表。单源失败只豁免该源（per-source
    savepoint，review S4g-003），其余源照常完成。
    """
    project_id = str(project.id)
    report = RebuildReport(org_id=org_id, project_id=project_id)
    sources = (
        ("datasets", index_datasets, (EK_DATASET_VERSION,)),
        ("artifacts", index_artifacts, (EK_ARTIFACT,)),
        ("map_products", index_map_products, (EK_MAP_PRODUCT,)),
        ("workflows", index_workflows, (EK_WORKFLOW,)),
        ("missions", index_missions, (EK_MISSION, EK_FAILURE_PATTERN)),
        ("places", index_places, (EK_PLACE,)),
        ("preferences", index_preferences, (EK_PREFERENCE,)),
    )
    for name, fn, kinds in sources:
        source_report = RebuildReport(org_id=org_id, project_id=project_id)
        try:
            with db.begin_nested():
                fn(db, project_id=project_id, org_id=org_id, report=source_report)
                ks.invalidate_rebuild_window(
                    db, org_id=org_id, project_id=project_id, kinds=kinds,
                    keep_ids=list(source_report.entry_ids),
                )
            for kind, count in source_report.upserted.items():
                report.upserted[kind] = report.upserted.get(kind, 0) + count
            report.rejected += source_report.rejected
            report.entry_ids.update(source_report.entry_ids)
        except Exception:  # noqa: BLE001 — 单源失败不中断其余源
            report.errors += 1
            report.failed_sources.append(name)
            logger.exception(
                "[ProjectKnowledge] rebuild source=%s failed project=%s",
                name, project_id,
            )
    # Apply the global cap once after coherent source windows have replaced
    # their old rows; never evict a row just refreshed by this rebuild.
    ks._apply_budget(db, org_id, project_id, protected_ids=list(report.entry_ids))
    return report


__all__ = ["RebuildReport", "rebuild_project_knowledge"]
