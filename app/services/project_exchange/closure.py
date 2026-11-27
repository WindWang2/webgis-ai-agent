"""项目依赖闭包读取器（WP1）——从既有权威表读取，零写入、零平行库。

权威来源（只读）：
- Project / ProjectDataset（app/models/project.py:14/42）
- Workflow + WorkflowRevision（不可变修订，graph_fingerprint）
- Artifact + ArtifactRevision（内容账本）+ ArtifactLineage（血缘 DAG）
- MapProductVersion（mapspec_snapshot / product_fingerprint）

分类纪律：引用绝不冒充已打包文件 —— upload/会话 ref/产物 blob 可 embed，
external URL / layer / 未知类型按 external_reference / unsupported / missing
诚实分类。闭包只回答"有什么、什么可带、什么不能带"；真正取载荷在
materialize 阶段（payloads.py）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from app.models.project import (
    Artifact, ArtifactLineage, ArtifactRevision, MapProductVersion,
    Project, ProjectDataset, Workflow, WorkflowRevision,
)
from app.services.project_exchange import contracts as pc

logger = logging.getLogger(__name__)

#: 会话 ref 前缀（ProjectDataset.source_ref 指向会话 store 载荷时的形态）。
_SESSION_REF_PREFIX = "ref:"


@dataclass
class DatasetNode:
    dataset_id: str
    name: str
    source_type: str
    source_ref: str
    crs: str
    schema_profile: Dict[str, Any]
    version_fingerprint: str
    detached: bool
    classification: str
    embeddable: bool
    estimated_bytes: Optional[int] = None
    license: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    geometry_type: str = ""
    feature_count: Optional[int] = None
    bbox: Optional[List[float]] = None
    upload_id: Optional[int] = None
    upload_path: Optional[str] = None


@dataclass
class WorkflowNode:
    workflow_id: str
    name: str
    version: int
    graph_spec: Dict[str, Any]
    graph_fingerprint: str
    inputs_schema: Dict[str, Any]
    input_dataset_ids: Set[str] = field(default_factory=set)
    input_source_refs: Set[str] = field(default_factory=set)


@dataclass
class ArtifactNode:
    artifact_id: str
    name: str
    artifact_type: str
    format: str
    crs: Optional[str]
    content_fingerprint: str
    revision_no: Optional[int]
    content_sha256: Optional[str]
    content_location: Optional[str]
    content_type: Optional[str]
    byte_size: int
    parent_ids: Set[str] = field(default_factory=set)
    producing_tool: str = ""
    tool_version: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    workflow_id: Optional[str] = None
    source_dataset_id: Optional[str] = None


@dataclass
class MapNode:
    map_product_id: str
    name: str
    version_no: int
    mapspec_snapshot: Dict[str, Any]
    mapspec_fingerprint: Optional[str]
    product_fingerprint: Optional[str]
    workflow_id: Optional[str]
    artifact_ids: List[str]
    label: str = ""


@dataclass
class ProjectClosure:
    """一次授权读取得到的完整闭包（inspect/plan/materialize 共用）。"""

    project_id: str
    project_name: str
    description: str
    metadata_json: Dict[str, Any]
    datasets: Dict[str, DatasetNode] = field(default_factory=dict)
    workflows: Dict[str, WorkflowNode] = field(default_factory=dict)
    artifacts: Dict[str, ArtifactNode] = field(default_factory=dict)
    maps: Dict[str, MapNode] = field(default_factory=dict)
    disclosure: List[Dict[str, Any]] = field(default_factory=list)
    selection: Dict[str, Any] = field(default_factory=dict)
    truncated: Dict[str, bool] = field(default_factory=dict)

    def classification_counts(self) -> Dict[str, int]:
        counts = {c: 0 for c in pc.CLASSIFICATIONS}
        counts["session_ref"] = 0  # 计划期暂态：导出时刻解析为 embedded/missing
        for node in self.datasets.values():
            counts[node.classification] += 1
        return counts

    def embedded_dataset_count(self) -> int:
        return self.classification_counts()[pc.CLASS_EMBEDDED]

    def repro_level(self) -> str:
        counts = self.classification_counts()
        embedded = counts[pc.CLASS_EMBEDDED]
        workflows = list(self.workflows.values())
        runnable = sum(1 for wf in workflows
                       if self._workflow_runnable(wf))
        if workflows and runnable == len(workflows) and embedded:
            return pc.REPRO_FULL_OFFLINE
        if embedded and workflows:
            return pc.REPRO_PARTIAL
        if embedded:
            return pc.REPRO_VIEW_ONLY
        return pc.REPRO_PARTIAL

    def _workflow_runnable(self, wf: WorkflowNode) -> bool:
        """工作流可运行 = 其绑定的数据依赖全部 embedded（保守判定）。"""
        if wf.input_dataset_ids or wf.input_source_refs:
            for ds_id in wf.input_dataset_ids:
                node = self.datasets.get(ds_id)
                if node is None or node.classification != pc.CLASS_EMBEDDED:
                    return False
            for ref in wf.input_source_refs:
                node = self._dataset_by_source_ref(ref)
                if node is None or node.classification != pc.CLASS_EMBEDDED:
                    return False
            return True
        # 无数据绑定的工作流（纯计算/常量输入）：图存在即可运行
        return bool(wf.graph_spec)

    def _dataset_by_source_ref(self, ref: str) -> Optional[DatasetNode]:
        for node in self.datasets.values():
            if node.source_ref and node.source_ref == ref:
                return node
        return None


def _dataset_classification(row: ProjectDataset) -> tuple[str, bool, str, Optional[int], Optional[str]]:
    """单数据集分类（只读 DB/stat，不取载荷）。

    Returns (classification, embeddable, reason, upload_id, upload_path)。
    """
    source_type = (row.source_type or "").strip()
    source_ref = (row.source_ref or "").strip()
    if row.detached_at is not None:
        return (pc.CLASS_MISSING, False, "数据集已从项目摘除（tombstone）", None, None)
    if source_type == "upload":
        text = source_ref.strip()
        if text.isdigit():
            from app.services.data_parser import get_upload_dir

            upload_path = get_upload_dir(_data_dir(), text) / _upload_filename(text)
            if upload_path.is_file():
                return (pc.CLASS_EMBEDDED, True, "", int(text), str(upload_path))
            return (pc.CLASS_MISSING, False,
                    f"上传记录文件缺失（uploads/{text}）", int(text), None)
        return (pc.CLASS_MISSING, False, "上传引用形态非法", None, None)
    if source_type in ("vector", "raster", "table") and source_ref.startswith(_SESSION_REF_PREFIX):
        # 会话绑定载荷：物化阶段在会话上下文内解析；计划阶段诚实标注
        # provisional（嵌入与否以导出时刻的真实解析为准）。
        return ("session_ref", True, "会话引用（导出时解析）", None, None)
    if source_type == "external":
        return (pc.CLASS_EXTERNAL_REFERENCE, False,
                f"外部源不随包携带: {source_ref[:120]}", None, None)
    if source_type == "layer":
        return (pc.CLASS_UNSUPPORTED, False,
                "layer 类型数据集不在工程包物化范围（v1 披露）", None, None)
    return (pc.CLASS_UNSUPPORTED, False,
            f"未知 source_type: {source_type[:40]}", None, None)


def _upload_filename(upload_id: str) -> str:
    from pathlib import Path

    base = Path(_data_dir()) / "uploads" / upload_id
    for name in ("original.geojson", "original.tif", "original.csv"):
        if (base / name).is_file():
            return name
    return "original.geojson"


def _data_dir() -> str:
    from app.core.config import settings

    return str(settings.DATA_DIR)


def _bounded_json(value: Any, limit: int = 256 * 1024) -> Dict[str, Any]:
    """图/快照 JSON 载荷守卫：超界截为披露形态，绝不整包携带无界文档。"""
    import json

    if not isinstance(value, dict):
        return {}
    try:
        blob = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {}
    if len(blob.encode("utf-8")) <= limit:
        return value
    return {"__truncated__": True,
            "reason": f"文档超过闭包读取界限 {limit}B（不打包，详见披露）"}


def read_project_closure(
    db: Session,
    project_id: str,
    *,
    selection: Optional[Dict[str, Any]] = None,
    max_items: int = pc.MAX_LOGICAL_IDS,
) -> ProjectClosure:
    """读取项目闭包（零写入）。权限校验是调用方责任（ProjectService 门后调用）。

    selection: ``{"kind": "project"}`` 或
    ``{"kind": "map", "map_product_ids": [...]}`` 或
    ``{"kind": "analysis", "workflow_ids": [...]}``。
    """
    project = db.execute(
        sa_select(Project).where(Project.id == project_id)
    ).scalar_one_or_none()
    if project is None:
        raise pc.PackageError(pc.ERR_PROJECT_NOT_FOUND, "项目不存在")
    closure = ProjectClosure(
        project_id=project.id,
        project_name=project.name or "",
        description=project.description or "",
        metadata_json=dict(project.metadata_json or {}),
        selection=dict(selection or {"kind": pc.SCOPE_PROJECT}),
    )

    sel_kind = (closure.selection.get("kind") or pc.SCOPE_PROJECT).strip()
    map_ids = set(str(x) for x in (closure.selection.get("map_product_ids") or []))
    wf_ids = set(str(x) for x in (closure.selection.get("workflow_ids") or []))

    # ── maps ─────────────────────────────────────────────────────────────
    map_rows = db.execute(
        sa_select(MapProductVersion)
        .where(MapProductVersion.project_id == project_id)
        .order_by(MapProductVersion.version_no.desc())
        .limit(max_items + 1)
    ).scalars().all()
    if len(map_rows) > max_items:
        closure.truncated["maps"] = True
        map_rows = map_rows[:max_items]
    if sel_kind == pc.SCOPE_MAP:
        map_rows = [m for m in map_rows if str(m.id) in map_ids] if map_ids else []
    for row in map_rows:
        closure.maps[str(row.id)] = MapNode(
            map_product_id=str(row.id),
            name=str(row.label or f"map-v{row.version_no}"),
            version_no=int(row.version_no or 1),
            mapspec_snapshot=_bounded_json(row.mapspec_snapshot),
            mapspec_fingerprint=row.mapspec_fingerprint,
            product_fingerprint=row.product_fingerprint,
            workflow_id=str(row.workflow_id) if row.workflow_id else None,
            artifact_ids=[str(a) for a in (row.artifact_ids or [])][:64],
            label=str(row.label or ""),
        )

    # ── workflows ────────────────────────────────────────────────────────
    wf_rows = db.execute(
        sa_select(Workflow)
        .where(Workflow.project_id == project_id)
        .order_by(Workflow.updated_at.desc())
        .limit(max_items + 1)
    ).scalars().all()
    if len(wf_rows) > max_items:
        closure.truncated["workflows"] = True
        wf_rows = wf_rows[:max_items]
    if sel_kind == pc.SCOPE_ANALYSIS:
        wf_rows = [w for w in wf_rows if str(w.id) in wf_ids] if wf_ids else []
    revision_by_workflow = _current_revisions(db, [str(w.id) for w in wf_rows])
    for row in wf_rows:
        revision = revision_by_workflow.get(str(row.id))
        graph = _bounded_json(revision.graph_spec if revision else None)
        node = WorkflowNode(
            workflow_id=str(row.id),
            name=row.name or "",
            version=int(row.version or 1),
            graph_spec=graph,
            graph_fingerprint=str(revision.graph_fingerprint or "") if revision else "",
            inputs_schema=dict(row.inputs_schema or {}),
        )
        for binding in (graph.get("input_bindings") or {}).values() if isinstance(graph, dict) else []:
            _absorb_binding(node, binding)
        for step in (graph.get("steps") or []):
            if isinstance(step, dict):
                for binding in (step.get("input_bindings") or {}).values():
                    _absorb_binding(node, binding)
        closure.workflows[node.workflow_id] = node

    # ── artifacts（范围：map 引用的 + workflow 产生的 + 全量上限截断）─────
    art_rows = db.execute(
        sa_select(Artifact)
        .where(Artifact.project_id == project_id)
        .order_by(Artifact.created_at.desc())
        .limit(max_items + 1)
    ).scalars().all()
    if len(art_rows) > max_items:
        closure.truncated["artifacts"] = True
        art_rows = art_rows[:max_items]
    art_ids_in_scope = {str(a.id) for a in art_rows}
    wanted_art_ids: Set[str] = set()
    for m in closure.maps.values():
        wanted_art_ids.update(m.artifact_ids)
    # analysis/map 范围下 artifact 只带被引用者；project 范围全量（上限内）。
    if sel_kind != pc.SCOPE_PROJECT:
        art_rows = [a for a in art_rows if str(a.id) in wanted_art_ids]
    revision_index = _head_revisions(db, [str(a.id) for a in art_rows])
    lineage_index = _lineage_index(db, [str(a.id) for a in art_rows])
    for row in art_rows:
        head = revision_index.get(str(row.id))
        lin = lineage_index.get(str(row.id))
        node = ArtifactNode(
            artifact_id=str(row.id),
            name=row.name or "",
            artifact_type=row.artifact_type or "",
            format=row.format or "",
            crs=row.crs,
            content_fingerprint=str(row.content_fingerprint or ""),
            revision_no=head.revision_no if head else None,
            content_sha256=head.content_sha256 if head else None,
            content_location=head.content_location if head else None,
            content_type=head.content_type if head else None,
            byte_size=int(head.byte_size or 0) if head else 0,
            producing_tool=str(lin.get("producing_tool") or "") if lin else "",
            tool_version=str(lin.get("tool_version") or "") if lin else "",
            parameters=dict(lin.get("parameters") or {}) if lin else {},
            workflow_id=str(lin.get("workflow_id")) if lin and lin.get("workflow_id") else None,
            source_dataset_id=str(lin.get("source_dataset_id")) if lin and lin.get("source_dataset_id") else None,
        )
        for parent in (lin.get("parents") or []) if lin else []:
            pid = str(parent)
            if pid in art_ids_in_scope:
                node.parent_ids.add(pid)
        closure.artifacts[node.artifact_id] = node

    # ── datasets ─────────────────────────────────────────────────────────
    ds_rows = db.execute(
        sa_select(ProjectDataset)
        .where(ProjectDataset.project_id == project_id)
        .order_by(ProjectDataset.created_at.desc())
        .limit(max_items + 1)
    ).scalars().all()
    if len(ds_rows) > max_items:
        closure.truncated["datasets"] = True
        ds_rows = ds_rows[:max_items]
    # 范围收敛：map/analysis 范围只带被闭包引用的数据集
    wanted_ds: Set[str] = set()
    for wf in closure.workflows.values():
        wanted_ds.update(wf.input_dataset_ids)
        wanted_ds.update(
            _dataset_id_for_ref(db, project_id, ref)
            for ref in wf.input_source_refs
        )
    for art in closure.artifacts.values():
        if art.source_dataset_id:
            wanted_ds.add(art.source_dataset_id)
    for ds_id in list(wanted_ds):
        if ds_id and not ds_id.startswith("ds_"):
            wanted_ds.discard(ds_id)
    if sel_kind != pc.SCOPE_PROJECT:
        ds_rows = [d for d in ds_rows if str(d.id) in wanted_ds] if wanted_ds else []
    for row in ds_rows:
        classification, embeddable, reason, upload_id, upload_path = (
            _dataset_classification(row))
        profile = dict(row.schema_profile or {})
        node = DatasetNode(
            dataset_id=str(row.id),
            name=row.name or "",
            source_type=row.source_type or "",
            source_ref=row.source_ref or "",
            crs=row.crs or "",
            schema_profile=profile if len(pc.json_bytes(profile)) <= 64 * 1024 else {},
            version_fingerprint=str(row.version_fingerprint or ""),
            detached=row.detached_at is not None,
            classification=classification,
            embeddable=embeddable,
            license=dict(profile.get("license") or {}) if isinstance(
                profile.get("license"), dict) else {},
            reason=reason,
            geometry_type=str(profile.get("geometry_type") or ""),
            feature_count=profile.get("feature_count"),
            bbox=profile.get("bbox") if isinstance(
                profile.get("bbox"), list) and len(profile.get("bbox") or []) == 4 else None,
            upload_id=upload_id,
            upload_path=upload_path,
        )
        if node.upload_path:
            from pathlib import Path

            try:
                node.estimated_bytes = Path(node.upload_path).stat().st_size
            except OSError:
                node.estimated_bytes = None
        closure.datasets[node.dataset_id] = node

    _collect_disclosure(closure)
    return closure


def _absorb_binding(node: WorkflowNode, binding: Any) -> None:
    if not isinstance(binding, dict):
        return
    ds_id = binding.get("dataset_id") or binding.get("dataset")
    if isinstance(ds_id, str) and ds_id.startswith("ds_"):
        node.input_dataset_ids.add(ds_id)
    ref = binding.get("ref") or binding.get("source_ref")
    if isinstance(ref, str) and ref.startswith(_SESSION_REF_PREFIX):
        node.input_source_refs.add(ref)


def _collect_disclosure(closure: ProjectClosure) -> None:
    for node in closure.datasets.values():
        if node.classification in (pc.CLASS_MISSING, pc.CLASS_REDACTED,
                                   pc.CLASS_UNSUPPORTED):
            closure.disclosure.append({
                "kind": "dataset", "name": node.name or node.dataset_id,
                "reason": node.reason or node.classification,
            })
        elif node.classification == pc.CLASS_EXTERNAL_REFERENCE:
            closure.disclosure.append({
                "kind": "external_link", "name": node.name or node.dataset_id,
                "reason": node.reason or node.source_ref[:200],
            })


def _dataset_id_for_ref(db: Session, project_id: str, ref: str) -> str:
    row = db.execute(
        sa_select(ProjectDataset.id).where(
            ProjectDataset.project_id == project_id,
            ProjectDataset.source_ref == ref,
        )
    ).scalar_one_or_none()
    return str(row) if row else ""


def _current_revisions(db: Session, workflow_ids: List[str]) -> Dict[str, WorkflowRevision]:
    if not workflow_ids:
        return {}
    out: Dict[str, WorkflowRevision] = {}
    rows = db.execute(
        sa_select(Workflow).where(Workflow.id.in_(workflow_ids[:pc.MAX_LOGICAL_IDS]))
    ).scalars().all()
    ids = {str(w.current_revision_id) for w in rows if w.current_revision_id}
    if not ids:
        return out
    for rev in db.execute(
        sa_select(WorkflowRevision).where(WorkflowRevision.id.in_(ids))
    ).scalars().all():
        for w in rows:
            if w.current_revision_id == rev.id:
                out[str(w.id)] = rev
    return out


def _head_revisions(db: Session, artifact_ids: List[str]) -> Dict[str, ArtifactRevision]:
    if not artifact_ids:
        return {}
    rows = db.execute(
        sa_select(ArtifactRevision)
        .where(ArtifactRevision.artifact_id.in_(artifact_ids[:pc.MAX_LOGICAL_IDS]))
        .order_by(ArtifactRevision.artifact_id, ArtifactRevision.revision_no.desc())
    ).scalars().all()
    out: Dict[str, ArtifactRevision] = {}
    for rev in rows:
        out.setdefault(str(rev.artifact_id), rev)
    return out


def _lineage_index(db: Session, artifact_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not artifact_ids:
        return {}
    rows = db.execute(
        sa_select(ArtifactLineage)
        .where(ArtifactLineage.artifact_id.in_(artifact_ids[:pc.MAX_LOGICAL_IDS]))
    ).scalars().all()
    out: Dict[str, Dict[str, Any]] = {}
    for lin in rows:
        out[str(lin.artifact_id)] = {
            "parents": [str(p) for p in ([lin.parent_artifact_id] if lin.parent_artifact_id else [])],
            "producing_tool": lin.producing_tool,
            "tool_version": lin.tool_version,
            "parameters": dict(lin.parameters or {}),
            "workflow_id": lin.workflow_run_id,  # 运行级溯源经 run；此处保守记录
            "source_dataset_id": lin.source_dataset_id,
        }
    return out
