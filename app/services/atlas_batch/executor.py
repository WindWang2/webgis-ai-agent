"""Atlas batch executor —— durable 图册 job 的执行体（与 Celery 解耦的纯入口）。

``execute_atlas_job(job_id, job=...)`` 在 ``durable_job`` 上下文内被任务体
调用（tests 直接调用；Celery 装饰在 tasks.py 薄壳）。生命周期：

    载入 job 参数 → committed spec + ref 水合 → 校验 → 规划（页绑定/指纹）
    → 逐页：checkpoint → 指纹页缓存命中? : 编译+注入 → 缓存写入 → status 落盘
    → 任一页失败：证据入库（result_summary 直写）→ typed failed（可重试，
      已过页走缓存不重渲染）
    → QA 门（完整性/顺序/分类一致/范围）→ 组装（忙碌有界退避）→ pypdf 页数复核
    → PDF + manifest 落 exports（属主 sidecar）→ lineage best-effort
    → finish_job(result=页级终态, result_ref=PDF) → 清理工作目录

取消语义：每页边界 ``checkpoint()``；落产物前 ``ensure_not_cancelled()``
（cancelled 不留 status=ready 资产）；取消/失败均清理 attempt 目录（缓存
目录是内容寻址共享产物，不属本 job 临时文件，保留）。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.atlas_job_spec import (
    ATLAS_JOB_SPEC_VERSION,
    AtlasJobSpec,
)
from app.lib.cartography.mapspec_schema import MapSpecSchemaError
from app.services.atlas_batch.manifest import (
    _page_product_manifest_digest,
    build_atlas_manifest,
)
from app.services.atlas_batch.page_compiler import (
    compile_atlas_page,
    diagnostic_dicts,
)
from app.services.atlas_batch.planner import (
    plan_atlas_binding,
)
from app.services.atlas_batch.qa import verify_atlas_pages, verify_pdf_page_count
from app.services.export_paths import ensure_exports_root
from app.services.publication_export import (
    PageCompileFailure,
    PublicationBusyError,
    assemble_pages_pdf,
    count_pdf_pages,
)

logger = logging.getLogger(__name__)

#: WeasyPrint 忙碌退避（attempt 内有界：批页缓存已保住进度，快速失败可重试）。
_ASSEMBLE_BUSY_RETRIES = 3
_ASSEMBLE_BUSY_BACKOFF_S = 5.0

#: 页缓存上限（内容寻址 {fingerprint}.svg；条目/字节双上限 + mtime 逐出）。
_PAGE_CACHE_MAX_ENTRIES = 256
_PAGE_CACHE_MAX_BYTES = 512 * 1024 * 1024


class AtlasJobError(Exception):
    """图册 job 的 typed 失败（code 进 error 单行；页级证据在 result_summary）。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _atomic_write(path: str, data: bytes) -> None:
    """临时文件 + os.replace（与既有导出纪律一致：半文件不可见）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(path), delete=False) as tmp:
        tmp.write(data)
    os.replace(tmp.name, path)


def _write_owner_sidecar(exports_dir: str, filename: str, owner_id: str) -> None:
    """属主 sidecar（格式与 map._set_export_owner 一致——worker 进程无内存
    LRU，持久 sidecar 是下载路由（API 进程）的事实源；fail-closed 语义同源）。"""
    try:
        with open(os.path.join(exports_dir, f"{filename}.owner"), "w", encoding="utf-8") as f:
            f.write(owner_id)
    except OSError as ex:  # noqa: BLE001 — sidecar 失败：下载侧 fail-closed 兜底
        logger.warning("[atlas] owner sidecar write failed file=%s: %s", filename, ex)


def _cache_evict(cache_dir: str) -> None:
    """页缓存容量守卫（条目/字节双上限，mtime 逐出；失败不阻断渲染）。"""
    try:
        entries = [
            (os.path.getmtime(os.path.join(cache_dir, n)), n)
            for n in os.listdir(cache_dir) if n.endswith(".svg")
        ]
    except OSError:
        return
    if len(entries) <= _PAGE_CACHE_MAX_ENTRIES:
        total = sum(os.path.getsize(os.path.join(cache_dir, n)) for _, n in entries) \
            if entries else 0
        if total <= _PAGE_CACHE_MAX_BYTES:
            return
    entries.sort()  # mtime 升序：最旧先逐出
    total = sum(os.path.getsize(os.path.join(cache_dir, n)) for _, n in entries)
    for mtime, name in entries:
        if len(entries) <= _PAGE_CACHE_MAX_ENTRIES and total <= _PAGE_CACHE_MAX_BYTES:
            break
        path = os.path.join(cache_dir, name)
        try:
            size = os.path.getsize(path)
            os.remove(path)
            total -= size
            entries = [(m, n) for m, n in entries if n != name]
        except OSError:
            continue


def _persist_result_summary(job_id: int, payload: Dict[str, Any]) -> None:
    """失败/取消前的页级证据直写（普通列更新，不触碰状态机）。"""
    try:
        from sqlalchemy import update

        from app.models.db_model import AnalysisTask
        from app.tools._utils import db_session

        with db_session() as db:
            db.execute(
                update(AnalysisTask)
                .where(AnalysisTask.id == int(job_id))
                .values(result_summary=payload)
            )
            db.commit()
    except Exception as ex:  # noqa: BLE001 — 证据直写失败不吞原始错误
        logger.warning("[atlas] result_summary persist failed job=%s: %s", job_id, ex)


def _run_async_blocking(coro):
    """loop 无关的阻塞执行（executor 里 async 边界的唯一桥）。

    - 无运行 loop（真实 Celery worker 线程）：``run_sync``（持久 loop 复用）。
    - 运行 loop 内（本地 dev eager 内联执行 / 内联测试）：专用线程 + 新
      loop —— 不嵌套 loop、不阻塞复用主 loop 连接；线程异常原样回抛。
    """
    import asyncio
    import threading

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        from app.core.async_runner import run_sync

        return run_sync(coro)

    out: Dict[str, Any] = {}

    def _runner() -> None:
        try:
            out["value"] = asyncio.run(coro)
        except BaseException as ex:  # noqa: BLE001 — 线程异常面回抛
            out["error"] = ex

    t = threading.Thread(target=_runner, name="atlas-async-bridge", daemon=True)
    t.start()
    t.join()
    if "error" in out:
        raise out["error"]
    return out.get("value")


def _load_job_spec(job_id: int) -> Tuple[AtlasJobSpec, Dict[str, Any]]:
    """job 行 → (AtlasJobSpec, 行元数据 attempt/owner/session)。"""
    from app.services.jobs.store import DurableJobStore
    from app.tools._utils import db_session

    with db_session() as db:
        record = DurableJobStore.get_sync(db, job_id)
        if record is None:
            raise AtlasJobError("atlas_job_not_found", f"job {job_id} 不存在")
        params = record.parameters if isinstance(record.parameters, dict) else {}
        meta = {
            "attempt": int(record.attempt or 1),
            "creator_id": record.creator_id,
            "owner_token": record.owner_token,
            "session_id": record.session_id or params.get("session_id"),
            "display_name": record.display_name or "批量图册导出",
        }
    try:
        spec = AtlasJobSpec(
            spec_version=ATLAS_JOB_SPEC_VERSION,
            session_id=str(params.get("session_id") or ""),
            atlas=params.get("atlas") or {},
            classification_scope=str(params.get("classification_scope") or "shared"),
            target_dpi=int(params.get("target_dpi") or 300),
            title=str(params.get("title") or ""),
            simulated_data=bool(params.get("simulated_data")),
        )
        spec.validate_semantics()
    except MapSpecSchemaError as ex:
        raise AtlasJobError(getattr(ex, "code", "atlas_spec_invalid"), str(ex)) from ex
    except Exception as ex:  # noqa: BLE001 — pydantic 校验面 → typed
        raise AtlasJobError("atlas_spec_invalid", f"job 参数不可解析: {ex}") from ex
    return spec, meta


def _load_committed_document(spec: AtlasJobSpec) -> Tuple[Dict[str, Any], int]:
    """committed spec 加载 + ref 水合 + 出版校验 → (document, mapspec revision)。"""
    from app.services.mapspec_store import mapspec_store
    from app.services.publication_export import (
        hydrate_ref_sources_sync,
        validate_publication_payload,
    )

    async def _load() -> Tuple[Optional[Dict[str, Any]], int]:
        payload = await mapspec_store.get_mapspec(spec.session_id)
        revision = 0
        try:
            from app.services.session_data import session_data_manager

            state = await session_data_manager.get_map_state(spec.session_id)
            revision = int((state or {}).get("_cartographic_mutation_revision") or 0)
        except Exception:  # noqa: BLE001
            revision = 0
        return payload, revision

    payload, revision = _run_async_blocking(_load())
    if not isinstance(payload, dict):
        raise AtlasJobError(
            "atlas_spec_missing",
            f"会话 {spec.session_id} 无已提交的权威 MapSpec（先在地图工作台提交）")
    hydrated = hydrate_ref_sources_sync(payload, spec.session_id)
    try:
        parsed = validate_publication_payload(hydrated)
    except MapSpecSchemaError as ex:
        raise AtlasJobError(getattr(ex, "code", "mapspec_invalid"), str(ex)) from ex
    return parsed.document, revision


def _page_effective_doc_for_manifest(
    document: Dict[str, Any], page: Any, frames_lookup: List[Any],
) -> Dict[str, Any]:
    from app.services.atlas_batch.planner import _page_effective_doc

    return _page_effective_doc(document, page, frames_lookup)


def execute_atlas_job(
    job_id: int,
    *,
    job: Any = None,
) -> Dict[str, Any]:
    """执行图册 job；返回 finish_job 的 result（页级终态汇总）。

    ``job``：``DurableJobHandle``（进度/取消）；tests 可传 None（跳过节流面）。
    typed 失败沿 :class:`AtlasJobError` / ``MapSpecSchemaError`` 上抛——
    任务体负责落终态；失败证据已先行直写 result_summary。
    """
    spec, meta = _load_job_spec(job_id)
    attempt = int(meta["attempt"])
    exports_dir = str(ensure_exports_root())
    work_dir = os.path.join(exports_dir, "atlas_jobs", str(int(job_id)))
    attempt_dir = os.path.join(work_dir, f"attempt-{attempt}")
    cache_dir = os.path.join(exports_dir, "atlas_jobs", "cache")
    os.makedirs(attempt_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    def _progress(pct: int, message: str) -> None:
        if job is not None:
            job.progress(max(0, min(100, pct)), message[:255])

    def _checkpoint() -> None:
        if job is not None:
            job.checkpoint()

    page_records: List[Dict[str, Any]] = []
    try:
        _progress(3, "加载权威 MapSpec")
        document, mapspec_revision = _load_committed_document(spec)
        _progress(6, "规划图册页面")
        plan = plan_atlas_binding(spec, document)

        from app.lib.cartography.publication_ir import enabled_frames

        frames_lookup: List[Any] = []
        if spec.atlas.driver == "frames":
            frames_lookup, _ = enabled_frames(document, cap=spec.atlas.page_budget)

        pages = plan.ir.pages
        total = len(pages)
        status_path = os.path.join(attempt_dir, "status.json")
        cache_hits = 0
        compiled_count = 0

        for idx, (page, binding) in enumerate(zip(pages, plan.bindings)):
            _checkpoint()
            fp = binding.fingerprint
            cache_path = os.path.join(cache_dir, f"{fp}.svg")
            svg: Optional[str] = None
            error: Optional[str] = None
            diagnostics: List[Dict[str, Any]] = []
            page_hit = False
            # 预算记账含缓存命中页（同页集合的总载荷与同步链同口径）
            budget_before = sum(r["svg_bytes"] for r in page_records)
            if os.path.isfile(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        svg = f.read()
                    cache_hits += 1
                    page_hit = True
                except OSError:
                    svg = None
            if svg is None:
                try:
                    outcome = compile_atlas_page(
                        document, page, plan.ir,
                        frames_lookup=frames_lookup,
                        target_dpi=spec.target_dpi,
                        max_labels=400,
                        svg_budget_remaining=96 * 1024 * 1024 - budget_before,
                        atlas_title=spec.atlas.atlas_title,
                    )
                    svg = outcome.svg
                    compiled_count += 1
                    diagnostics = diagnostic_dicts(outcome.frame_diagnostics)
                    _atomic_write(cache_path, svg.encode("utf-8"))
                    _cache_evict(cache_dir)
                except PageCompileFailure as pcf:
                    error = f"{pcf.code}: {pcf.detail}"
            record: Dict[str, Any] = {
                "page_id": binding.page_id,
                "title": binding.title,
                "page_number": binding.page_number,
                "fingerprint": fp,
                "status": "rendered" if error is None else "failed",
                "error": error,
                "svg_bytes": len(svg) if svg else 0,
                "feature_count": binding.feature_count,
                "diagnostics": diagnostics,
            }
            page_records.append(record)
            _progress(6 + int(84 * (idx + 1) / total),
                      f"页 {idx + 1}/{total} · {binding.page_id}"
                      + ("（缓存命中）" if page_hit else ""))
            _atomic_write(status_path, json.dumps(
                {"spec_version": ATLAS_JOB_SPEC_VERSION, "pages": page_records},
                ensure_ascii=False).encode("utf-8"))

        failed = [r for r in page_records if r["status"] != "rendered"]
        if failed:
            summary = {
                "spec_version": ATLAS_JOB_SPEC_VERSION,
                "pages": page_records,
                "page_count_expected": total,
                "page_count_rendered": total - len(failed),
                "failed_page_ids": [r["page_id"] for r in failed][:21],
            }
            _persist_result_summary(int(job_id), summary)
            raise AtlasJobError(
                "atlas_pages_failed",
                f"{len(failed)}/{total} 页编译失败（重试只重渲染失败页）: "
                + "; ".join(f"{r['page_id']}({r['error']})" for r in failed[:3])[:180])

        # QA（组装前门）
        qa_report = verify_atlas_pages(
            plan.bindings,
            [r["page_id"] for r in page_records],
            classification_scope=spec.classification_scope,
            classification_scope_disclosed=True,  # manifest 恒携带可比性披露（构造保证）
        )
        if not qa_report.passed:
            _persist_result_summary(int(job_id), {
                "spec_version": ATLAS_JOB_SPEC_VERSION, "pages": page_records,
                "page_count_expected": total, "page_count_rendered": total,
                "qa": qa_report.model_dump(),
            })
            raise AtlasJobError(
                "atlas_qa_failed",
                "; ".join(f"{v['code']}: {v['detail']}" for v in qa_report.violations[:3])[:200])

        _progress(92, "组装 PDF")
        page_specs: List[Tuple[str, str, float, float]] = []
        for page, record in zip(pages, page_records):
            page_specs.append((
                f"p{page.page_number - 1}",
                # status.json 只存指纹，页体从缓存按指纹取（内存中已有则直用）
                _svg_of(plan, page, record, cache_dir),
                page.paper.width_mm, page.paper.height_mm,
            ))

        from app.lib.cartography.render_diagnostics import DiagnosticSink

        sink = DiagnosticSink()
        pdf_bytes: Optional[bytes] = None
        last_busy: Optional[PublicationBusyError] = None
        for retry in range(_ASSEMBLE_BUSY_RETRIES):
            _checkpoint()
            try:
                pdf_bytes = assemble_pages_pdf(page_specs, title=spec.title, sink=sink)
                break
            except PublicationBusyError as ex:
                last_busy = ex
                time.sleep(_ASSEMBLE_BUSY_BACKOFF_S)
        if pdf_bytes is None:
            raise AtlasJobError(
                "atlas_pdf_busy",
                f"矢量 PDF 渲染器持续占用（重试 {retry + 1} 次仍忙）；页进度已缓存，"
                "请稍后重试本任务（不会重渲染已过页）") from last_busy

        pdf_page_count = count_pdf_pages(pdf_bytes, fallback=len(page_specs))
        qa_report = verify_pdf_page_count(qa_report, pdf_page_count)
        if not qa_report.passed:
            _persist_result_summary(int(job_id), {
                "spec_version": ATLAS_JOB_SPEC_VERSION, "pages": page_records,
                "page_count_expected": total, "page_count_rendered": total,
                "qa": qa_report.model_dump(),
            })
            raise AtlasJobError(
                "atlas_page_count_mismatch",
                f"PDF 实际 {pdf_page_count} 页 vs 计划 {total} 页")

        # 落产物（不可逆副作用前强制取消复核）
        if job is not None:
            job.ensure_not_cancelled()
        _progress(96, "写出图册产物")
        stamp = int(time.time())
        pdf_filename = f"atlas_{job_id}_{stamp}.pdf"
        manifest_filename = f"atlas_{job_id}_{stamp}.manifest.json"
        owner_id = str(meta.get("creator_id") or "anonymous")

        manifest_records = []
        for page, binding, record in zip(pages, plan.bindings, page_records):
            effective = _page_effective_doc_for_manifest(document, page, frames_lookup)
            manifest_records.append({
                **record,
                "product_manifest_digest": _page_product_manifest_digest(
                    effective, record.get("diagnostics") or []),
            })

        from app.services.publication_export import _structural_fingerprint

        notes = ["页头/页码为批量路径注入（同步 vector-pdf 路由不含）"]
        notes.extend(
            f"degradations: {d.get('code')}" for d in plan.ir.degradations[:4])
        manifest = build_atlas_manifest(
            spec=spec, plan=plan, page_records=manifest_records, qa_report=qa_report,
            job_meta={
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "job_id": int(job_id), "attempt": attempt,
                "mapspec_revision": mapspec_revision,
                "spec_fingerprint": _structural_fingerprint(document),
                "notes": notes,
            },
            artifacts={"pdf": pdf_filename, "manifest": manifest_filename},
            simulated_data=bool(spec.simulated_data),
        )
        _atomic_write(os.path.join(exports_dir, pdf_filename), pdf_bytes)
        _atomic_write(
            os.path.join(exports_dir, manifest_filename),
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        _write_owner_sidecar(exports_dir, pdf_filename, owner_id)
        _write_owner_sidecar(exports_dir, manifest_filename, owner_id)

        # lineage best-effort（失败不阻断交付，如实入 result_summary）
        lineage_recorded = False
        try:
            from app.core.async_runner import get_thread_async_engine
            from app.services.export_lineage import record_export_lineage

            async def _lineage() -> bool:
                # 线程隔离引擎（NullPool）：worker 线程持久 loop 与 eager
                # 内联线程各自持引擎，绝不跨 loop 复用连接（#1437 纪律）。
                from sqlalchemy.ext.asyncio import async_sessionmaker

                factory = async_sessionmaker(
                    bind=get_thread_async_engine(), expire_on_commit=False)
                async with factory() as session:
                    info = await record_export_lineage(
                        str(spec.session_id),
                        filename=pdf_filename, ext=".pdf",
                        user_id=owner_id,
                        db=session,
                        owner_token=meta.get("owner_token"),
                        title=spec.title or spec.atlas.atlas_title,
                        vector=True, pages=int(pdf_page_count),
                        target_dpi=int(spec.target_dpi),
                        degradation_codes=[
                            str(d.get("code")) for d in sink.to_payload()
                            if isinstance(d, dict) and d.get("code")
                        ][:16],
                        layout_version=str(plan.ir.ir_version),
                        spec_fingerprint=manifest["inputs"]["spec_fingerprint"],
                    )
                    return bool(info and info.get("artifact_recorded"))

            lineage_recorded = bool(_run_async_blocking(_lineage()))
        except Exception as ex:  # noqa: BLE001 — 增值披露，绝不阻断交付
            logger.warning("[atlas] lineage record failed job=%s: %s", job_id, ex)

        result: Dict[str, Any] = {
            "spec_version": ATLAS_JOB_SPEC_VERSION,
            "plan_fingerprint": plan.ir_fingerprint,
            "pages": [
                {k: r.get(k) for k in
                 ("page_id", "title", "page_number", "status", "error", "feature_count")}
                for r in page_records
            ],
            "page_count_expected": total,
            "page_count_rendered": len(page_records),
            "page_count_pdf": int(pdf_page_count),
            "qa": qa_report.model_dump(),
            "cache_hits": cache_hits,
            "compiled_pages": compiled_count,
            "artifacts": {"pdf": pdf_filename, "manifest": manifest_filename},
            "pdf_url": f"/api/v1/export/download/{pdf_filename}",
            "manifest_url": f"/api/v1/export/download/{manifest_filename}",
            "lineage_recorded": lineage_recorded,
            "simulated_data": bool(spec.simulated_data),
        }
        _progress(99, f"图册完成：{total} 页")
        return result
    finally:
        # 取消/失败/成功统一清理本 job 工作目录（页级证据已在 DB/缓存）。
        shutil.rmtree(work_dir, ignore_errors=True)


def _svg_of(plan: Any, page: Any, record: Dict[str, Any], cache_dir: str) -> str:
    """组装期按指纹从缓存取页体（executor 主循环后 svg 内存不再持有）。"""
    fp = str(record.get("fingerprint") or "")
    path = os.path.join(cache_dir, f"{fp}.svg")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


__all__ = ["AtlasJobError", "execute_atlas_job"]
