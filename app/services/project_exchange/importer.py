"""工程包导入编排（WP3）—— 不可信包 → 受控解包 → 拓扑序注册 → 单事务回滚。

信任边界：
- 包来源是**受控上传 ID**（专用接收面，owner sidecar），不是任意磁盘路径；
- 静态防线与逐条目解压预算在 ``untrusted_zip``（slip/bomb/白名单/哈希）；
- 注册在**一个全新隔离项目**内单事务完成：任一步失败 → 事务回滚 +
  磁盘清理，原项目与其他项目零接触，任务中心可见失败终态；
- 不自动执行任何工作流；历史产物作为历史产物导入（不伪造 run）；
- 会话引用恢复是**提交后的尽力而为**（async 阶段），失败如实进恢复报告。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.project_exchange import contracts as pc
from app.services.project_exchange.untrusted_zip import (
    extract_zip, inspect_zip,
)

logger = logging.getLogger(__name__)

#: durable job task_type（与 exporter 的导入类型一致；任务中心可见）。
TASK_TYPE = "project_package_import"

_IMPORT_SEMAPHORE = asyncio.Semaphore(1)  # 导入注册串行（写路径 + 单事务）
_ACTIVE_IMPORT_RUNNERS: Dict[str, asyncio.Task] = {}

#: 受控上传 ID 形态。
_UPLOAD_ID_RE = re.compile(r"^pkgnz-[0-9a-f]{16}$")


@dataclass
class ImportMapping:
    """源逻辑 ID → 新系统 ID 映射（回执/恢复报告的事实源）。"""

    project_id: str = ""
    datasets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    workflows: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    artifacts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    maps: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    session_writebacks: List[Dict[str, Any]] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)


# ── 受控上传接收面 ───────────────────────────────────────────────────────

def uploads_root() -> Path:
    root = _packages_root() / "imports" / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_package_upload(actor, filename: str, tmp_path: Path, session_id: Optional[str]) -> Dict[str, Any]:
    """接收一个包文件（服务端已校验扩展名/大小），落盘 + owner sidecar。"""
    name = (filename or "").strip()
    if not name.lower().endswith(pc.PACKAGE_FILENAME_SUFFIX) and not name.lower().endswith(".zip"):
        raise pc.PackageError(pc.ERR_ENTRY_TYPE,
                              "工程包必须是 .zip / .wgproject.zip")
    size = tmp_path.stat().st_size
    if size <= 0:
        raise pc.PackageError(pc.ERR_IMPORT_SOURCE_INVALID, "空文件")
    if size > _max_package_bytes():
        raise pc.PackageError(pc.ERR_SIZE_BUDGET,
                              f"包超过预算 {_max_package_bytes()}B")
    upload_id = f"pkgnz-{uuid.uuid4().hex[:16]}"
    target = uploads_root() / f"{upload_id}.zip"
    shutil.move(str(tmp_path), str(target))
    owner = actor.user_id or (f"session:{session_id}" if session_id else "")
    meta = {
        "upload_id": upload_id,
        "owner": owner,
        "org_id": actor.org_id,
        "filename": name[:255],
        "bytes": size,
        "sha256": pc.sha256_file(target),
        "created_at": _utc_now_iso(),
    }
    (uploads_root() / f"{upload_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8",
        newline="\n")
    return meta


def resolve_package_upload(package_upload_id: str, actor) -> Tuple[Path, Dict[str, Any]]:
    """受控上传 ID → (zip path, meta)；owner/org 不符一律拒绝（404 语义）。"""
    if not _UPLOAD_ID_RE.fullmatch(package_upload_id or ""):
        raise pc.PackageError(pc.ERR_IMPORT_SOURCE_INVALID, "上传 ID 形态非法")
    meta_path = uploads_root() / f"{package_upload_id}.json"
    zip_path = uploads_root() / f"{package_upload_id}.zip"
    if not meta_path.is_file() or not zip_path.is_file():
        raise pc.PackageError(pc.ERR_IMPORT_SOURCE_INVALID, "上传不存在或已清理")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise pc.PackageError(pc.ERR_IMPORT_SOURCE_INVALID,
                              f"上传元数据损坏: {e}") from e
    owner = meta.get("owner") or ""
    if actor.user_id and owner == actor.user_id:
        return zip_path, meta
    if actor.org_id is not None and meta.get("org_id") is not None \
            and str(meta.get("org_id")) == str(actor.org_id):
        return zip_path, meta
    raise pc.PackageError(pc.ERR_UNAUTHORIZED, "无权访问该上传")


# ── inspect / plan（零写入）─────────────────────────────────────────────

def inspect_package(zip_path: Path) -> Dict[str, Any]:
    """静态审读 + manifest schema 门（不落 staging）。"""
    inspection = inspect_zip(zip_path)
    assert inspection.manifest_bytes is not None
    try:
        manifest_raw = json.loads(inspection.manifest_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise pc.PackageError(pc.ERR_MANIFEST_INVALID,
                              f"manifest 不是合法 JSON: {e}") from e
    manifest = pc.format_gate(manifest_raw)
    hashes = {f.path: f.sha256 for f in manifest.files}
    planned = {e.target_rel for e in inspection.entries}
    declared_missing = [p for p in sorted(hashes) if p not in planned
                        and p != pc.MANIFEST_PATH]
    extra_entries = [p for p in sorted(planned)
                     if p not in hashes and p != pc.MANIFEST_PATH]
    licenses = [
        {"logical_id": d.logical_id, "name": d.name,
         "spdx": d.license.spdx, "note": d.license.note,
         "distributable": d.license.distributable,
         "classification": d.classification}
        for d in manifest.datasets[:200]
    ]
    counts: Dict[str, int] = {}
    for d in manifest.datasets:
        counts[d.classification] = counts.get(d.classification, 0) + 1
    return {
        "package_id": manifest.package_id,
        "format": manifest.format,
        "format_version": manifest.format_version,
        "created_at": manifest.created_at,
        "origin": manifest.origin.model_dump(),
        "project_name": manifest.project.name,
        "reproduction": manifest.reproduction.model_dump(),
        "classification_counts": counts,
        "totals": {
            "datasets": len(manifest.datasets),
            "workflows": len(manifest.workflows),
            "artifacts": len(manifest.artifacts),
            "maps": len(manifest.maps),
            "files": len(manifest.files),
            "zip_entries": inspection.entry_count,
        },
        "declared_total_bytes": inspection.declared_total_bytes,
        "licenses": licenses,
        "disclosure": manifest.disclosure.model_dump(),
        "conflicts": {
            "declared_missing": declared_missing[:100],
            "extra_entries": extra_entries[:100],
            "duplicate_logical_ids": _duplicate_logical_ids(manifest),
        },
        "limits": {
            "max_package_bytes": _max_package_bytes(),
            "max_entries": pc.MAX_PACKAGE_ENTRIES,
            "max_entry_bytes": pc.MAX_ENTRY_BYTES,
        },
    }


def _duplicate_logical_ids(manifest: pc.PackageManifest) -> List[str]:
    seen: Dict[str, int] = {}
    for d in manifest.datasets:
        seen[d.logical_id] = seen.get(d.logical_id, 0) + 1
    for w in manifest.workflows:
        seen[w.logical_id] = seen.get(w.logical_id, 0) + 1
    for a in manifest.artifacts:
        seen[a.logical_id] = seen.get(a.logical_id, 0) + 1
    for m in manifest.maps:
        seen[m.logical_id] = seen.get(m.logical_id, 0) + 1
    return [k for k, v in seen.items() if v > 1][:100]


# ── 提交 ─────────────────────────────────────────────────────────────────

async def submit_import(
    package_upload_id: str,
    actor,
    *,
    target_name: str = "",
    session_id: Optional[str] = None,
    acknowledged_conflicts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """提交导入：上传复核 → 静态预检 → 幂等 durable job → 进程内 runner。"""
    zip_path, meta = resolve_package_upload(package_upload_id, actor)
    preview = await asyncio.to_thread(inspect_package, zip_path)
    conflicts = preview["conflicts"]
    blocking: List[str] = []
    blocking.extend(f"declared_missing:{p}" for p in conflicts["declared_missing"])
    if conflicts["duplicate_logical_ids"]:
        blocking.extend(f"duplicate:{x}" for x in conflicts["duplicate_logical_ids"])
    acked = set(acknowledged_conflicts or [])
    missing_ack = [c for c in blocking if c not in acked]
    if missing_ack:
        raise pc.PackageError(
            pc.ERR_CONFLICT_UNRESOLVED,
            f"存在未确认的包冲突: {missing_ack[:10]} —— 请在预检确认后随提交"
            " acknowledged_conflicts 一并确认")

    from app.core.database import async_db_session
    from app.services.jobs.lifecycle import JobStatus
    from app.services.jobs.store import DurableJobStore

    params = {
        "package_upload_id": package_upload_id,
        "package_id": preview["package_id"],
        "target_name": (target_name or preview["project_name"])[:120],
        "sha256": meta.get("sha256", ""),
    }
    from app.services.jobs.submit import build_execution_key

    key = build_execution_key(
        "project_package_import", None,
        {"package_upload_id": package_upload_id,
         "target_name": params["target_name"]},
        owner_id=actor.user_id, org_id=str(actor.org_id or ""))
    async with async_db_session() as db:
        job = await DurableJobStore.create(
            db,
            task_type=TASK_TYPE,
            display_name=f"工程包导入 · {params['target_name'][:60]}",
            owner_id=actor.user_id,
            owner_token=actor.owner_token,
            org_id=actor.org_id,
            session_id=session_id,
            parameters=params,
            idempotency_key=key,
            max_retries=0,
        )
        job_id = str(job.id)
        reused = job.idempotency_key != key or job.status != JobStatus.pending.value
    if reused and not (job.status == JobStatus.pending.value
                       and job_id not in _ACTIVE_IMPORT_RUNNERS):
        return {"job_id": job_id, "status": job.status,
                "idempotent_reuse": True,
                "message": "相同规格的导入任务已存在（幂等复用）"}
    runner = asyncio.create_task(
        _run_import_job(job_id, zip_path, actor, params["target_name"],
                        session_id, preview),
        name=f"project-exchange-import:{job_id}")
    _ACTIVE_IMPORT_RUNNERS[job_id] = runner
    runner.add_done_callback(lambda t: _ACTIVE_IMPORT_RUNNERS.pop(job_id, None))
    return {"job_id": job_id, "status": "running",
            "idempotent_reuse": False,
            "message": "工程包导入已启动（默认导入为全新隔离项目；进度见任务中心）"}


# ── runner ───────────────────────────────────────────────────────────────

async def _run_import_job(job_id: str, zip_path: Path, actor,
                          target_name: str, session_id: Optional[str],
                          preview: Dict[str, Any]) -> None:
    from app.lib.cancellation import CancellationToken, OperationCancelled
    from app.services.jobs import registry as cancellation_registry
    from app.services.jobs.store import DurableJobStore
    from app.services.jobs.worker_lifecycle import default_worker_id

    token = CancellationToken(job_id=job_id)
    cancellation_registry.register(job_id, token)
    worker_id = default_worker_id()
    import_id = f"imp-{uuid.uuid4().hex[:16]}"
    staging_root = _imports_root() / import_id
    staging = staging_root / "staging"

    def _fail(code: str, message: str) -> None:
        from app.core.database import SessionLocal

        shutil.rmtree(staging_root, ignore_errors=True)
        with SessionLocal() as db:
            DurableJobStore.mark_failed_sync(
                db, job_id, error=f"{code}: {message}",
                message=f"{code}: {message}"[:255], worker_id=worker_id)
            db.commit()

    def _progress(pct: int, msg: str) -> None:
        from app.core.database import SessionLocal
        from app.services.jobs.lifecycle import JobStatus
        from app.services.jobs.store import DurableJobStore, _utcnow

        with SessionLocal() as db:
            DurableJobStore.transition_sync(
                db, job_id, JobStatus.running, expected=[JobStatus.running],
                progress=max(0, min(100, pct)), progress_message=msg[:255],
                heartbeat_at=_utcnow())
            db.commit()

    try:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            ok = DurableJobStore.mark_running_sync(db, job_id, worker_id=worker_id)
            if ok:
                record = DurableJobStore.get_sync(db, job_id)
                if record is not None:
                    params = dict(record.parameters or {})
                    params["import_id"] = import_id
                    record.parameters = params
            db.commit()
        if not ok:
            return
        _progress(5, "静态审读与 schema 门")
        staging.mkdir(parents=True, exist_ok=True)
        inspection = inspect_zip(zip_path)
        assert inspection.manifest_bytes is not None
        manifest_raw = json.loads(inspection.manifest_bytes.decode("utf-8"))
        manifest = pc.format_gate(manifest_raw)
        expected = {f.path: f.sha256 for f in manifest.files}

        def cancel_check() -> None:
            if token.cancelled:
                raise OperationCancelled("import cancelled")

        _progress(12, "受控解包与哈希对账")
        extract_zip(zip_path, inspection, staging,
                    expected_hashes=expected,
                    progress=lambda p, m: _progress(p, m))

        _progress(55, "拓扑序注册（隔离新项目）")
        created_files: List[Path] = []

        def _register() -> ImportMapping:
            from app.core.database import SessionLocal

            mapping = ImportMapping()
            with SessionLocal() as db:
                try:
                    mapping = _register_all(
                        db, manifest, staging, actor, target_name,
                        session_id, created_files, cancel_check)
                    db.commit()
                except Exception:
                    db.rollback()
                    # 补偿式回滚（create_project 等入口内部 commit，单靠
                    # rollback 不够）：隔离新项目行级联删除（datasets/
                    # workflows/artifacts 经 ORM cascade）+ 自产 upload 行。
                    if mapping.project_id:
                        with SessionLocal() as cleanup_db:
                            from app.models.project import Project

                            row = cleanup_db.get(Project, mapping.project_id)
                            if row is not None:
                                cleanup_db.delete(row)
                            from app.models.upload import UploadRecord

                            for info in mapping.datasets.values():
                                upload_id = info.get("upload_id")
                                if upload_id and str(upload_id).isdigit():
                                    up = cleanup_db.get(
                                        UploadRecord, int(upload_id))
                                    if up is not None:
                                        cleanup_db.delete(up)
                            cleanup_db.commit()
                    for path in created_files:
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    raise
            return mapping

        mapping = await asyncio.to_thread(_register)
        _progress(80, "会话引用恢复（尽力而为）")
        writebacks = await _restore_session_refs(
            mapping, manifest, staging, session_id, cancel_check)
        mapping.session_writebacks = writebacks

        _progress(90, "导入后校验")
        from app.services.project_exchange.verify import verify_import

        verification = await asyncio.to_thread(
            verify_import, manifest, mapping, staging)

        receipt = {
            "import_id": import_id,
            "job_id": job_id,
            "package_id": manifest.package_id,
            "project_id": mapping.project_id,
            "target_name": target_name,
            "mapping": {
                "datasets": mapping.datasets,
                "workflows": mapping.workflows,
                "artifacts": mapping.artifacts,
                "maps": mapping.maps,
            },
            "session_writebacks": writebacks,
            "skipped": mapping.skipped,
            "verification": verification,
            "created_at": _utc_now_iso(),
        }
        out_dir = _imports_root() / import_id
        shutil.rmtree(out_dir / "staging", ignore_errors=True)
        (out_dir / "receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8", newline="\n")

        from app.core.database import SessionLocal
        from app.services.jobs.lifecycle import JobStatus
        from app.services.jobs.store import DurableJobStore, _utcnow

        with SessionLocal() as db:
            DurableJobStore.transition_sync(
                db, job_id, JobStatus.completed,
                expected=[JobStatus.running], progress=100,
                progress_message=(
                    f"导入完成: 新项目 {mapping.project_id}"),
                completed_at=_utcnow())
            db.commit()
        logger.info("[project-exchange] import %s -> project %s",
                    import_id, mapping.project_id)
    except OperationCancelled:
        shutil.rmtree(staging_root, ignore_errors=True)
        from app.core.database import SessionLocal
        from app.services.jobs.lifecycle import JobStatus
        from app.services.jobs.store import DurableJobStore, _utcnow

        with SessionLocal() as db:
            DurableJobStore.transition_sync(
                db, job_id, JobStatus.cancelled,
                expected=[JobStatus.cancelling, JobStatus.running],
                progress_message="cancelled", completed_at=_utcnow())
            db.commit()
    except Exception as e:  # noqa: BLE001 — 全部如实失败（typed 码在上层映射）
        code = getattr(e, "error_code", pc.ERR_WRITE_FAILED)
        logger.exception("[project-exchange] import job %s failed", job_id)
        _fail(code, str(getattr(e, "reason", e)))
    finally:
        cancellation_registry.discard(job_id)


# ── 注册（单事务体；异常由调用方回滚）─────────────────────────────────

def _register_all(
    db, manifest: pc.PackageManifest, staging: Path, actor,
    target_name: str, session_id: Optional[str],
    created_files: List[Path], cancel_check,
) -> ImportMapping:
    from app.models.project import (
        MapProductVersion, Workflow, WorkflowRevision,
    )
    from app.services.project_service import ProjectService

    mapping = ImportMapping()
    project = ProjectService.create_project(
        db, name=(target_name or manifest.project.name)[:255],
        description=(manifest.project.description or "")[:2000],
        org_id=actor.org_id if actor.org_id is not None else None,
        owner_id=actor.user_id if actor.user_id else None,
        metadata_json={
            "imported_from_package": manifest.package_id,
            "import_origin_project": manifest.origin.project_id,
            "reproduction_level": manifest.reproduction.level,
        },
    )
    if project is None:
        raise pc.PackageError(pc.ERR_WRITE_FAILED, "隔离项目创建失败")
    mapping.project_id = str(project.id)
    mapping.datasets = {}
    mapping.workflows = {}
    mapping.artifacts = {}
    mapping.maps = {}

    real_id_by_logical = _build_real_id_index(manifest)
    new_ds_by_logical: Dict[str, str] = {}

    # 1) datasets（embedded 优先；upload 承载 / 会话恢复双轨）
    for entry in manifest.datasets:
        cancel_check()
        if entry.classification != pc.CLASS_EMBEDDED:
            mapping.skipped.append({
                "kind": "dataset", "logical_id": entry.logical_id,
                "reason": f"classification={entry.classification}"})
            continue
        resolution = _register_dataset(
            db, project.id if not isinstance(project.id, int) else str(project.id),
            manifest, entry, staging, actor, created_files)
        if resolution is None:
            mapping.skipped.append({
                "kind": "dataset", "logical_id": entry.logical_id,
                "reason": "载荷不可注册（形态受限，见恢复报告）"})
            continue
        dataset_id, upload_id, session_pending = resolution
        mapping.datasets[entry.logical_id] = {
            "dataset_id": dataset_id,
            "upload_id": upload_id,
            "original_ref": entry.original_ref,
            "session_restore": session_pending,
        }
        if session_pending:
            mapping.session_writebacks.append({
                "logical_id": entry.logical_id,
                "ref": entry.original_ref,
                "source": session_pending["source"],
                "kind": session_pending["kind"],
            })
        new_ds_by_logical[entry.logical_id] = dataset_id

    # 2) workflows（图内引用重映射）
    for entry in manifest.workflows:
        cancel_check()
        graph = entry.graph_spec
        if not graph and entry.graph_spec_path:
            graph = _read_json(staging / entry.graph_spec_path)
        remap = _build_remap_table(manifest, mapping, real_id_by_logical)
        remapped, edits = _remap_graph(graph, remap)
        workflow = Workflow(
            id=f"wf_{uuid.uuid4().hex[:16]}",
            project_id=str(project.id),
            name=(entry.name or entry.logical_id)[:255],
            version=1,
            graph_spec=remapped if remapped else None,
            inputs_schema=entry.inputs_schema or None,
        )
        db.add(workflow)
        db.flush()
        revision = WorkflowRevision(
            id=f"wfrev_{uuid.uuid4().hex[:16]}",
            workflow_id=workflow.id,
            revision_no=1,
            graph_spec=remapped if remapped else None,
            graph_fingerprint=entry.graph_fingerprint or "",
            created_by=actor.user_id,
        )
        db.add(revision)
        db.flush()
        workflow.current_revision_id = revision.id
        mapping.workflows[entry.logical_id] = {
            "workflow_id": str(workflow.id),
            "remapped_edits": edits,
            "runnable_declared": entry.runnable,
        }

    # 3) artifacts（BlobStore 内容 + 修订账本 + 血缘）
    for entry in manifest.artifacts:
        cancel_check()
        artifact = _register_artifact(
            db, str(project.id), entry, staging, manifest, mapping,
            real_id_by_logical, actor)
        if artifact is None:
            mapping.skipped.append({
                "kind": "artifact", "logical_id": entry.logical_id,
                "reason": "载荷缺失或形态不支持"})
            continue
        mapping.artifacts[entry.logical_id] = {
            "artifact_id": str(artifact.id),
            "content_available": True,
        }

    # 4) map products（mapspec 快照 + 栅格引用重写计划）
    for entry in manifest.maps:
        cancel_check()
        mapspec = _read_json(staging / entry.mapspec_path) \
            if entry.mapspec_path else {}
        rewrite_plan = _plan_map_raster_rewrite(mapspec, session_id is not None)
        for plan_item in rewrite_plan:
            mapping.session_writebacks.append({
                "kind": "raster_png",
                "map_logical_id": entry.logical_id,
                "source_key": plan_item["source_key"],
                "original_ref": plan_item["original_ref"],
                "new_ref": plan_item["new_ref"],
                "package_path": plan_item["package_path"],
            })
        if rewrite_plan:
            mapspec = _apply_map_raster_rewrite(mapspec, rewrite_plan)
        elif session_id is None:
            # 无会话上下文：保留原生 imageRef，如实披露（恢复报告可见）
            mapping.skipped.append({
                "kind": "map_raster", "logical_id": entry.logical_id,
                "reason": "无会话上下文：栅格源未恢复（原生引用保留）"})
        map_row = MapProductVersion(
            id=f"mpv_{uuid.uuid4().hex[:16]}",
            project_id=str(project.id),
            version_no=1,
            product_fingerprint=entry.map_product_fingerprint,
            mapspec_fingerprint=entry.mapspec_fingerprint,
            mapspec_revision=entry.version_no,
            mapspec_snapshot=mapspec if mapspec else None,
            label=(entry.name or entry.logical_id)[:200],
            actor=f"project_exchange_import:{manifest.package_id[:32]}",
            lineage_kind="import",
            artifact_ids=[
                mapping.artifacts[b.artifact]["artifact_id"]
                for b in entry.source_bindings
                if b.artifact and b.artifact in mapping.artifacts
            ][:32] or None,
        )
        db.add(map_row)
        mapping.maps[entry.logical_id] = {
            "map_product_id": str(map_row.id),
            "version_no": 1,
            "raster_rewrites": len(rewrite_plan),
        }
    return mapping


def _build_real_id_index(manifest: pc.PackageManifest) -> Dict[str, Dict[str, str]]:
    """逻辑 ID → 源系统真实 ID/引用（用于图内引用重映射）。"""
    out: Dict[str, Dict[str, str]] = {"datasets": {}, "workflows": {},
                                      "artifacts": {}}
    for entry in manifest.datasets:
        out["datasets"][entry.logical_id] = entry.origin_id or entry.original_ref
    return out


def _build_remap_table(manifest: pc.PackageManifest, mapping: ImportMapping,
                       real_index: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    """旧引用字符串 → 新引用字符串（图内精确匹配替换）。

    - 会话恢复路径（``ref:`` 原 ref 在目标会话原位恢复）→ 不改写；
    - upload 承载路径 → 旧 dataset id / 旧 source_ref → ``upload:<新 id>``；
    - origin dataset id → 新 dataset id。
    """
    remap: Dict[str, str] = {}
    origin_by_logical: Dict[str, str] = {}
    for entry in manifest.datasets:
        if entry.origin_id:
            origin_by_logical[entry.logical_id] = entry.origin_id
    for logical, info in mapping.datasets.items():
        original = info.get("original_ref") or ""
        new_dataset_id = str(info.get("dataset_id") or "")
        if original.startswith("ref:"):
            continue  # 会话恢复在原 ref 上进行 → 无需改写
        if info.get("upload_id"):
            remap[original] = f"upload:{info['upload_id']}"
        origin = origin_by_logical.get(logical)
        if origin and new_dataset_id:
            remap[origin] = new_dataset_id
    return {k: v for k, v in remap.items() if k}


def _remap_graph(graph: Dict[str, Any],
                 remap: Dict[str, str]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """工作流图内字符串值精确重映射（dataset id / upload 引用）。"""
    edits: List[Dict[str, str]] = []
    if not isinstance(graph, dict):
        return graph, edits

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            return {k: walk(v, f"{path}.{k}") for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        if isinstance(node, str) and node in remap:
            edits.append({"path": path[:200], "from": node[:128],
                          "to": remap[node][:128]})
            return remap[node]
        return node

    remapped = walk(graph, "$")
    return remapped, edits[:100]


def _register_dataset(
    db, project_id: str, manifest: pc.PackageManifest,
    entry: pc.ManifestDataset, staging: Path, actor,
    created_files: List[Path],
) -> Optional[Tuple[str, Optional[int], Optional[Dict[str, Any]]]]:
    """embedded 数据集 → UploadRecord 承载 + ProjectDataset 直挂。

    为什么不走 ``ProjectService.attach_dataset``：该入口的上传 IDOR 守卫
    （``_caller_may_attach_upload``）要求 UploadRecord 归属调用者**已有会话**
    ——它防的是「引用他人的既有上传」。导入场景的 upload 行是本事务**自产**
    的（从包内载荷物化），IDOR 面不存在；且 attach_dataset 内部 commit 会
    破坏单事务回滚假设。直挂复用同一确定性指纹
    （``compute_dataset_fingerprint``），身份语义与生产 attach 一致。

    Returns (dataset_id, upload_id, session_pending) 或 None（不可注册）。
    session_pending 非 None 时由提交后的 async 阶段恢复会话引用。
    """
    import uuid as _uuid

    from app.models.project import ProjectDataset
    from app.services.provenance import compute_dataset_fingerprint

    payload = entry.payload
    if payload is None:
        return None
    src = staging / payload.path
    if not src.is_file():
        return None
    if payload.path != pc.LAYERS_GPKG_PATH and src.suffix.lower() == ".png":
        # PNG 载荷不进 UploadRecord（format 词表无 png）；会话恢复双轨承担
        return None
    upload_id, file_path, _up_meta = _write_upload_file(
        db, entry, staging, payload, created_files)
    if upload_id is None:
        return None
    crs = entry.crs or "EPSG:4326"
    schema_profile = entry.schema_profile or {}
    dataset = ProjectDataset(
        id=f"ds_{_uuid.uuid4().hex[:16]}",
        project_id=str(project_id),
        name=(entry.name or entry.logical_id)[:255],
        source_type="upload",
        source_ref=str(upload_id),
        schema_profile=schema_profile,
        crs=crs,
        quality_status="unchecked",
        version_fingerprint=compute_dataset_fingerprint(
            source_type="upload", source_ref=str(upload_id), crs=crs,
            schema_profile=schema_profile),
    )
    db.add(dataset)
    db.flush()
    is_gpkg_member = (payload.member is not None
                      and payload.path == pc.LAYERS_GPKG_PATH)
    session_pending: Optional[Dict[str, Any]] = None
    if entry.original_ref.startswith("ref:"):
        session_pending = (
            {"source": f"gpkg:{payload.path}:{payload.member}",
             "kind": "fc_from_gpkg"} if is_gpkg_member
            else {"source": str(file_path), "kind": "file"})
    return str(dataset.id), upload_id, session_pending


def _write_upload_file(
    db, entry: pc.ManifestDataset, staging: Path,
    payload: pc.DatasetPayloadRef, created_files: List[Path],
) -> Tuple[Optional[int], Optional[Path], Dict[str, Any]]:
    """包内载荷 → uploads/<id>/original.<ext> + UploadRecord 行。"""
    from app.models.upload import UploadRecord

    src = staging / payload.path
    if payload.member:
        features = _gpkg_layer_to_features(src, payload.member)
        if features is None:
            return None, None, {}
        payload_bytes = json.dumps(
            {"type": "FeatureCollection", "features": features},
            ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ext = ".geojson"
        file_type, fmt = "vector", "geojson"
        feature_count = len(features)
        geometry_type = entry.geometry_type or ""
        bbox = entry.bbox
    else:
        ext = src.suffix.lower()
        if ext not in (".tif", ".tiff", ".csv", ".geojson", ".parquet", ".png"):
            return None, None, {}
        payload_bytes = src.read_bytes()
        file_type = "raster" if ext in (".tif", ".tiff", ".png") else "vector"
        fmt = {".tif": "geotiff", ".tiff": "geotiff", ".csv": "csv",
               ".geojson": "geojson", ".parquet": "csv", ".png": "geotiff"}[ext]
        feature_count = entry.feature_count or 0
        geometry_type = "raster" if file_type == "raster" else (
            entry.geometry_type or "")
        bbox = entry.bbox
    import hashlib

    digest = hashlib.sha256(payload_bytes).hexdigest()
    row = UploadRecord(
        filename="(pending)",
        original_name=(entry.name or entry.logical_id)[:255] + ext,
        file_type=file_type,
        format=fmt,
        crs=entry.crs or "EPSG:4326",
        geometry_type=geometry_type or None,
        feature_count=feature_count,
        bbox=bbox,
        file_size=len(payload_bytes),
        content_sha256=digest,
    )
    db.add(row)
    db.flush()
    upload_dir = Path(_data_dir()) / "uploads" / str(row.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"original{ext}"
    _atomic_write_bytes(target, payload_bytes)
    created_files.append(target)
    row.filename = f"{row.id}/original{ext}"
    db.flush()
    return int(row.id), target, {"bytes": len(payload_bytes), "sha256": digest}


def _gpkg_layer_to_features(gpkg: Path, layer: str,
                            max_features: int = pc.MAX_FEATURES_PER_DATASET
                            ) -> Optional[List[Dict[str, Any]]]:
    """gpkg 图层 → FeatureCollection features（导入方向；有界）。"""
    import pyogrio

    try:
        frame = pyogrio.read_dataframe(str(gpkg), layer=layer)
    except Exception as e:  # noqa: BLE001 — 读回失败 → 诚实不可注册
        logger.warning("[project-exchange] gpkg layer read failed %s: %s",
                       layer, e)
        return None
    if len(frame) > max_features:
        logger.warning("[project-exchange] layer %s exceeds cap", layer)
        return None
    geom_col = frame.geometry.name
    features: List[Dict[str, Any]] = []
    from shapely.geometry import mapping

    props_df = frame.drop(columns=[geom_col])
    records = props_df.to_dict(orient="records")
    geoms = frame.geometry.values
    for i, props in enumerate(records):
        geom = geoms[i]
        features.append({
            "id": i,
            "geometry": mapping(geom) if geom is not None and not geom.is_empty else None,
            "properties": _json_safe(props),
        })
    return features


def _json_safe(props: Dict[str, Any]) -> Dict[str, Any]:
    import math

    out: Dict[str, Any] = {}
    for k, v in props.items():
        if v is None or isinstance(v, (str, bool, int, float)):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                out[str(k)] = None
            else:
                out[str(k)] = v
        else:
            try:
                out[str(k)] = json.loads(json.dumps(v, default=str))
            except (TypeError, ValueError):
                out[str(k)] = str(v)[:500]
    return out


def _register_artifact(
    db, project_id: str, entry: pc.ManifestArtifact, staging: Path,
    manifest: pc.PackageManifest, mapping: ImportMapping,
    real_index: Dict[str, Dict[str, str]], actor,
) -> Optional[Any]:
    """产物 → Artifact + BlobStore + 修订账本 + 血缘行。"""
    import hashlib

    from app.models.project import Artifact, ArtifactLineage
    from app.services.artifact_revisions import record_revision
    from app.services.durable_blob_store import get_filesystem_blob_store
    from app.services.project_artifact_promotion import canonical_dumps

    payload = entry.payload
    content_type = "json"
    data: Optional[bytes] = None
    if payload is not None:
        src = staging / payload.path
        if not src.is_file():
            return None
        raw = src.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if payload.sha256 and digest != payload.sha256:
            raise pc.PackageError(
                pc.ERR_HASH_MISMATCH,
                f"产物 {entry.logical_id} 载荷哈希与 manifest 不符")
        if src.suffix.lower() in (".geojson", ".json"):
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None
            blob_text = canonical_dumps(parsed)
            data = blob_text.encode("utf-8")
            content_type = "json"
        else:
            data = raw
            content_type = "binary"
        store = get_filesystem_blob_store()
        result = store.put_blob(digest, data, content_type)
        location = result.location
        byte_size = len(data)
    else:
        return None
    artifact = Artifact(
        id=f"art_{uuid.uuid4().hex[:16]}",
        project_id=project_id,
        name=(entry.logical_id or "imported-artifact")[:255],
        artifact_type=entry.artifact_type[:64],
        format=entry.format[:64] or None,
        crs=entry.crs,
        storage_ref="",
        content_fingerprint=entry.content_fingerprint or digest,
        metadata_json={
            "content_status": "promoted",
            "content_location": location,
            "content_payload_sha256": digest,
            "imported_from_package": manifest.package_id,
            "logical_id": entry.logical_id,
        },
    )
    db.add(artifact)
    db.flush()
    record_revision(
        db, artifact_id=str(artifact.id), content_sha256=digest,
        content_location=location, content_type=content_type,
        byte_size=byte_size,
        metadata={"imported_from_package": manifest.package_id})
    lineage = entry.lineage
    parent_ids = [
        mapping.artifacts[p]["artifact_id"]
        for p in (lineage.parents or []) if p in mapping.artifacts
    ][:16]
    for parent in parent_ids:
        db.add(ArtifactLineage(
            artifact_id=str(artifact.id),
            parent_artifact_id=parent,
            producing_tool=lineage.producing_tool[:128] or None,
            tool_version=lineage.tool_version[:64] or None,
            parameters=lineage.parameters or None,
        ))
    return artifact


def _plan_map_raster_rewrite(mapspec: Dict[str, Any],
                             has_session: bool) -> List[Dict[str, str]]:
    """MapSpec imageRef → 新会话 ref 重写计划（会话恢复提交后执行）。"""
    if not has_session or not isinstance(mapspec, dict):
        return []
    sources = mapspec.get("sources") or {}
    plan: List[Dict[str, str]] = []
    for key, src in list(sources.items())[:64]:
        if not isinstance(src, dict):
            continue
        ref = src.get("imageRef")
        if isinstance(ref, str) and ref.startswith("ref:raster/"):
            new_ref = f"ref:raster/{uuid.uuid4().hex[:16]}"
            plan.append({"source_key": str(key)[:128],
                         "original_ref": ref[:128],
                         "new_ref": new_ref,
                         "package_path": ""})
    return plan


def _apply_map_raster_rewrite(mapspec: Dict[str, Any],
                              plan: List[Dict[str, str]]) -> Dict[str, Any]:
    by_key = {p["source_key"]: p for p in plan}
    import copy

    rewritten = copy.deepcopy(mapspec)
    sources = rewritten.get("sources") or {}
    for key, src in sources.items():
        p = by_key.get(str(key))
        if p and isinstance(src, dict) and src.get("imageRef") == p["original_ref"]:
            src["imageRef"] = p["new_ref"]
    return rewritten


# ── 提交后：会话引用恢复（尽力而为）────────────────────────────────────

async def _restore_session_refs(
    mapping: ImportMapping, manifest: pc.PackageManifest, staging: Path,
    session_id: Optional[str], cancel_check,
) -> List[Dict[str, Any]]:
    if not session_id or not mapping.session_writebacks:
        return []
    results: List[Dict[str, Any]] = []
    from app.services.workspace.durability import (
        restore_raster_png, write_back_session_payload,
    )

    for item in mapping.session_writebacks[:200]:
        cancel_check()
        entry: Dict[str, Any] = {
            "ref": item.get("ref") or item.get("new_ref") or "",
            "kind": item.get("kind") or "",
        }
        try:
            if item.get("kind") == "raster_png":
                path = _find_map_raster_package_path(
                    mapping, manifest, staging, item)
                if path is None:
                    entry["status"] = "skipped"
                    entry["reason"] = "包内栅格文件缺失"
                else:
                    data = await asyncio.to_thread(path.read_bytes)
                    ok = await restore_raster_png(
                        session_id, str(item["new_ref"]), data)
                    entry["status"] = "restored" if ok else "failed"
            else:
                payload = await asyncio.to_thread(
                    _load_ref_payload, staging, item)
                if payload is None:
                    entry["status"] = "skipped"
                    entry["reason"] = "载荷不可读"
                else:
                    ok, mode = await write_back_session_payload(
                        session_id, str(item["ref"]), payload)
                    entry["status"] = "restored" if ok else "failed"
                    entry["mode"] = mode
        except Exception as e:  # noqa: BLE001 — 恢复失败如实披露
            entry["status"] = "failed"
            entry["reason"] = f"{type(e).__name__}: {e}"[:300]
        results.append(entry)
    return results


def _find_map_raster_package_path(
    mapping: ImportMapping, manifest: pc.PackageManifest, staging: Path,
    item: Dict[str, Any],
) -> Optional[Path]:
    """地图栅格 → 包内文件（导出侧命名 `<map_lid>-r<idx>.png`；按序匹配）。"""
    map_lid = item.get("map_logical_id") or ""
    original = item.get("original_ref") or ""
    for entry in manifest.maps:
        if entry.logical_id != map_lid:
            continue
        for idx, binding in enumerate(entry.source_bindings):
            if binding.raster_ref == original:
                candidate = staging / "standard" / "rasters" / (
                    f"{map_lid}-r{idx}.png")
                if candidate.is_file():
                    return candidate
    return None


def _load_ref_payload(staging: Path, item: Dict[str, Any]) -> Optional[Any]:
    source = item.get("source") or ""
    if source.startswith("gpkg:"):
        _prefix, rel, member = source.split(":", 2)
        features = _gpkg_layer_to_features(staging / rel, member)
        if features is None:
            return None
        return {"type": "FeatureCollection", "features": features}
    path = Path(source)
    if not path.is_absolute():
        path = staging / source
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ── 状态查询 ─────────────────────────────────────────────────────────────

def get_import_receipt(import_id: str, actor) -> Dict[str, Any]:
    if not re.fullmatch(r"imp-[0-9a-f]{16}", import_id or ""):
        raise pc.PackageError(pc.ERR_JOB_NOT_FOUND, "import_id 形态非法")
    path = _imports_root() / import_id / "receipt.json"
    if not path.is_file():
        raise pc.PackageError(pc.ERR_JOB_NOT_FOUND, "导入回执不存在")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise pc.PackageError(pc.ERR_JOB_NOT_FOUND,
                              f"回执不可读: {e}") from e
    return receipt


def get_export_receipt(package_id: str, actor) -> Dict[str, Any]:
    from app.services.project_exchange.exporter import package_dir

    _pkg_gate(package_id)
    path = package_dir(package_id) / "receipt.json"
    if not path.is_file():
        raise pc.PackageError(pc.ERR_JOB_NOT_FOUND, "导出回执不存在")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise pc.PackageError(pc.ERR_JOB_NOT_FOUND, f"回执不可读: {e}") from e


def _pkg_gate(package_id: str) -> None:
    if not re.fullmatch(r"pkg-[0-9a-f]{32}", package_id or ""):
        raise pc.PackageError(pc.ERR_JOB_NOT_FOUND, "package_id 形态非法")


# ── 小工具 ───────────────────────────────────────────────────────────────

def _packages_root() -> Path:
    from app.core.config import settings

    root = Path(str(settings.DATA_DIR)) / "project-packages"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _imports_root() -> Path:
    root = _packages_root() / "imports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _data_dir() -> str:
    from app.core.config import settings

    return str(settings.DATA_DIR)


def _max_package_bytes() -> int:
    from app.core.config import settings

    return int(getattr(settings, "PROJECT_EXCHANGE_MAX_PACKAGE_BYTES",
                       pc.DEFAULT_MAX_PACKAGE_BYTES))


def _next_upload_row(db) -> int:
    return 0  # UploadRecord id 由 DB 自增分配（_write_upload_file 内 flush 取号）


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent),
                                    prefix=".upload-", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(data)
        os.replace(str(tmp), str(target))
    finally:
        tmp.unlink(missing_ok=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
