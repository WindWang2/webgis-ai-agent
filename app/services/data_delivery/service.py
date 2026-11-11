"""Data Delivery Service — 预览/提交/执行/回执/下载 编排（G18 WP07/WP08）。

执行模型：复用既有 durable job（``AnalysisTask``，task_type=data_delivery）
+ 进程内执行体（全局一次一个 writer）+ 两段式取消（DB 事实 + 进程 token；
    DELETE /api/v1/jobs/{id} 直接生效）。阶段化推进，批间检查取消/预算：

    resolve（身份复核）→ scope → transform（CRS/字段）→ write（staging）
    → verify（独立读取路径反读）→ publish（原子发布 + DataObject + 台账）

诚实语义：估算≠实测；成功=真实文件 + 原子发布 + 可下载回读；cancelled
不被晚到 success 覆盖（DurableJobStore 迁移表保证）；失败/取消清理 staging，
绝不留「半成品 + 正确扩展名」的终态目录。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.data_delivery_schema import (
    DeliveryFormat,
    DeliveryJobView,
    DeliveryPreview,
    DeliveryReceipt,
    DeliverySpec,
    FormatCapability,
    LossItem,
    SourceFieldInfo,
    SourceGeometrySummary,
    SourceIdentity,
)
from app.services.data_delivery import contracts as dl_contracts
from app.services.data_delivery import store as dl_store
from app.services.data_delivery.loss import LossAnalysis, LossError, analyze_losses
from app.services.data_delivery.scope import (
    ScopeError,
    ScopePlan,
    make_batch_filter,
    negotiate_scope,
)
from app.services.data_delivery.source_resolver import (
    DeliveryActor,
    SourceResolutionError,
    SourceSnapshot,
    resolve_source,
    sample_parquet_geometry,
)
from app.services.data_delivery.writers import (
    CSVWriter,
    GeoJSONWriter,
    GeoParquetWriter,
    PyogrioWriter,
    ShapefileGroupWriter,
    WriterContext,
    WriterError,
)

logger = logging.getLogger(__name__)

TASK_TYPE = "data_delivery"

#: 全局一次一个数据 writer（资源纪律：默认串行）。
_WRITER_SEMAPHORE = asyncio.Semaphore(1)

#: 进程内在飞的 runner task（防 GC；状态事实源始终是 DB 行）。
_ACTIVE_RUNNERS: Dict[str, "asyncio.Task[None]"] = {}


class DeliveryError(Exception):
    """交付域 typed 失败（error_code + reason；路由层映射 4xx/5xx）。"""

    def __init__(self, error_code: str, reason: str, status_code: int = 422):
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason
        self.status_code = status_code


def _err_from(exc: Exception) -> DeliveryError:
    if isinstance(exc, (SourceResolutionError, ScopeError, LossError, WriterError)):
        code = getattr(exc, "error_code", dl_contracts.ERR_INVALID_SPEC)
        reason = getattr(exc, "reason", None) or str(exc)
        status = 404 if code == dl_contracts.ERR_UNAUTHORIZED else 422
        return DeliveryError(code, reason, status_code=status)
    return DeliveryError(dl_contracts.ERR_INVALID_SPEC, str(exc))


def spec_fingerprint(spec: DeliverySpec) -> str:
    payload = json.dumps(spec.model_dump(), sort_keys=True, ensure_ascii=False,
                         default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def execution_key(spec: DeliverySpec, actor: DeliveryActor,
                  project_id: Optional[str]) -> str:
    from app.services.jobs.submit import build_execution_key

    owner = project_id or spec.source.session_id or actor.user_id or "-"
    return build_execution_key(TASK_TYPE, owner, {
        "spec_fingerprint": spec_fingerprint(spec)})


# ── 预览 ──────────────────────────────────────────────────────────────────

async def preview_delivery(spec: DeliverySpec, actor: DeliveryActor) -> DeliveryPreview:
    """规格 → 保真/范围/能力预览（无副作用；每次现算，不缓存授权结论）。"""
    try:
        snapshot = await resolve_source(spec.source, actor)
    except (SourceResolutionError, ScopeError, LossError) as e:
        raise _err_from(e) from e
    if snapshot.parquet_path is not None:
        sample_parquet_geometry(snapshot)
    try:
        plan = negotiate_scope(snapshot, spec.scope)
        losses = analyze_losses(
            snapshot, spec.format, spec.fields, spec.crs, spec.scope,
            spreadsheet_safe=spec.spreadsheet_safe)
    except (SourceResolutionError, ScopeError, LossError) as e:
        raise _err_from(e) from e
    probe = dl_contracts.probe_format(spec.format)
    _shapefile_mixed_guard(spec, snapshot, losses)

    source_identity = _source_identity(snapshot)
    est_rows: Optional[int] = None
    est_note = "执行期统计实际行数"
    if plan.type == "full":
        est_rows = snapshot.row_count
        est_note = plan.note
    elif plan.type == "ids":
        est_rows = len(plan.requested_ids or [])
        est_note = plan.note
    est_bytes = None
    if est_rows is not None:
        est_bytes = min(_estimate_bytes(spec.format, est_rows),
                        dl_contracts.MAX_OUTPUT_BYTES)

    required = list(losses.required_acks)
    blocking = [LossItem(code=b.code, message=b.message)
                for b in losses.blocking]
    for b in plan.blocking:
        blocking.append(LossItem(code=b["code"], message=b["reason"]))
    if not probe.available:
        blocking.append(LossItem(
            code=dl_contracts.ERR_FORMAT_UNAVAILABLE,
            message=f"格式 {spec.format} 当前不可用：{probe.reason}"))
    if est_rows is not None and est_rows > dl_contracts.MAX_ROWS_PER_DELIVERY:
        blocking.append(LossItem(
            code=dl_contracts.ERR_ROW_LIMIT_EXCEEDED,
            message=(f"范围行数 {est_rows} 超过单次交付上限 "
                     f"{dl_contracts.MAX_ROWS_PER_DELIVERY} —— 请缩小范围后重试")))

    return DeliveryPreview(
        spec_fingerprint=spec_fingerprint(spec),
        source=source_identity,
        requested_format=FormatCapability(
            format=spec.format, available=probe.available,
            constrained=probe.constrained, reason=probe.reason,
            extensions=[dl_contracts.FORMAT_EXT[spec.format]],
            notes=probe.notes),
        formats=[FormatCapability(
            format=p.format, available=p.available, constrained=p.constrained,
            reason=p.reason, notes=p.notes,
            extensions=[dl_contracts.FORMAT_EXT[p.format]])
            for p in dl_contracts.probe_all_formats()],
        estimated_rows=est_rows,
        estimated_bytes=est_bytes,
        estimate_note=est_note + "（估算非实测；字节为格式启发式）",
        losses=[LossItem(code=l.code, message=l.message, fields=l.fields,
                         requires_ack=l.requires_ack)
                for l in losses.losses],
        blocking_issues=blocking,
        required_acknowledgments=required,
        field_name_map_applied=losses.field_map,
        scope_note=plan.note,
    )


def _shapefile_mixed_guard(spec: DeliverySpec, snapshot: SourceSnapshot,
                           losses: LossAnalysis) -> None:
    """Shapefile 混合几何 + 单图层 → 阻断（该格式物理上不容纳混合单层）。"""
    types = set((snapshot.geometry or {}).get("types") or [])
    if spec.format == "shapefile_zip" and len(types) > 1 \
            and spec.geometry_grouping == "single":
        losses.block(dl_contracts.ERR_INVALID_SPEC,
                     f"Shapefile 单图层不能容纳混合几何（{sorted(types)}）；"
                     "请使用 by_type 分组（多成员文件，计数相加守恒）")


def _source_identity(snapshot: SourceSnapshot) -> SourceIdentity:
    geometry = None
    if snapshot.geometry is not None:
        g = dict(snapshot.geometry)
        geometry = SourceGeometrySummary(
            types=list(g.get("types") or []),
            has_z=bool(g.get("has_z")),
            null_or_empty_count=int(g.get("null_or_empty_count") or 0),
            sampled_rows=int(g.get("sampled_rows") or 0))
    return SourceIdentity(
        kind=snapshot.kind,  # type: ignore[arg-type]
        label=snapshot.label,
        content_fingerprint=snapshot.content_fingerprint,
        content_revision=snapshot.content_revision,
        artifact_revision_no=snapshot.artifact_revision_no,
        row_count=snapshot.row_count,
        is_table=snapshot.is_table,
        crs=snapshot.crs,
        crs_known=snapshot.crs is not None,
        fields=[SourceFieldInfo(
            name=f["name"], inferred_type=f.get("inferred_type", "unknown"),
            sample_values=f.get("sample_values"))
            for f in snapshot.fields[: dl_contracts.MAX_FIELDS]],
        geometry=geometry,
        completeness=dict(snapshot.completeness or {}))


_BYTES_PER_ROW_HEURISTIC = {
    "geojson": 900, "gpkg": 500, "geoparquet": 350, "csv": 220,
    "shapefile_zip": 400, "flatgeobuf": 450,
}


def _estimate_bytes(fmt: str, rows: int) -> int:
    return int(_BYTES_PER_ROW_HEURISTIC.get(fmt, 600) * max(rows, 1))


# ── 提交 ──────────────────────────────────────────────────────────────────

async def submit_delivery(
    spec: DeliverySpec, actor: DeliveryActor, *,
    acknowledged_losses: Optional[List[str]] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """提交交付任务：服务端复检（预览逻辑 + 损失确认）→ durable job → runner。"""
    acked = set(acknowledged_losses or [])
    preview = await preview_delivery(spec, actor)
    if preview.blocking_issues:
        first = preview.blocking_issues[0]
        raise DeliveryError(
            first.code,
            f"交付被阻断（{len(preview.blocking_issues)} 项）：{first.message}")
    missing = [c for c in preview.required_acknowledgments if c not in acked]
    if missing:
        raise DeliveryError(
            dl_contracts.ERR_LOSS_ACK_REQUIRED,
            f"存在未确认的格式损失: {missing} —— 请在预览确认后随提交一并"
            "acknowledge（acknowledged_losses 需覆盖全部 required codes）")

    from app.core.database import async_db_session
    from app.services.jobs.lifecycle import JobStatus
    from app.services.jobs.store import DurableJobStore

    key = execution_key(spec, actor, project_id)
    source_session = spec.source.session_id if spec.source.kind in (
        "session_ref", "fabric_parquet", "table_rows") else None
    params = {
        "spec": spec.model_dump(),
        "spec_fingerprint": preview.spec_fingerprint,
        "source_fingerprint": preview.source.content_fingerprint,
        "content_revision": preview.source.content_revision,
        "artifact_revision_no": preview.source.artifact_revision_no,
        "format": spec.format,
        "filename": spec.filename,
    }
    async with async_db_session() as db:
        job = await DurableJobStore.create(
            db,
            task_type=TASK_TYPE,
            display_name=f"数据交付 · {spec.format} · {dl_contracts.safe_filename(spec.filename)}",
            owner_id=actor.user_id,
            owner_token=actor.owner_token,
            org_id=actor.org_id,
            session_id=source_session,
            project_id=project_id,
            parameters=params,
            idempotency_key=key,
            dispatch_spec={"task": "data_delivery.execute", "args": [params]},
        )
        job_id = str(job.id)
        reused = job.idempotency_key != key or job.status != JobStatus.pending.value
    # 已有同键行（重放）→ 不再启动新 runner；进行中/已完成直接查询即可。
    # 例外：行仍 pending 且本进程没有在飞 runner（create 与 runner 启动之间
    # 崩溃过的恢复路径）→ 补启动，避免幂等键把任务卡死在 pending。
    if reused and not (job.status == JobStatus.pending.value
                       and job_id not in _ACTIVE_RUNNERS):
        return {"job_id": job_id, "status": job.status,
                "idempotent_reuse": True,
                "message": "相同规格的交付任务已存在（幂等复用）"}
    runner = asyncio.create_task(
        _run_delivery_job(job_id, spec, actor, project_id),
        name=f"data-delivery:{job_id}",
    )
    _ACTIVE_RUNNERS[job_id] = runner
    runner.add_done_callback(lambda t: _ACTIVE_RUNNERS.pop(job_id, None))
    return {"job_id": job_id, "status": "running",
            "idempotent_reuse": False,
            "message": "交付任务已启动（进度见任务中心/交付面板）"}


# ── 执行体 ────────────────────────────────────────────────────────────────

async def _run_delivery_job(
    job_id: str, spec: DeliverySpec, actor: DeliveryActor,
    project_id: Optional[str],
) -> None:
    """进程内执行体：串行 writer + 阶段化 + 批间取消/预算检查。

    两阶段：async 侧完成源解析/身份复核（需要事件循环与 async DB），
    阻塞写盘全部卸载到 worker 线程（asyncio.to_thread），登记回执回到
    async 侧。取消走既有两段式（DB cancel_requested_at + 进程 token；
    DELETE /api/v1/jobs/{id} 直接生效）。
    """
    from app.lib.cancellation import CancellationToken, OperationCancelled
    from app.services.jobs import registry as cancellation_registry
    from app.services.jobs.store import DurableJobStore
    from app.services.jobs.worker_lifecycle import default_worker_id

    token = CancellationToken(job_id=job_id)
    cancellation_registry.register(job_id, token)
    worker_id = default_worker_id()
    delivery_id = dl_store.new_delivery_id()

    def _fail(error_code: str, message: str) -> None:
        from app.core.database import SessionLocal

        dl_store.discard_staging(delivery_id)
        with SessionLocal() as db:
            DurableJobStore.mark_failed_sync(
                db, job_id, error=f"{error_code}: {message}",
                message=f"{error_code}: {message}"[:255], worker_id=worker_id)
            db.commit()

    try:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            ok = DurableJobStore.mark_running_sync(db, job_id,
                                                   worker_id=worker_id)
            if ok:
                record = DurableJobStore.get_sync(db, job_id)
                if record is not None:
                    params = dict(record.parameters or {})
                    params["delivery_id"] = delivery_id
                    record.parameters = params
            db.commit()
        if not ok:
            return

        # 阶段 A（async）：源解析 + 身份复核（预览/提交之后内容可能已变）
        snapshot = await resolve_source(spec.source, actor)
        frozen = _load_frozen(job_id)
        if frozen.get("source_fingerprint") and \
                snapshot.content_fingerprint != frozen["source_fingerprint"]:
            raise DeliveryError(
                dl_contracts.ERR_SOURCE_REVISION_CHANGED,
                "源内容在提交后发生变化（身份指纹不匹配）—— 请重新预览并提交")
        await snapshot.verify_unchanged(spec.source.session_id)

        # 阶段 B（worker 线程）：scope/损失复检 → 写 → 反读 → 原子发布
        async with _WRITER_SEMAPHORE:
            outcome = await asyncio.to_thread(
                _execute_delivery_sync, job_id, delivery_id, spec, snapshot,
                token, _make_progress(job_id))

        # 阶段 C（async）：durable/台账登记（增值记录，绝不阻断交付本体）
        await _finalize_delivery(job_id, delivery_id, spec, snapshot, outcome,
                                 project_id, worker_id)
    except OperationCancelled:
        dl_store.discard_staging(delivery_id)
        from app.core.database import SessionLocal
        from app.services.jobs.lifecycle import JobStatus
        from app.services.jobs.store import _utcnow

        with SessionLocal() as db:
            # cancelled 不被晚到 success 覆盖：仅从 cancelling/running 收敛；
            # 已 completed（发布完成后取消）则如实保留成功态。
            DurableJobStore.transition_sync(
                db, job_id, JobStatus.cancelled,
                expected=[JobStatus.cancelling, JobStatus.running],
                progress_message="cancelled",
                completed_at=_utcnow(),
            )
            db.commit()
    except (SourceResolutionError, ScopeError, LossError, WriterError) as e:
        code = getattr(e, "error_code", dl_contracts.ERR_WRITE_FAILED)
        _fail(code, str(e))
    except DeliveryError as e:
        _fail(e.error_code, e.reason)
    except Exception as e:  # noqa: BLE001 — 未预期错误如实失败
        logger.exception("[data-delivery] job %s crashed", job_id)
        _fail(dl_contracts.ERR_WRITE_FAILED, f"{type(e).__name__}: {e}")
    finally:
        cancellation_registry.discard(job_id)


def _make_progress(job_id: str):
    from app.services.jobs.lifecycle import JobStatus
    from app.services.jobs.store import DurableJobStore, _utcnow

    def _progress(pct: int, msg: str) -> None:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            DurableJobStore.transition_sync(
                db, job_id, JobStatus.running, expected=[JobStatus.running],
                progress=max(0, min(100, pct)),
                progress_message=msg[:255],
                heartbeat_at=_utcnow(),
            )
            db.commit()

    return _progress


def _execute_delivery_sync(
    job_id: str, delivery_id: str, spec: DeliverySpec, snapshot: SourceSnapshot,
    token: Any,
    progress: Any,
) -> Dict[str, Any]:
    """阻塞执行（worker 线程）：scope→transform→write→verify→原子发布。

    返回 outcome dict（回执素材）；不在此做 DB 终态迁移（由 async 侧收尾）。
    """
    t0 = time.monotonic()
    progress(5, "范围与保真复检")

    plan = negotiate_scope(snapshot, spec.scope)
    if plan.blocking:
        raise DeliveryError(plan.blocking[0]["code"], plan.blocking[0]["reason"])
    if snapshot.parquet_path is not None:
        sample_parquet_geometry(snapshot)
    losses = analyze_losses(snapshot, spec.format, spec.fields, spec.crs,
                            spec.scope, spreadsheet_safe=spec.spreadsheet_safe)
    _shapefile_mixed_guard(spec, snapshot, losses)
    if losses.blocking:
        raise DeliveryError(losses.blocking[0].code, losses.blocking[0].reason)
    probe = dl_contracts.probe_format(spec.format)
    if not probe.available:
        raise DeliveryError(dl_contracts.ERR_FORMAT_UNAVAILABLE,
                            f"格式不可用: {probe.reason}")

    staging = dl_store.staging_dir(delivery_id)
    base_name = dl_contracts.safe_filename(spec.filename)
    out_fields, field_pairs = _resolve_output_fields(snapshot, spec, losses)
    ctx = WriterContext(
        fmt=spec.format, out_dir=staging, base_filename=base_name,
        fields=out_fields, field_map=losses.field_map,
        crs_out=(losses.crs_target if losses.crs_transformed
                 else (losses.crs_source or snapshot.crs)),
        geometry_grouping=spec.geometry_grouping,
        spreadsheet_safe=spec.spreadsheet_safe,
        include_geometry_wkt=spec.fields.include_geometry,
        coordinate_precision=spec.crs.coordinate_precision,
        is_table=snapshot.is_table and spec.format == "csv",
    )
    crs_meta = {
        "source": snapshot.crs,
        "target": (losses.crs_target if losses.crs_transformed
                   else (losses.crs_source or snapshot.crs)),
        "transformed": bool(losses.crs_transformed),
    }

    # ── fast path：fabric-parquet → GeoParquet 无变换安全复制 ──────────
    fast_path = (
        spec.format == "geoparquet"
        and snapshot.parquet_path is not None
        and not losses.crs_transformed
        and plan.type == "full"
        and spec.fields.fields is None
        and not spec.fields.field_map
    )
    progress(15, "读取源数据（分批）")
    batch_filter = make_batch_filter(snapshot, plan)

    losses_applied: List[str] = []
    members: List[Dict[str, Any]]
    rows_written = 0
    if fast_path:
        import shutil

        from app.services.data_delivery.writers import _member

        target = staging / f"{base_name}.parquet"
        shutil.copyfile(snapshot.parquet_path, target)
        members = [_member(target, "data", snapshot.row_count)]
        rows_written = snapshot.row_count
        losses_applied.append("fast_path_byte_copy")
        progress(60, f"安全复制完成（{rows_written} 行，无中间格式转换）")
    else:
        writer = _build_writer(ctx, spec, snapshot, losses)
        transformer = _build_transformer(snapshot, spec, losses)
        transform = _make_transform(field_pairs, spec, transformer, snapshot)
        batches = 0
        for batch in snapshot.iter_feature_batches(dl_contracts.READ_BATCH_ROWS):
            token.raise_if_cancelled()
            batches += 1
            kept = batch_filter(batch)
            if not kept:
                continue
            if spec.format == "csv" and snapshot.is_table:
                writer.write_batch(kept)  # 表格行原样（无 geometry 包装）
            else:
                writer.write_batch(transform(kept))
            rows_written += len(kept)
            if rows_written > dl_contracts.MAX_ROWS_PER_DELIVERY:
                raise DeliveryError(
                    dl_contracts.ERR_ROW_LIMIT_EXCEEDED,
                    f"实际行数超过上限 {dl_contracts.MAX_ROWS_PER_DELIVERY}")
            _check_output_budget(staging)
            if batches % 5 == 0:
                progress(30, f"已写入 {rows_written} 行（批 {batches}）")
        result = writer.close()
        rows_written = result.rows
        members = [dict(m) for m in result.members]
        losses_applied.extend(result.losses_applied)
        _finalize_scope_stats(batch_filter)
    # 声明与实际改变一一对应：预览声明的损失码合并进回执（保留计数型
    # code 交给 writer 实测 —— formula_prefix_sanitized 以实际变换数为准）。
    losses_applied.extend(
        code for code in (
            dl_contracts.LOSS_CRS_LABEL_UNKNOWN if (
                snapshot.crs is None) else None,
            dl_contracts.LOSS_COORDINATE_PRECISION_REDUCED if (
                spec.crs.coordinate_precision is not None) else None,
            dl_contracts.LOSS_MIXED_GEOMETRY_SINGLE_LAYER if (
                spec.format == "gpkg"
                and len(set((snapshot.geometry or {}).get("types") or [])) > 1
                and spec.geometry_grouping == "single") else None,
            dl_contracts.LOSS_GEOMETRY_SPLIT_BY_TYPE if (
                spec.format in ("shapefile_zip", "gpkg")
                and len(set((snapshot.geometry or {}).get("types") or [])) > 1
                and spec.geometry_grouping == "by_type") else None,
            dl_contracts.LOSS_GEOMETRY_OMITTED if (
                spec.format == "csv" and (
                    snapshot.is_table or not spec.fields.include_geometry)) else None,
            dl_contracts.LOSS_FIELD_NAME_TRUNCATED if (
                spec.format == "shapefile_zip" and losses.field_map
                and not spec.fields.field_map) else None,
            dl_contracts.LOSS_FIELD_NAME_MAPPED if (
                spec.format == "shapefile_zip" and spec.fields.field_map) else None,
        ) if code
    )
    if snapshot.crs is None and any(
            f.get("inferred_type") == "complex" for f in snapshot.fields):
        losses_applied.extend(
            code for code in (
                dl_contracts.LOSS_NESTED_VALUE_JSON_ENCODED
                if spec.format != "geojson" else None,
            ) if code
        )
    if rows_written == 0:
        raise DeliveryError(
            dl_contracts.ERR_WRITE_FAILED,
            "范围过滤后没有可交付行 —— 拒绝写空文件冒充成功（请检查选择/谓词）")

    # ── verify：独立读取路径反读（WP11 oracle 的在服务内面）────────────
    progress(88, "反读校验（独立读取路径）")
    verify = _verify_output(staging, spec.format, rows_written, crs_meta)

    # ── publish：manifest → 原子发布（receipt 在登记后定稿重写）────────
    progress(93, "写入清单，原子发布")
    scope_stats = getattr(batch_filter, "stats", None)
    scope_dict = _scope_dict(plan, scope_stats, rows_written, snapshot)
    receipt = DeliveryReceipt(
        delivery_id=delivery_id, job_id=job_id, format=spec.format,
        status="completed", filename=base_name, members=members,
        row_count=rows_written,
        bytes_total=sum(m["bytes"] for m in members),
        content_sha256=next((m["sha256"] for m in members
                             if m.get("role") == "data"), None),
        crs=crs_meta,
        losses_applied=losses_applied,
        field_map_applied=losses.field_map,
        scope=scope_dict,
        source={
            "kind": snapshot.kind, "label": snapshot.label,
            "content_fingerprint": snapshot.content_fingerprint,
            "content_revision": snapshot.content_revision,
            "artifact_revision_no": snapshot.artifact_revision_no,
            "row_count": snapshot.row_count,
        },
        writer_versions=dl_contracts.writer_versions(),
        durable="pending",
        timings={"total_s": round(time.monotonic() - t0, 3),
                 **({"fast_path": True} if fast_path else {})},
    )
    manifest = {
        "delivery_id": delivery_id,
        "format": spec.format,
        "rows": rows_written,
        "members": members,
        "crs": crs_meta,
        "field_map_applied": losses.field_map,
        "losses_applied": losses_applied,
        "scope": scope_dict,
        "source": receipt.source,
        "writer_versions": receipt.writer_versions,
        "verify": verify,
    }
    dl_store.write_manifest(staging, manifest)
    dl_store.write_receipt(staging, _receipt_dict(receipt))
    dl_store.atomic_publish(delivery_id)
    return {
        "receipt": receipt, "manifest": manifest, "verify": verify,
        "rows_written": rows_written, "crs_meta": crs_meta,
    }


async def _finalize_delivery(
    job_id: str, delivery_id: str, spec: DeliverySpec,
    snapshot: SourceSnapshot, outcome: Dict[str, Any],
    project_id: Optional[str], worker_id: str,
) -> None:
    """阶段 C：durable DataObject + 台账登记 → 回指定稿 → 终态 completed。"""
    from fastapi.concurrency import run_in_threadpool

    from app.services.jobs.store import DurableJobStore

    receipt: DeliveryReceipt = outcome["receipt"]
    progress = _make_progress(job_id)
    progress(95, "durable 登记与回指定稿")
    owner_scope: Dict[str, str] = {}
    if project_id:
        owner_scope = {"project_id": project_id}
    elif spec.source.session_id:
        owner_scope = {"session_id": spec.source.session_id}
    durable = await run_in_threadpool(
        dl_store.publish_data_object_for_delivery,
        delivery_id, owner_scope=owner_scope,
        source_refs=_source_refs(spec),
        input_fingerprint=snapshot.content_fingerprint,
        budget=dl_contracts.DATA_OBJECT_PUBLISH_BUDGET_BYTES)
    receipt.durable = "published" if durable else "oversized"
    if durable:
        receipt.data_object_id = durable.get("data_object_id")
    if spec.source.session_id:
        receipt.artifact_ref = await dl_store.register_session_artifact(
            spec.source.session_id, delivery_id, receipt.filename,
            descriptor={"feature_count": receipt.row_count,
                        "format": spec.format},
            metadata={
                "content_sha256": receipt.content_sha256,
                "byte_size": receipt.bytes_total,
                "delivery_id": delivery_id,
                **({"data_object_id": receipt.data_object_id}
                   if receipt.data_object_id else {}),
                "storage": "disk-cursor",
            })
    if project_id:
        parent = (spec.source.artifact_id
                  if spec.source.kind == "project_artifact" else None)
        receipt.project_artifact_id = await run_in_threadpool(
            dl_store.register_project_artifact,
            project_id, delivery_id, receipt.filename, spec.format,
            receipt.content_sha256, receipt.bytes_total, receipt.row_count,
            parent)
    dl_store.write_receipt(dl_store.final_dir(delivery_id),
                           _receipt_dict(receipt))

    from app.core.database import SessionLocal

    with SessionLocal() as db:
        final_status = DurableJobStore.mark_succeeded_sync(
            db, job_id,
            result=_receipt_summary(receipt),
            result_ref=f"data-delivery/{delivery_id}",
            message=(f"交付完成：{receipt.row_count} 行 · "
                     f"{receipt.bytes_total} 字节"),
            worker_id=worker_id,
        )
        db.commit()
    progress(100, "交付完成")


def _load_frozen(job_id: str) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.services.jobs.store import DurableJobStore

    with SessionLocal() as db:
        record = DurableJobStore.get_sync(db, job_id)
        if record is None or not isinstance(record.parameters, dict):
            return {}
        return record.parameters


def _resolve_output_fields(
    snapshot: SourceSnapshot, spec: DeliverySpec, losses: LossAnalysis
) -> Tuple[List[str], List[Tuple[str, str]]]:
    """(输出字段顺序, [(输出名, 源名)]) —— 投影 + 重命名（shapefile 映射）。"""
    all_fields = [f["name"] for f in snapshot.fields]
    src_fields = spec.fields.fields if spec.fields.fields is not None else all_fields
    fmap = losses.field_map or spec.fields.field_map or {}
    pairs = [(fmap.get(k, k), k) for k in src_fields]
    out = [p[0] for p in pairs]
    if len(out) != len(set(out)):
        raise DeliveryError(dl_contracts.ERR_INVALID_SPEC,
                            "输出字段名冲突（投影/映射后重名）")
    return out, pairs


def _build_writer(ctx: WriterContext, spec: DeliverySpec,
                  snapshot: SourceSnapshot, losses: LossAnalysis) -> Any:
    if spec.format == "geojson":
        return GeoJSONWriter(ctx)
    if spec.format == "geoparquet":
        return GeoParquetWriter(ctx, crs_source=losses.crs_target or ctx.crs_out)
    if spec.format == "csv":
        return CSVWriter(ctx, fields_meta=snapshot.fields,
                         geometry_column=spec.fields.include_geometry)
    driver = {"gpkg": "GPKG", "flatgeobuf": "FlatGeobuf",
              "shapefile_zip": "ESRI Shapefile"}[spec.format]
    types = set((snapshot.geometry or {}).get("types") or [])
    mixed = len(types) > 1
    group = spec.format == "shapefile_zip" and mixed
    by_type = spec.geometry_grouping == "by_type" and spec.format == "gpkg" and mixed
    layer = ctx.base_filename or "delivery"
    gtype = "Unknown" if mixed else (next(iter(types), "Unknown") if types else "Unknown")
    if group or by_type:
        return ShapefileGroupWriter(ctx, driver=driver, layer_name=layer)
    return PyogrioWriter(ctx, driver=driver, layer_name=layer,
                         geometry_type=gtype)


def _build_transformer(snapshot: SourceSnapshot, spec: DeliverySpec,
                       losses: LossAnalysis) -> Optional[Any]:
    if not losses.crs_transformed:
        return None
    source = losses.crs_source or snapshot.crs
    target = losses.crs_target
    if not source or not target:
        return None
    try:
        from pyproj import CRS, Transformer

        tr = Transformer.from_crs(CRS.from_user_input(source),
                                  CRS.from_user_input(target), always_xy=True)
        return tr
    except Exception as e:
        raise DeliveryError(
            dl_contracts.ERR_INVALID_SPEC,
            f"CRS 变换不可用（{source} → {target}）: {e}") from e


def _make_transform(field_pairs, spec: DeliverySpec, transformer,
                    snapshot: SourceSnapshot):
    """批变换：投影/重命名/复杂值 JSON 化/shapefile 类型适配/CRS 重投影。"""
    import json as _json

    import shapely
    from shapely.geometry import shape as _shape

    fmt = spec.format
    is_shp = fmt == "shapefile_zip"

    def _transform_geom(g: Dict[str, Any]) -> Dict[str, Any]:
        try:
            geom = _shape(g)
            from shapely.ops import transform as _shp_transform

            out = _shp_transform(transformer.transform, geom)  # type: ignore[union-attr]
            return _json.loads(shapely.to_geojson(out))
        except DeliveryError:
            raise
        except Exception as e:
            raise WriterError(
                dl_contracts.ERR_WRITE_FAILED,
                f"坐标重投影失败（{snapshot.crs} → "
                f"{spec.crs.target_crs}）: {e}") from e

    def transform(features: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for f in features:
            src_props = f.get("properties") or {}
            props: Dict[str, Any] = {}
            for out_name, src_name in field_pairs:
                v = src_props.get(src_name)
                if isinstance(v, (dict, list)):
                    v = _json.dumps(v, ensure_ascii=False, default=str)
                elif is_shp and isinstance(v, bool):
                    v = "true" if v else "false"
                elif is_shp and isinstance(v, int) and not isinstance(v, bool) \
                        and abs(v) > 2 ** 31 - 1:
                    v = str(v)
                elif isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
                    v = None
                props[out_name] = v
            geom = f.get("geometry")
            if transformer is not None and isinstance(geom, dict) and geom:
                geom = _transform_geom(geom)
            nf: Dict[str, Any] = {"type": "Feature", "geometry": geom,
                                  "properties": props}
            if f.get("id") is not None:
                nf["id"] = f["id"]
            out.append(nf)
        return out

    return transform


def _check_output_budget(staging: Path) -> None:
    total = sum(p.stat().st_size for p in staging.rglob("*") if p.is_file())
    if total > dl_contracts.MAX_TEMP_DISK_BYTES:
        raise DeliveryError(
            dl_contracts.ERR_TEMP_DISK_EXCEEDED,
            f"staging 临时盘超过预算 {dl_contracts.MAX_TEMP_DISK_BYTES} 字节")


def _verify_output(staging: Path, fmt: str, expected_rows: Optional[int],
                   crs_meta: Dict[str, Any]) -> Dict[str, Any]:
    """独立读取路径反读（与 writer 不同的解码路径）——行数/CRS/成员证据。"""
    import json as _json

    evidence: Dict[str, Any] = {"rows_read": None, "crs": None, "layers": None}
    try:
        if fmt == "geojson":
            files = list(staging.glob("*.geojson"))
            if len(files) != 1:
                raise DeliveryError(dl_contracts.ERR_VERIFY_FAILED,
                                    f"GeoJSON 成员数异常: {len(files)}")
            count = 0
            decoder = _json.JSONDecoder()
            text = files[0].read_text(encoding="utf-8")
            idx = text.find('"features":[')
            pos = idx + len('"features":[')
            while pos < len(text):
                ch = text[pos]
                if ch in " \t\r\n,":
                    pos += 1
                    continue
                if text.startswith("]}", pos):
                    break
                obj, end = decoder.raw_decode(text, pos)
                count += 1
                pos = end
            evidence["rows_read"] = count
        elif fmt == "geoparquet":
            import pyarrow.parquet as pq

            files = list(staging.glob("*.parquet"))
            if len(files) != 1:
                raise DeliveryError(dl_contracts.ERR_VERIFY_FAILED,
                                    f"GeoParquet 成员数异常: {len(files)}")
            meta = pq.read_metadata(str(files[0]))
            schema = pq.read_schema(str(files[0]))
            evidence["rows_read"] = int(meta.num_rows)
            geo = (schema.metadata or {}).get(b"geo")
            if geo:
                g = _json.loads(geo.decode("utf-8", "replace"))
                col = g.get("primary_column") or "geometry"
                evidence["crs"] = (g.get("columns") or {}).get(col, {}).get("crs")
            if crs_meta.get("transformed") and not evidence["crs"]:
                raise DeliveryError(
                    dl_contracts.ERR_VERIFY_FAILED,
                    "GeoParquet 反读缺少 CRS 元数据（声明变换但元数据缺席）")
        elif fmt == "gpkg":
            import pyogrio

            files = list(staging.glob("*.gpkg"))
            if len(files) != 1:
                raise DeliveryError(dl_contracts.ERR_VERIFY_FAILED,
                                    f"GPKG 成员数异常: {len(files)}")
            layers = pyogrio.list_layers(str(files[0]))
            total = 0
            for name, _gtype in layers:
                info = pyogrio.read_info(str(files[0]), layer=name)
                total += int(info["features"])
            evidence["layers"] = [str(l[0]) for l in layers]
            evidence["rows_read"] = total
        elif fmt == "flatgeobuf":
            import pyogrio

            files = list(staging.glob("*.fgb"))
            if len(files) != 1:
                raise DeliveryError(dl_contracts.ERR_VERIFY_FAILED,
                                    f"FlatGeobuf 成员数异常: {len(files)}")
            info = pyogrio.read_info(str(files[0]))
            evidence["rows_read"] = int(info["features"])
        elif fmt == "csv":
            import csv as _csv

            files = list(staging.glob("*.csv"))
            if len(files) != 1:
                raise DeliveryError(dl_contracts.ERR_VERIFY_FAILED,
                                    f"CSV 成员数异常: {len(files)}")
            with open(files[0], encoding="utf-8", newline="") as fh:
                evidence["rows_read"] = sum(1 for _ in _csv.reader(fh)) - 1
        elif fmt == "shapefile_zip":
            import zipfile

            import pyogrio

            files = list(staging.glob("*.zip"))
            if len(files) != 1:
                raise DeliveryError(dl_contracts.ERR_VERIFY_FAILED,
                                    f"ZIP 成员数异常: {len(files)}")
            import tempfile as _tf

            with zipfile.ZipFile(str(files[0])) as zf:
                names = zf.namelist()
                if not any(n.endswith(".shp") for n in names):
                    raise DeliveryError(
                        dl_contracts.ERR_VERIFY_FAILED, "ZIP 缺少 .shp 成员")
                with _tf.TemporaryDirectory() as td:
                    zf.extractall(td)
                    total = 0
                    for shp in sorted(Path(td).glob("*.shp")):
                        info = pyogrio.read_info(str(shp))
                        total += int(info["features"])
                    evidence["rows_read"] = total
                    evidence["layers"] = sorted(
                        p.stem for p in Path(td).glob("*.shp"))
        if expected_rows is not None and evidence["rows_read"] is not None \
                and evidence["rows_read"] != expected_rows:
            raise DeliveryError(
                dl_contracts.ERR_VERIFY_FAILED,
                f"反读行数 {evidence['rows_read']} ≠ 写入 {expected_rows}")
        if crs_meta.get("transformed") and fmt in ("geojson",) and crs_meta.get("target") \
                and "4326" not in (crs_meta["target"] or "").upper():
            raise DeliveryError(dl_contracts.ERR_VERIFY_FAILED,
                                "GeoJSON 变换目标必须是 EPSG:4326")
        return evidence
    except DeliveryError:
        raise
    except Exception as e:
        raise DeliveryError(dl_contracts.ERR_VERIFY_FAILED,
                            f"反读校验失败: {e}") from e


def _finalize_scope_stats(batch_filter: Any) -> None:
    """ids 过滤收尾：回收缺失 ID 统计（缺失披露进 receipt）。"""
    finalize = getattr(batch_filter, "finalize", None)
    if finalize is not None:
        finalize()


def _scope_dict(plan: ScopePlan, stats: Any, rows_written: int,
                snapshot: SourceSnapshot) -> Dict[str, Any]:
    base = {
        "type": plan.type,
        "note": plan.note,
        "subset_of_selection": plan.subset_of_selection,
        "delivered": rows_written,
        "source_rows": snapshot.row_count,
    }
    if stats is not None:
        base["requested"] = stats.requested
        if stats.missing_ids:
            base["missing_ids"] = stats.missing_ids[:50]
            base["missing_ids_count"] = len(stats.missing_ids)
    return base


def _source_refs(spec: DeliverySpec) -> List[str]:
    refs = []
    if spec.source.ref:
        refs.append(spec.source.ref)
    if spec.source.table_ref:
        refs.append(spec.source.table_ref)
    if spec.source.artifact_id:
        refs.append(f"artifact:{spec.source.artifact_id}")
    return refs[:4]


def _receipt_dict(receipt: DeliveryReceipt) -> Dict[str, Any]:
    return receipt.model_dump(exclude_none=False)


def _receipt_summary(receipt: DeliveryReceipt) -> Dict[str, Any]:
    """result_summary 的有界投影（巨型内容绝不入列 —— 规范 §38）。"""
    return {
        "delivery_id": receipt.delivery_id,
        "format": receipt.format,
        "filename": receipt.filename,
        "row_count": receipt.row_count,
        "bytes_total": receipt.bytes_total,
        "content_sha256": receipt.content_sha256,
        "members": [
            {"path": m.path, "role": m.role, "bytes": m.bytes,
             "sha256": m.sha256}
            for m in receipt.members[:32]],
        "losses_applied": receipt.losses_applied[:32],
        "crs": receipt.crs,
        "durable": receipt.durable,
        "data_object_id": receipt.data_object_id,
        "artifact_ref": receipt.artifact_ref,
        "project_artifact_id": receipt.project_artifact_id,
        "scope": {k: receipt.scope.get(k) for k in
                  ("type", "requested", "delivered", "subset_of_selection",
                   "missing_ids_count")},
        "download_url": f"/api/v1/data-delivery/jobs/{{job_id}}/download",
    }


# ── 查询 / 下载 ───────────────────────────────────────────────────────────

def _job_authorize(record: Any, actor: DeliveryActor) -> None:
    """job 行归属：creator / owner_token / 会话 / 项目成员（404 语义不泄漏）。"""
    from app.core.database import SessionLocal
    from app.services.project_service import ProjectService

    if actor.user_id and record.creator_id == actor.user_id:
        return
    if actor.owner_token and record.owner_token == actor.owner_token:
        return
    if record.session_id:
        from app.core.auth import authorize_session_write
        from app.models.db_model import Conversation

        with SessionLocal() as db:
            conv = db.query(Conversation).filter(
                Conversation.id == record.session_id).first()
            if conv is not None and authorize_session_write(
                    conv, actor.user_id, actor.owner_token):
                return
    if record.project_id:
        with SessionLocal() as db:
            if ProjectService.get_project_with_auth(
                    db=db, project_id=record.project_id,
                    user_id=actor.user_id, org_id=actor.org_id):
                return
    raise DeliveryError(dl_contracts.ERR_UNAUTHORIZED, "无权访问该交付任务",
                        status_code=404)


async def get_delivery_job(job_id: str, actor: DeliveryActor) -> DeliveryJobView:
    from fastapi.concurrency import run_in_threadpool

    return await run_in_threadpool(_get_delivery_job_sync, job_id, actor)


def _get_delivery_job_sync(job_id: str, actor: DeliveryActor) -> DeliveryJobView:
    from app.core.database import SessionLocal
    from app.services.jobs.store import DurableJobStore

    with SessionLocal() as db:
        record = DurableJobStore.get_sync(db, job_id)
        if record is None or record.task_type != TASK_TYPE:
            raise DeliveryError(dl_contracts.ERR_SOURCE_NOT_FOUND,
                                "交付任务不存在", status_code=404)
        _job_authorize(record, actor)
        params = record.parameters if isinstance(record.parameters, dict) else {}
        receipt_data = None
        if record.status == "completed":
            delivery_id = params.get("delivery_id") or (
                record.result_summary or {}).get("delivery_id")
            if delivery_id is None and record.result_ref:
                delivery_id = record.result_ref.rsplit("/", 1)[-1]
            if delivery_id:
                receipt_data = dl_store.read_receipt(delivery_id)
        receipt = DeliveryReceipt(**receipt_data) if receipt_data else None
        return DeliveryJobView(
            job_id=str(record.id),
            status=record.status,
            progress=record.progress or 0,
            progress_message=record.progress_message,
            stage=_stage_of(record.status, record.progress_message),
            created_at=record.created_at.isoformat() if record.created_at else None,
            completed_at=record.completed_at.isoformat() if record.completed_at else None,
            spec_fingerprint=params.get("spec_fingerprint"),
            format=params.get("format"),
            filename=params.get("filename"),
            receipt=receipt,
            download_url=(f"/api/v1/data-delivery/jobs/{record.id}/download"
                          if record.status == "completed" else None),
            error_code=_error_code_of(record.error_trace),
            error=record.error_trace[:500] if record.error_trace else None,
        )


def _stage_of(status: str, message: Optional[str]) -> Optional[str]:
    if message:
        return message[:64]
    return status


def _error_code_of(error_trace: Optional[str]) -> Optional[str]:
    if not error_trace:
        return None
    head = error_trace.split(":", 1)[0].strip()
    return head if head.isupper() and 4 <= len(head) <= 48 else None


async def list_delivery_jobs(
    actor: DeliveryActor, project_id: Optional[str] = None, limit: int = 50,
) -> List[DeliveryJobView]:
    from fastapi.concurrency import run_in_threadpool

    return await run_in_threadpool(_list_delivery_jobs_sync, actor, project_id,
                                   limit)


def _list_delivery_jobs_sync(
    actor: DeliveryActor, project_id: Optional[str], limit: int,
) -> List[DeliveryJobView]:
    from sqlalchemy import select as sa_select

    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask

    with SessionLocal() as db:
        stmt = sa_select(AnalysisTask).where(
            AnalysisTask.task_type == TASK_TYPE).order_by(
            AnalysisTask.created_at.desc()).limit(max(1, min(limit, 200)))
        rows = db.execute(stmt).scalars().all()
        views = []
        for record in rows:
            if project_id and record.project_id != project_id:
                continue
            try:
                _job_authorize(record, actor)
            except DeliveryError:
                continue
            views.append(_get_delivery_job_sync(str(record.id), actor))
        return views


async def resolve_download(
    job_id: str, member: Optional[str], actor: DeliveryActor
) -> Tuple[Path, str, str, Optional[str]]:
    """受控下载：授权 → receipt 成员校验 → (路径, MIME, 安全文件名, sha256)。"""
    from fastapi.concurrency import run_in_threadpool

    return await run_in_threadpool(_resolve_download_sync, job_id, member, actor)


def _resolve_download_sync(
    job_id: str, member: Optional[str], actor: DeliveryActor
) -> Tuple[Path, str, str, Optional[str]]:
    from app.core.database import SessionLocal
    from app.services.jobs.store import DurableJobStore

    with SessionLocal() as db:
        record = DurableJobStore.get_sync(db, job_id)
        if record is None or record.task_type != TASK_TYPE:
            raise DeliveryError(dl_contracts.ERR_SOURCE_NOT_FOUND,
                                "交付任务不存在", status_code=404)
        _job_authorize(record, actor)
        if record.status != "completed":
            raise DeliveryError(
                dl_contracts.ERR_SOURCE_NOT_FOUND,
                "交付尚未完成或已失败 —— 没有可下载文件", status_code=404)
        params = record.parameters if isinstance(record.parameters, dict) else {}
        delivery_id = (params.get("delivery_id")
                       or (record.result_summary or {}).get("delivery_id")
                       or (record.result_ref.rsplit("/", 1)[-1]
                           if record.result_ref else None))
        if not delivery_id:
            raise DeliveryError(dl_contracts.ERR_SOURCE_NOT_FOUND,
                                "交付目录不可解析", status_code=404)
    receipt = dl_store.read_receipt(delivery_id)
    if receipt is None:
        raise DeliveryError(
            dl_contracts.ERR_SOURCE_NOT_FOUND,
            "交付回执缺失（durable 内容不可读）", status_code=404)
    members = receipt.get("members") or []
    target = None
    if member:
        # 成员名必须逐字匹配 receipt 清单（basename 域；防路径穿越）
        target = next((m for m in members if m.get("path") == member
                       and "/" not in member and "\\" not in member
                       and ".." not in member), None)
        if target is None:
            raise DeliveryError(dl_contracts.ERR_SOURCE_NOT_FOUND,
                                "成员不在交付清单中", status_code=404)
    else:
        target = next((m for m in members if m.get("role") == "data"), members[0] if members else None)
    if target is None:
        raise DeliveryError(dl_contracts.ERR_SOURCE_NOT_FOUND,
                            "交付无数据成员", status_code=404)
    path = dl_store.final_dir(delivery_id) / str(target.get("path"))
    if not path.is_file():
        raise DeliveryError(
            dl_contracts.ERR_SOURCE_NOT_FOUND,
            "交付文件已不存在（可能被存储清理）", status_code=404)
    fmt = receipt.get("format") or ""
    ext = path.suffix.lower()
    mime = dl_contracts.FORMAT_MIME.get(fmt, "application/octet-stream")
    if ext == ".json":
        mime = "application/json"
    base = dl_contracts.safe_filename(receipt.get("filename") or "delivery")
    download_name = f"{base}{ext}"
    return path, mime, download_name, target.get("sha256")
