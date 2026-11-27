"""工程包导出编排（WP2）—— durable job + staging → ZIP → 原子发布 → 回执。

镜像 data_delivery runner 纪律（进程内执行体，不是新任务中心）：
- 提交：授权复检 → 幂等 durable job → asyncio runner；
- 阶段 A（async）：闭包重读 + 会话载荷解析（真实会话 store）；
- 阶段 B（worker 线程）：物化 → 保真读回 → manifest → ZIP → 原子发布；
- 阶段 C（async）：回执 + exports 下载链登记（owner 侧车 + 会话面板）；
- 取消/失败：staging 丢弃，绝不留半成品；幂等键防重复提交。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.project_exchange import contracts as pc
from app.services.project_exchange.closure import (
    DatasetNode, ProjectClosure, read_project_closure,
)
from app.services.project_exchange.inspector import assign_logical_ids
from app.services.project_exchange.payloads import (
    PayloadOutcome, ResolvedPayload, resolve_artifact_payload,
    resolve_session_payload, resolve_upload_payload,
)
from app.services.project_exchange.writer import (
    MaterializeError, PackageMaterializer,
    fc_features_from_payload, stream_fc_features,
)

logger = logging.getLogger(__name__)

#: durable job task_type（任务中心可见）。
TASK_TYPE_EXPORT = "project_package_export"
TASK_TYPE_IMPORT = "project_package_import"

#: 进程内并发导出上限（共机资源纪律）。
_EXPORT_SEMAPHORE = asyncio.Semaphore(2)

_ACTIVE_EXPORT_RUNNERS: Dict[str, asyncio.Task] = {}


def packages_root() -> Path:
    """包台账根目录（manifest/receipt 副本；staging 也在其下）。"""
    from app.core.config import settings

    root = Path(str(settings.DATA_DIR)) / "project-packages"
    root.mkdir(parents=True, exist_ok=True)
    return root


def package_dir(package_id: str) -> Path:
    _validate_pkg_id(package_id)
    path = packages_root() / package_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _validate_pkg_id(package_id: str) -> None:
    import re

    if not re.fullmatch(r"pkg-[0-9a-f]{32}", package_id or ""):
        raise pc.PackageError(pc.ERR_MANIFEST_INVALID, "package_id 形态非法")


def max_package_bytes() -> int:
    from app.core.config import settings

    return int(getattr(settings, "PROJECT_EXCHANGE_MAX_PACKAGE_BYTES",
                       pc.DEFAULT_MAX_PACKAGE_BYTES))


# ── 授权 ─────────────────────────────────────────────────────────────────

def _authorize_project(project_id: str, actor, *, write: bool = False):
    """ProjectService 权威门（owner/org；"a project id is never a capability"）。"""
    from app.services.project_service import ProjectService

    def _run():
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            if write:
                return ProjectService.get_project_for_write(
                    db, project_id,
                    user_id=actor.user_id, org_id=actor.org_id)
            return ProjectService.get_project_with_auth(
                db, project_id,
                user_id=actor.user_id, org_id=actor.org_id)

    return asyncio.to_thread(_run)


# ── 提交 ─────────────────────────────────────────────────────────────────

async def submit_export(
    project_id: str,
    actor,
    *,
    selection: Optional[Dict[str, Any]] = None,
    package_name: str = "",
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """提交导出：授权 → 零写入预检 → durable job（幂等）→ 进程内 runner。"""
    project = await _authorize_project(project_id, actor)
    if project is None:
        raise pc.PackageError(pc.ERR_UNAUTHORIZED,
                              "无权访问该项目（或项目不存在）")
    selection = _normalize_selection(selection)
    await asyncio.to_thread(_sweep_stale_staging)
    closure = await asyncio.to_thread(
        _read_closure_sync, project_id, selection)
    # R1 P3-16：session_ref 在计划期是暂态 —— 执行期可解析为 embedded，
    # 预检口径与执行期一致（否则纯会话 ref 项目被误拒）。
    has_session_payloads = any(
        node.classification == "session_ref"
        for node in closure.datasets.values())
    if (not closure.embedded_dataset_count() and not closure.maps
            and not has_session_payloads):
        raise pc.PackageError(
            pc.ERR_EMPTY_SELECTION,
            "闭包内没有可打包的数据载荷或地图 —— 请先物化数据为项目产物/上传")
    plan_level = closure.repro_level()

    from app.core.database import async_db_session
    from app.services.jobs.lifecycle import JobStatus
    from app.services.jobs.store import DurableJobStore

    params = {
        "project_id": project_id,
        "selection": selection,
        "package_name": (package_name or closure.project_name)[:120],
        "planned_level": plan_level,
    }
    key = _execution_key(TASK_TYPE_EXPORT, project_id, selection,
                         package_name, actor)
    async with async_db_session() as db:
        job = await DurableJobStore.create(
            db,
            task_type=TASK_TYPE_EXPORT,
            display_name=f"工程包导出 · {(package_name or closure.project_name)[:60]}",
            owner_id=actor.user_id,
            owner_token=actor.owner_token,
            org_id=actor.org_id,
            session_id=session_id,
            project_id=project_id,
            parameters=params,
            idempotency_key=key,
            max_retries=0,
        )
        job_id = str(job.id)
        reused = job.idempotency_key != key or job.status != JobStatus.pending.value
    if reused and not (job.status == JobStatus.pending.value
                       and job_id not in _ACTIVE_EXPORT_RUNNERS):
        return {"job_id": job_id, "status": job.status,
                "idempotent_reuse": True,
                "message": "相同规格的导出任务已存在（幂等复用）"}
    runner = asyncio.create_task(
        _run_export_job(job_id, project_id, selection, actor,
                        package_name or closure.project_name,
                        session_id),
        name=f"project-exchange-export:{job_id}")
    _ACTIVE_EXPORT_RUNNERS[job_id] = runner

    def _cleanup(task: asyncio.Task) -> None:
        _ACTIVE_EXPORT_RUNNERS.pop(job_id, None)

    runner.add_done_callback(_cleanup)
    return {"job_id": job_id, "status": "running",
            "idempotent_reuse": False,
            "message": "工程包导出已启动（进度见任务中心）"}


def _normalize_selection(selection: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    sel = dict(selection or {})
    kind = str(sel.get("kind") or pc.SCOPE_PROJECT)
    if kind not in pc.SCOPES:
        raise pc.PackageError(pc.ERR_MANIFEST_INVALID, f"未知范围类型: {kind[:40]}")
    out = {"kind": kind}
    map_ids = [str(x) for x in (sel.get("map_product_ids") or [])][:pc.MAX_SELECTION_IDS]
    wf_ids = [str(x) for x in (sel.get("workflow_ids") or [])][:pc.MAX_SELECTION_IDS]
    if map_ids:
        out["map_product_ids"] = map_ids
    if wf_ids:
        out["workflow_ids"] = wf_ids
    return out


def _read_closure_sync(project_id: str, selection: Dict[str, Any]) -> ProjectClosure:
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        return read_project_closure(db, project_id, selection=selection)


def _execution_key(task_type: str, project_id: str, selection: Dict[str, Any],
                   name: str, actor) -> str:
    from app.services.jobs.submit import build_execution_key

    params = {"project_id": project_id, "selection": selection, "name": name}
    from app.services.jobs.context import current_origin

    origin = current_origin()
    return build_execution_key(
        task_type, None, params,
        owner_id=(origin.owner_id if origin else actor.user_id),
        org_id=str(origin.org_id if origin else actor.org_id))


# ── runner ───────────────────────────────────────────────────────────────

async def _run_export_job(
    job_id: str, project_id: str, selection: Dict[str, Any], actor,
    package_name: str, session_id: Optional[str],
) -> None:
    from app.lib.cancellation import CancellationToken, OperationCancelled
    from app.services.jobs import registry as cancellation_registry
    from app.services.jobs.store import DurableJobStore
    from app.services.jobs.worker_lifecycle import default_worker_id

    token = CancellationToken(job_id=job_id)
    cancellation_registry.register(job_id, token)
    worker_id = default_worker_id()
    package_id = pc.new_package_id()

    def _fail(code: str, message: str) -> None:
        from app.core.database import SessionLocal

        _discard_staging(package_id)
        with SessionLocal() as db:
            DurableJobStore.mark_failed_sync(
                db, job_id, error=f"{code}: {message}",
                message=f"{code}: {message}"[:255], worker_id=worker_id)
            db.commit()

    try:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            ok = DurableJobStore.mark_running_sync(db, job_id, worker_id=worker_id)
            if ok:
                record = DurableJobStore.get_sync(db, job_id)
                if record is not None:
                    params = dict(record.parameters or {})
                    params["package_id"] = package_id
                    record.parameters = params
            db.commit()
        if not ok:
            return

        progress = _make_progress(job_id)
        progress(5, "闭包重读与授权复核")
        closure = await asyncio.to_thread(_read_closure_sync, project_id, selection)
        # 提交后项目可能变化：闭包以执行时刻为准（manifest.origin 记录时刻）。
        progress(15, "载荷解析（上传/会话/产物）")
        resolved = await _resolve_all_payloads(closure, session_id, token)

        async with _EXPORT_SEMAPHORE:
            outcome = await asyncio.to_thread(
                _execute_export_sync, job_id, package_id, closure, resolved,
                package_name, actor, session_id, token, progress,
                _cancel_check(token))

        progress(95, "登记下载链与会话面板")
        await _finalize_export(job_id, package_id, outcome, actor, session_id)
    except OperationCancelled:
        _discard_staging(package_id)
        from app.core.database import SessionLocal
        from app.services.jobs.lifecycle import JobStatus
        from app.services.jobs.store import _utcnow

        with SessionLocal() as db:
            DurableJobStore.transition_sync(
                db, job_id, JobStatus.cancelled,
                expected=[JobStatus.cancelling, JobStatus.running],
                progress_message="cancelled", completed_at=_utcnow())
            db.commit()
    except (pc.PackageError, MaterializeError) as e:
        _fail(getattr(e, "error_code", pc.ERR_WRITE_FAILED), str(getattr(e, "reason", e)))
    except Exception as e:  # noqa: BLE001 — 未预期错误如实失败
        logger.exception("[project-exchange] export job %s crashed", job_id)
        _fail(pc.ERR_WRITE_FAILED, f"{type(e).__name__}: {e}")
    finally:
        cancellation_registry.discard(job_id)


def _cancel_check(token) -> Callable[[], None]:
    from app.lib.cancellation import OperationCancelled

    def check() -> None:
        if token.cancelled:
            raise OperationCancelled("export cancelled")
    return check


_STAGING_TTL_SECONDS = 24 * 3600


def _sweep_stale_staging() -> None:
    """R1 P3-18：崩溃遗留 staging/导入暂存的 TTL 清扫（>24h；尽力而为）。"""
    import time

    now = time.time()
    for root in (packages_root(), packages_root() / "imports"):
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not (child.name.endswith(".staging")
                    or child.name.startswith("imp-")):
                continue
            try:
                if now - child.stat().st_mtime > _STAGING_TTL_SECONDS:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                continue


def _discard_staging(package_id: str) -> None:
    staging = packages_root() / f"{package_id}.staging"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)


def _make_progress(job_id: str):
    from app.services.jobs.store import DurableJobStore, _utcnow

    def _progress(pct: int, msg: str) -> None:
        from app.core.database import SessionLocal
        from app.services.jobs.lifecycle import JobStatus

        with SessionLocal() as db:
            DurableJobStore.transition_sync(
                db, job_id, JobStatus.running, expected=[JobStatus.running],
                progress=max(0, min(100, pct)),
                progress_message=msg[:255], heartbeat_at=_utcnow())
            db.commit()
    return _progress


# ── 阶段 A：载荷解析 ─────────────────────────────────────────────────────

async def _resolve_all_payloads(
    closure: ProjectClosure, session_id: Optional[str], token,
) -> Dict[str, PayloadOutcome]:
    """逐依赖解析真实载荷；解析失败落为分类事实（missing/unsupported+reason）。"""
    from app.lib.cancellation import OperationCancelled

    outcomes: Dict[str, PayloadOutcome] = {}
    for real_id, node in closure.datasets.items():
        if node.detached:
            outcomes[real_id] = PayloadOutcome(
                classification=pc.CLASS_MISSING, reason="数据集已摘除")
            continue
        if node.classification == pc.CLASS_EMBEDDED and node.upload_path:
            # R1 P2-8：文件 sha256/上传探测是 O(payload) IO —— 卸载到
            # worker 线程，绝不阻塞事件循环。
            outcomes[real_id] = await asyncio.to_thread(
                resolve_upload_payload, node)
        elif node.classification == "session_ref":
            outcomes[real_id] = await resolve_session_payload(session_id, node)
        elif node.classification == pc.CLASS_EXTERNAL_REFERENCE:
            outcomes[real_id] = PayloadOutcome(
                classification=pc.CLASS_EXTERNAL_REFERENCE,
                reason=node.reason or "外部源不随包携带")
        else:
            outcomes[real_id] = PayloadOutcome(
                classification=node.classification, reason=node.reason)
        if token.cancelled:
            raise OperationCancelled("export cancelled")
    for real_id, node in closure.artifacts.items():
        # 产物读回 + 摘要校验同为 O(payload) IO（R1 P2-8）
        outcomes[f"art:{real_id}"] = await asyncio.to_thread(
            resolve_artifact_payload, node)
    return outcomes


# ── 阶段 B：物化 + 打包（worker 线程，全同步）───────────────────────────

def _execute_export_sync(
    job_id: str,
    package_id: str,
    closure: ProjectClosure,
    resolved: Dict[str, PayloadOutcome],
    package_name: str,
    actor,
    session_id: Optional[str],
    token,
    progress,
    cancel_check,
) -> Dict[str, Any]:
    """物化 → 保真读回 → manifest → ZIP → 原子发布。返回 outcome dict。"""
    staging = packages_root() / f"{package_id}.staging"
    _discard_staging(package_id)
    staging.mkdir(parents=True, exist_ok=True)
    logical = assign_logical_ids(closure)
    created_at = _utc_now_iso()

    try:
        mat = PackageMaterializer(staging)
        dataset_entries: List[pc.ManifestDataset] = []
        gpkg_member_indices: List[int] = []
        counts = {c: 0 for c in pc.CLASSIFICATIONS}

        total_ds = len(closure.datasets)
        for idx, (real_id, node) in enumerate(closure.datasets.items()):
            cancel_check()
            lid = logical["datasets"][real_id]
            outcome = resolved.get(real_id) or PayloadOutcome()
            entry = pc.ManifestDataset(
                logical_id=lid,
                name=node.name[:255],
                classification=outcome.classification,
                source_type=node.source_type[:64],
                crs=node.crs,
                geometry_type=(outcome.payload.geometry_type if outcome.payload
                               else node.geometry_type) or "",
                feature_count=(outcome.payload.feature_count if outcome.payload
                               else node.feature_count),
                bbox=(outcome.payload.bbox if outcome.payload else node.bbox),
                schema_profile=_bounded_profile(node.schema_profile),
                version_fingerprint=node.version_fingerprint,
                license=pc.LicenseInfo(
                    spdx=_opt_str(node.license.get("spdx"), 64),
                    note=_opt_str(node.license.get("note"), 2000),
                    distributable=_opt_bool(node.license.get("distributable")),
                ),
                original_ref=node.source_ref[:512],
                origin_id=node.dataset_id[:255],
            )
            payload = outcome.payload
            classification = outcome.classification
            # 零行表格不可物化为 CSV（无 schema 可写）→ 诚实降级为
            # unsupported + 披露，不失败整包（零要素矢量有诚实空图层通道）。
            if (classification == pc.CLASS_EMBEDDED and payload is not None
                    and payload.kind == "rows"
                    and not ((payload.data or {}).get("rows"))):
                classification = pc.CLASS_UNSUPPORTED
                payload = None
                closure.disclosure.append({
                    "kind": "dataset", "name": entry.name,
                    "reason": "零行表格无法物化（无字段可写）"})
            entry.classification = classification
            if classification == pc.CLASS_EMBEDDED and payload is not None:
                entry.payload = _materialize_dataset(
                    mat, lid, node, payload, cancel_check)
                if (entry.payload is not None
                        and entry.payload.path == pc.LAYERS_GPKG_PATH):
                    gpkg_member_indices.append(len(dataset_entries))
            elif classification == pc.CLASS_EXTERNAL_REFERENCE:
                entry.external = pc.ExternalRef(
                    uri=node.source_ref[:4096] if node.source_ref.startswith("http") else "",
                    reason=(outcome.reason or node.reason)[:4000])
            counts[classification] += 1
            dataset_entries.append(entry)
            if idx % 5 == 0:
                progress(20 + int(40 * (idx + 1) / max(1, total_ds)),
                         f"数据集物化 {idx + 1}/{total_ds}")

        if gpkg_member_indices:
            # R1 P1-5：多图层共写一个 gpkg —— 逐数据集时刻的哈希是中间态。
            # 统一以终态整文件哈希回填全部成员条目（导入侧按此对账）。
            gpkg_digest = pc.sha256_file(mat._gpkg_path)
            gpkg_bytes = mat._gpkg_path.stat().st_size
            for i in gpkg_member_indices:
                ref = dataset_entries[i].payload
                assert ref is not None
                ref.sha256 = gpkg_digest
                ref.bytes = gpkg_bytes

        progress(65, "产物与地图物化")
        artifact_entries = _materialize_artifacts(
            mat, closure, resolved, logical, cancel_check)
        map_entries = _materialize_maps(
            mat, closure, logical, session_id, cancel_check)

        progress(72, "工作流图与披露文件")
        workflow_entries = _write_workflows(mat, closure, logical)

        progress(80, "保真读回比对")
        fidelity_entries, writer_versions = mat.verify_materialized()

        disclosure = pc.Disclosure(**_disclosure_payload(closure, resolved, counts))
        # 人可读披露文件（manifest.disclosure 的镜像；缺席=无缺口）
        disclosure_lines = ["# 包内不可携带内容披露", ""]
        for bucket, label in (
                (disclosure.not_included, "未包含"),
                (disclosure.external_links, "外部引用（不随包携带）"),
                (disclosure.unsupported, "不支持物化")):
            for item in bucket:
                disclosure_lines.append(
                    f"- [{label}] {item.kind} · {item.name}: {item.reason}")
        if not any((disclosure.not_included, disclosure.external_links,
                    disclosure.unsupported)):
            disclosure_lines.append("- 无缺口：全部依赖随包携带。")
        mat.write_disclosure_md(disclosure_lines)
        progress(82, "文件台账")
        file_entries = mat.inventory()
        total_bytes = mat.total_bytes()
        if total_bytes > max_package_bytes():
            raise pc.PackageError(
                pc.ERR_SIZE_BUDGET,
                f"包总大小 {total_bytes}B 超过预算 {max_package_bytes()}B")

        repro = _repro_summary(closure, counts)
        manifest = pc.PackageManifest(
            package_id=package_id,
            created_at=created_at,
            origin=pc.OriginInfo(
                project_id=closure.project_id,
                project_name=closure.project_name[:255],
            ),
            project=pc.ProjectInfo(
                name=closure.project_name[:255],
                description=(closure.description or "")[:4000],
                metadata_json=_safe_metadata(closure.metadata_json),
            ),
            selection=dict(closure.selection),
            reproduction=repro,
            datasets=dataset_entries,
            workflows=workflow_entries,
            artifacts=artifact_entries,
            maps=map_entries,
            files=file_entries,
            disclosure=disclosure,
            fidelity_receipt=pc.FidelityReceipt(
                datasets=fidelity_entries, writer_versions=writer_versions),
        )
        progress(90, "写 manifest 与压缩")
        manifest_path = staging / pc.MANIFEST_PATH
        manifest_path.write_text(
            json.dumps(manifest.model_dump(), ensure_ascii=False, indent=1,
                       default=str),
            encoding="utf-8", newline="\n")
        if manifest_path.stat().st_size > pc.MAX_MANIFEST_BYTES:
            raise pc.PackageError(
                pc.ERR_MANIFEST_TOO_LARGE, "manifest 超过 1MiB 上限")

        filename = _package_filename(package_name, package_id)
        zip_bytes_total, zip_sha = _write_zip(staging, filename, job_id)
        progress(93, "原子发布")
        # R1 P3-21：session 主体走签名 URL（裸路径对匿名导出永不可下载）
        from app.services.export_storage import export_download_url

        owner = actor.user_id or (f"session:{session_id}" if session_id
                                  else "")
        download_path = export_download_url(filename, owner or "user:none")
        receipt = {
            "package_id": package_id,
            "filename": filename,
            "sha256": zip_sha,
            "bytes": zip_bytes_total,
            "project_id": closure.project_id,
            "reproduction": repro.model_dump(),
            "counts": {k: v for k, v in counts.items() if v},
            "created_at": created_at,
            "download_path": download_path,
        }
        out_dir = package_dir(package_id)
        (out_dir / "receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=1),
            encoding="utf-8", newline="\n")
        manifest_copy = out_dir / "manifest.json"
        manifest_copy.write_text(
            manifest_path.read_text(encoding="utf-8"), encoding="utf-8",
            newline="\n")
        return {
            "package_id": package_id,
            "filename": filename,
            "sha256": zip_sha,
            "bytes": zip_bytes_total,
            "receipt": receipt,
        }
    finally:
        # 发布已完成（zip 已在 exports），staging 不再需要。
        _discard_staging(package_id)


def _materialize_dataset(
    mat: PackageMaterializer, lid: str, node: DatasetNode,
    payload: ResolvedPayload, cancel_check,
) -> pc.DatasetPayloadRef:
    """单数据集载荷 → 包内标准形态（gpkg 图层 / 栅格拷贝 / CSV）。"""
    if payload.kind == "featurecollection":
        feats = fc_features_from_payload(payload.data or {})
        # R1 P2-11：CRS 未声明传 None（gdal 不标 CRS），不捏造 4326
        feats_crs = (node.crs or "").strip() or None
        total, _fields = mat.write_vector_dataset(
            lid, feats, crs=feats_crs or "",
            geometry_type=payload.geometry_type or "Unknown",
            cancel_check=cancel_check)
        if total == 0:
            mat.write_empty_vector_layer(lid, crs=feats_crs or "",
                                         geometry_type=payload.geometry_type or "Unknown")
        # sha256/bytes 由数据集循环结束后的终态回填统一写入（P1-5）
        return pc.DatasetPayloadRef(
            path=pc.LAYERS_GPKG_PATH, member=lid, sha256="", bytes=0)
    if payload.kind == "file":
        path = payload.path
        ext = (path.suffix.lower() if path else ".geojson")
        if ext == ".geojson":
            feats = stream_fc_features(path)
            feats_crs = (node.crs or "").strip() or None
            total, _fields = mat.write_vector_dataset(
                lid, feats, crs=feats_crs or "",
                geometry_type=payload.geometry_type or "Unknown",
                cancel_check=cancel_check)
            if total == 0:
                mat.write_empty_vector_layer(
                    lid, crs=feats_crs or "")
            return pc.DatasetPayloadRef(
                path=pc.LAYERS_GPKG_PATH, member=lid, sha256="", bytes=0)
        if ext in (".tif", ".tiff", ".png"):
            mat.write_raster_copy(lid, path, ext, crs=node.crs or "")
            target = (mat.root / "standard" / "rasters" / f"{lid}{ext}")
            return pc.DatasetPayloadRef(
                path=str(target.relative_to(mat.root)),
                sha256=pc.sha256_file(target), bytes=target.stat().st_size)
        if ext in (".csv", ".parquet"):
            target = mat.root / "standard" / "tables" / f"{lid}{ext}"
            shutil.copyfile(path, target)
            return pc.DatasetPayloadRef(
                path=str(target.relative_to(mat.root)),
                sha256=pc.sha256_file(target), bytes=target.stat().st_size)
        raise MaterializeError(
            pc.ERR_ENTRY_TYPE, f"上传文件扩展名不受支持: {ext}")
    if payload.kind == "raster_png":
        mat.write_raster_copy(lid, payload.path, ".png", crs="")
        target = mat.root / "standard" / "rasters" / f"{lid}.png"
        return pc.DatasetPayloadRef(
            path=str(target.relative_to(mat.root)),
            sha256=pc.sha256_file(target), bytes=target.stat().st_size)
    if payload.kind == "rows":
        rows = (payload.data or {}).get("rows") or []
        mat.write_table(lid, rows, crs=node.crs or "")
        target = mat.root / "standard" / "tables" / f"{lid}.csv"
        return pc.DatasetPayloadRef(
            path=str(target.relative_to(mat.root)),
            sha256=pc.sha256_file(target), bytes=target.stat().st_size)
    raise MaterializeError(pc.ERR_WRITE_FAILED,
                           f"未知载荷形态: {payload.kind}")


def _materialize_artifacts(
    mat: PackageMaterializer, closure: ProjectClosure,
    resolved: Dict[str, PayloadOutcome], logical, cancel_check,
) -> List[pc.ManifestArtifact]:
    entries: List[pc.ManifestArtifact] = []
    for real_id, node in closure.artifacts.items():
        cancel_check()
        lid = logical["artifacts"][real_id]
        outcome = resolved.get(f"art:{real_id}") or PayloadOutcome()
        entry = pc.ManifestArtifact(
            logical_id=lid,
            artifact_type=node.artifact_type[:64],
            format=node.format[:64],
            crs=node.crs,
            content_fingerprint=node.content_fingerprint,
            lineage=pc.LineageEdge(
                parents=[
                    logical["artifacts"].get(p, p) for p in sorted(node.parent_ids)
                ][:64],
                producing_tool=node.producing_tool[:128],
                tool_version=node.tool_version[:64],
                parameters=_bounded_parameters(node.parameters),
                workflow=(logical["workflows"].get(node.workflow_id)
                          if node.workflow_id else None),
            ),
        )
        payload = outcome.payload
        if outcome.classification == pc.CLASS_EMBEDDED and payload is not None:
            rel = _write_artifact_file(mat, lid, payload)
            entry.payload = pc.DatasetPayloadRef(
                path=rel, sha256=pc.sha256_file(mat.root / rel),
                bytes=(mat.root / rel).stat().st_size)
        entries.append(entry)
    return entries


def _write_artifact_file(mat: PackageMaterializer, lid: str,
                         payload: ResolvedPayload) -> str:
    if payload.kind == "featurecollection":
        return mat.write_artifact_payload(lid, ".geojson", data=payload.data)
    if payload.kind == "rows":
        rows = (payload.data or {}).get("rows") or []
        mat.write_table(f"art_{lid}", rows)
        # 表格产物落 tables/（与数据集同一形态）；路径回填
        return f"{pc.TABLES_DIR}art_{lid}.csv"
    src = payload.path
    ext = (src.suffix.lower() if src else ".bin")
    if ext not in (".png", ".tif", ".tiff"):
        ext = ".bin" if ext not in pc.ALLOWED_ENTRY_EXTENSIONS else ext
    if ext == ".bin":
        raise MaterializeError(
            pc.ERR_ENTRY_TYPE, f"产物 {lid} 载荷扩展名不受支持")
    return mat.write_artifact_payload(lid, ext, src_path=src)


def _materialize_maps(
    mat: PackageMaterializer, closure: ProjectClosure, logical,
    session_id: Optional[str], cancel_check,
) -> List[pc.ManifestMap]:
    entries: List[pc.ManifestMap] = []
    for real_id, node in closure.maps.items():
        cancel_check()
        lid = logical["maps"][real_id]
        mapspec = node.mapspec_snapshot or {}
        bindings: List[pc.MapSourceBinding] = []
        if mapspec:
            bindings = _map_source_bindings(mapspec, closure, logical)
            # 地图栅格源（imageRef）→ 包内 PNG 拷贝（会话可用时）
            _materialize_map_rasters(mat, lid, mapspec, session_id)
        rel = mat.write_map(lid, mapspec) if mapspec else ""
        entries.append(pc.ManifestMap(
            logical_id=lid,
            name=(node.name or node.label)[:255],
            mapspec_fingerprint=(node.mapspec_fingerprint or "")[:80],
            mapspec_path=rel or f"{pc.MAPS_DIR}{lid}.mapspec.json",
            source_bindings=bindings,
            map_product_fingerprint=node.product_fingerprint,
            version_no=node.version_no,
        ))
    return entries


def _map_source_bindings(mapspec: Dict[str, Any], closure: ProjectClosure,
                         logical) -> List[pc.MapSourceBinding]:
    """MapSpec 源 → dataset/artifact 绑定（按 source_ref 精确匹配）。"""
    sources = mapspec.get("sources") or {}
    out: List[pc.MapSourceBinding] = []
    for key, src in list(sources.items())[:64]:
        binding = pc.MapSourceBinding(source_key=str(key)[:128])
        ref = None
        if isinstance(src, dict):
            ref = src.get("url") or src.get("dataPath") or src.get("imageRef")
        if isinstance(ref, str):
            binding.raster_ref = ref[:128] if ref.startswith("ref:raster/") else None
            for node in closure.datasets.values():
                if node.source_ref and node.source_ref == ref:
                    binding.dataset = logical["datasets"].get(
                        node.dataset_id, node.dataset_id)
                    break
            if binding.dataset is None and ref.startswith("ref:raster/"):
                binding.raster_ref = ref[:128]
        out.append(binding)
    return out


def _materialize_map_rasters(mat: PackageMaterializer, lid: str,
                             mapspec: Dict[str, Any],
                             session_id: Optional[str]) -> None:
    if not session_id:
        return
    from app.services.artifact_registry import is_raster_ref
    from app.services.artifact_registry import raster_png_path

    sources = mapspec.get("sources") or {}
    idx = 0
    for _key, src in list(sources.items())[:64]:
        if not isinstance(src, dict):
            continue
        ref = src.get("imageRef")
        if not isinstance(ref, str) or not is_raster_ref(ref):
            continue
        path = raster_png_path(session_id, ref)
        if path is None or not Path(path).is_file():
            continue
        if idx >= 64:
            break
        mat.write_raster_copy(f"{lid}-r{idx}", Path(path), ".png")
        idx += 1


def _write_workflows(mat: PackageMaterializer, closure: ProjectClosure,
                     logical) -> List[pc.ManifestWorkflow]:
    entries: List[pc.ManifestWorkflow] = []
    for real_id, node in closure.workflows.items():
        lid = logical["workflows"][real_id]
        runnable = closure._workflow_runnable(node)
        reason = "" if runnable else "存在未 embedded 的输入依赖（见数据集分类）"
        graph_path: Optional[str] = None
        graph_inline: Dict[str, Any] = {}
        if node.graph_spec:
            graph_path = _write_workflow_graph(mat, lid, node.graph_spec)
        bindings = {
            str(k)[:128]: pc.WorkflowInputBinding(
                kind=str((v or {}).get("kind") or "value")[:16] if isinstance(v, dict) else "value",
                logical_id=str((v or {}).get("logical_id") or "")[:64] if isinstance(v, dict) else "",
            )
            for k, v in (node.inputs_schema.get("bindings") or {}).items()
        } if isinstance(node.inputs_schema, dict) else {}
        entries.append(pc.ManifestWorkflow(
            logical_id=lid, name=node.name[:255], version=node.version,
            graph_fingerprint=node.graph_fingerprint,
            graph_spec=graph_inline, graph_spec_path=graph_path,
            inputs_schema=_bounded_inputs_schema(node.inputs_schema),
            input_bindings=bindings, runnable=runnable,
            not_runnable_reason=reason[:4000],
        ))
    return entries


def _write_workflow_graph(mat: PackageMaterializer, lid: str,
                          graph: Dict[str, Any]) -> str:
    wf_dir = mat.root / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    path = wf_dir / f"{lid}.graph.json"
    path.write_text(
        json.dumps(graph, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8", newline="\n")
    return f"workflows/{lid}.graph.json"


# ── manifest 组装辅助 ────────────────────────────────────────────────────

def _bounded_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    blob = json.dumps(profile or {}, ensure_ascii=False, default=str)
    if len(blob) <= 32 * 1024:
        return profile or {}
    return {"__truncated__": True,
            "reason": "schema_profile 超过 32KB（不随包携带完整画像）"}


def _bounded_parameters(params: Dict[str, Any]) -> Dict[str, Any]:
    blob = json.dumps(params or {}, ensure_ascii=False, default=str)
    if len(blob) <= 16 * 1024:
        return params or {}
    return {"__truncated__": True}


def _bounded_inputs_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    blob = json.dumps(schema or {}, ensure_ascii=False, default=str)
    if len(blob) <= 16 * 1024:
        return schema or {}
    return {"__truncated__": True}


def _safe_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """项目 metadata_json → 白名单键（防凭据/路径混入 manifest）。"""
    allowed = ("title", "description", "tags", "area", "theme", "crs_preference")
    out: Dict[str, Any] = {}
    for key in allowed:
        value = metadata.get(key)
        if isinstance(value, (str, int, float, bool)):
            out[key] = value
        elif isinstance(value, list) and len(value) <= 32 and all(
                isinstance(x, str) for x in value):
            out[key] = value
    return out


def _disclosure_payload(closure: ProjectClosure,
                        resolved: Dict[str, PayloadOutcome],
                        counts: Dict[str, int]) -> Dict[str, Any]:
    not_included: List[Dict[str, Any]] = []
    external_links: List[Dict[str, Any]] = []
    unsupported: List[Dict[str, Any]] = []
    for real_id, node in closure.datasets.items():
        outcome = resolved.get(real_id) or PayloadOutcome()
        name = node.name or real_id
        if outcome.classification == pc.CLASS_MISSING:
            not_included.append({"kind": "dataset", "name": name,
                                 "reason": outcome.reason or "missing"})
        elif outcome.classification == pc.CLASS_REDACTED:
            not_included.append({"kind": "dataset", "name": name,
                                 "reason": outcome.reason or "redacted"})
        elif outcome.classification == pc.CLASS_EXTERNAL_REFERENCE:
            external_links.append({"kind": "dataset", "name": name,
                                   "reason": outcome.reason or node.source_ref[:200]})
        elif outcome.classification == pc.CLASS_UNSUPPORTED:
            unsupported.append({"kind": "dataset", "name": name,
                                "reason": outcome.reason or "unsupported"})
    for item in closure.disclosure[:100]:
        kind = item.get("kind")
        target = (external_links if kind == "external_link"
                  else not_included)
        target.append({"kind": str(kind), "name": str(item.get("name"))[:255],
                       "reason": str(item.get("reason"))[:500]})
    return {"not_included": not_included[:200],
            "external_links": external_links[:200],
            "unsupported": unsupported[:200]}


def _repro_summary(closure: ProjectClosure,
                   counts: Dict[str, int]) -> pc.ReproductionSummary:
    """以**导出时刻**的真实分类计数为准（不是计划期暂态）。"""
    embedded = counts.get(pc.CLASS_EMBEDDED, 0)
    workflows = list(closure.workflows.values())
    runnable = sum(1 for wf in workflows if closure._workflow_runnable(wf))
    if workflows and runnable == len(workflows) and embedded:
        level = pc.REPRO_FULL_OFFLINE
    elif embedded and workflows:
        level = pc.REPRO_PARTIAL
    elif embedded:
        level = pc.REPRO_VIEW_ONLY
    else:
        level = pc.REPRO_PARTIAL
    return pc.ReproductionSummary(
        level=level,
        counts={k: v for k, v in counts.items() if v})


# ── ZIP 与发布 ───────────────────────────────────────────────────────────

def _package_filename(package_name: str, package_id: str) -> str:
    from app.services.data_delivery.contracts import safe_filename

    stem = safe_filename(package_name or "project", default="project")
    return f"wgproject-{stem}-{package_id[4:12]}{pc.PACKAGE_FILENAME_SUFFIX}"


def _write_zip(staging: Path, filename: str, job_id: str) -> Tuple[int, str]:
    """staging → ZIP（deflated）→ 原子发布到 exports 目录。"""
    from app.services import export_paths

    exports_root = Path(export_paths.ensure_exports_root())
    final_path = exports_root / filename
    total = 0
    fd, tmp_name = tempfile.mkstemp(dir=str(exports_root),
                                    prefix=f".{filename}.", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    arcname = str(path.relative_to(staging))
                    zf.write(path, arcname)
                    total += path.stat().st_size
        if total > max_package_bytes():
            raise pc.PackageError(
                pc.ERR_SIZE_BUDGET,
                f"包总大小 {total}B 超过预算 {max_package_bytes()}B")
        zip_sha = pc.sha256_file(tmp_path)
        os.replace(str(tmp_path), str(final_path))
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    return total, zip_sha


# ── 阶段 C：登记 ─────────────────────────────────────────────────────────

async def _finalize_export(job_id: str, package_id: str, outcome: Dict[str, Any],
                           actor, session_id: Optional[str]) -> None:
    """exports owner 侧车 + 会话面板 export ref + durable 完成态。"""
    from app.core.database import SessionLocal
    from app.services.export_storage import set_export_owner
    from app.services.jobs.lifecycle import JobStatus
    from app.services.jobs.store import DurableJobStore, _utcnow

    filename = outcome["filename"]
    owner = actor.user_id or (f"session:{session_id}" if session_id else "")
    if owner:
        set_export_owner(filename, owner)
    if session_id:
        try:
            from app.services.artifact_registry import register_artifact

            await register_artifact(
                session_id,
                artifact_id=f"ref:export/{filename}",
                artifact_type="project_package",
                producer_capability="project_exchange_export",
                producer_tool="project_exchange_export",
                metadata={"package_id": package_id,
                          "sha256": outcome.get("sha256", "")},
            )
        except Exception:  # noqa: BLE001 — 面板登记是增值记录，不阻断
            logger.warning("[project-exchange] panel register failed for %s",
                           filename, exc_info=True)
    with SessionLocal() as db:
        DurableJobStore.transition_sync(
            db, job_id, JobStatus.completed,
            expected=[JobStatus.running],
            progress=100,
            progress_message=f"工程包已生成: {filename}",
            result_ref=f"ref:export/{filename}",
            completed_at=_utcnow())
        db.commit()


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _opt_str(value: Any, limit: int) -> str:
    return str(value)[:limit] if isinstance(value, str) else ""


def _opt_bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None
