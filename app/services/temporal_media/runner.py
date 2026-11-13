"""WP07 — 持久任务执行体（阶段/进度/取消/恢复）。

复用 G23 durable job 的 claim、attempt fencing、heartbeat 与取消；本模块
只投影本领域阶段。检查点缓存由 ``owner+plan+source+frame+renderer`` 指纹确定，
只复用可读取且 checksum 正确的帧；取消后杀后续帧并清理临时产物，
迟到成功不得覆盖 cancelled。
"""
from __future__ import annotations

import asyncio
import shutil
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .contracts import MAX_TEMP_BYTES, TemporalMediaError
from .encode import build_encoded_frames, encode_media
from .manifest import build_manifest, package_evidence, publish_manifest
from .render import build_compile_doc, render_digital_frame
from .store import (
    cache_dir, cached_frame, frame_key, is_cancelled, job_dir, load_plan,
    put_cached_frame, update_state,
)
from .verify import verify_delivery


def _run_async(coro: Any) -> Any:
    """同步 worker 中安全执行 async 复检（不复用别人的事件循环）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    result: Dict[str, Any] = {}

    def _worker() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=30)
    if "error" in result:
        raise result["error"]
    if "value" not in result:
        raise TemporalMediaError("RECHECK_TIMEOUT", "freeze recheck timed out",
                                 status_code=504)
    return result["value"]


class _Cancel(Exception):
    """内部取消信号（不写终态，交给 durable_job 框架收敛）。"""


def execute_media_job(
    principal: str, job_id: str, *,
    job_handle: Any = None,
    external_cancel: Optional[Callable[[], bool]] = None,
    external_progress: Optional[Callable[..., None]] = None,
    recheck: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """同步执行一条完整媒体任务；返回有界回执，绝不返回媒体字节。"""
    plan = load_plan(principal, job_id)
    root = job_dir(principal, job_id)
    work = root / "work"
    frames_dir = work / "frames"
    svgs_dir = work / "svg"
    media_dir = work / "media"
    for directory in (frames_dir, svgs_dir, media_dir):
        directory.mkdir(parents=True, exist_ok=True)

    def cancelled() -> bool:
        if external_cancel is not None and external_cancel():
            return True
        if is_cancelled(principal, job_id):
            return True
        if job_handle is not None:
            try:
                job_handle.ensure_not_cancelled()
            except Exception as exc:  # noqa: BLE001 — 框架 OperationCancelled
                if "cancel" in type(exc).__name__.lower():
                    return True
                raise
        return False

    def publish_state(mutator: Callable[[Dict[str, Any]], None]) -> None:
        result = update_state(principal, job_id, mutator)
        if external_progress is not None and result.get("progress") is not None:
            try:
                external_progress(int(result["progress"]), str(result["message"]),
                                  phase=str(result["phase"]),
                                  current_step=int(result.get("current_frame") or 0),
                                  total_steps=int(result.get("total_frames") or 0))
            except Exception:  # noqa: BLE001 — 进度上报绝不阻断交付
                pass

    try:
        if cancelled():
            raise _Cancel()
        publish_state(lambda s: _phase_mutate(
            s, "freezing", 2, "冻结源、表达、地理窗口与渲染器"))
        if recheck is None:
            from .freeze import recheck_freeze

            def _default_recheck() -> None:
                _run_async(recheck_freeze(plan["session_id"], plan["frozen"]))

            recheck = _default_recheck
        recheck()

        compile_doc, omitted = build_compile_doc(plan)
        held_windows = {str(o["id"]): {"start": str(o["start"]),
                                       "end": str(o["end"])}
                        for o in plan["observations"]}
        segments = plan["display"]["segments"]
        total_unique = int(plan["resource_estimate"]["unique_digital_frames"])
        digital_by_segment: List[Optional[Dict[str, Any]]] = [None] * len(segments)
        rendered: Dict[str, Dict[str, Any]] = {}
        cache_hits = 0
        rendered_count = 0
        # 运行时磁盘配额：按真实写入字节累计，而非只用计划期估算。
        written_bytes = 0

        for position, segment in enumerate(segments):
            if cancelled():
                raise _Cancel()
            key = frame_key(plan, segment)
            if key in rendered:
                digital_by_segment[int(segment["index"])] = rendered[key]
                continue
            # SVG 证据与 PNG 一起放进**共享**有界帧缓存（按 owner 隔离），
            # 不能放在 job 私有目录：worker 重启后新域任务也必须能复用。
            svg_cache = cache_dir(principal)
            hit = cached_frame(principal, key)
            if hit is not None:
                png_path, evidence = hit
                svg_src = svg_cache / f"{key}.svg"
                if svg_src.is_file():
                    # 缓存命中仍鉴权：key 已含 owner，且必须能读回真实字节。
                    target = frames_dir / f"{key}.png"
                    target.write_bytes(png_path.read_bytes())
                    svgs_dir.joinpath(f"{key}.svg").write_text(
                        svg_src.read_text(encoding="utf-8"), encoding="utf-8")
                    cache_hits += 1
                else:
                    hit = None
            if hit is None:
                evidence = render_digital_frame(
                    plan, segment, compile_doc=compile_doc,
                    omitted_layers=omitted, frames_dir=frames_dir,
                    svgs_dir=svgs_dir, held_windows=held_windows,
                    cancel=cancelled)
                put_cached_frame(principal, key,
                                 (frames_dir / f"{key}.png").read_bytes(), evidence)
                svg_cache.mkdir(parents=True, exist_ok=True)
                svg_cache.joinpath(f"{key}.svg").write_text(
                    (svgs_dir / f"{key}.svg").read_text(encoding="utf-8"),
                    encoding="utf-8")
                rendered_count += 1
            png_work = frames_dir / f"{key}.png"
            svg_work = svgs_dir / f"{key}.svg"
            written_bytes += (png_work.stat().st_size if png_work.is_file() else 0)
            written_bytes += (svg_work.stat().st_size if svg_work.is_file() else 0)
            if written_bytes > MAX_TEMP_BYTES:
                raise TemporalMediaError(
                    "RESOURCE_BUDGET_EXCEEDED",
                    f"temporary frame bytes {written_bytes} exceed runtime cap "
                    f"{MAX_TEMP_BYTES}; aborting before encoding",
                    status_code=413)
            rendered[key] = evidence
            digital_by_segment[int(segment["index"])] = evidence
            pct = 5 + int(65 * (position + 1) / max(1, len(segments)))
            publish_state(lambda s, p=pct, u=len(rendered), c=cache_hits,
                          r=rendered_count, i=position: _phase_mutate(
                              s, "rendering", p,
                              f"逐帧渲染 {i + 1}/{len(segments)}（唯一数字帧 {u}）",
                              current_frame=i + 1, rendered_frames=r,
                              cache_hits=c, total_frames=total_unique))
        digital: List[Dict[str, Any]] = [d for d in digital_by_segment if d]
        if len(digital) != len(segments):
            raise TemporalMediaError("DIGITAL_FRAMES_INCOMPLETE",
                                     "not every display segment has a digital frame",
                                     status_code=502)

        if cancelled():
            raise _Cancel()
        publish_state(lambda s: _phase_mutate(s, "encoding", 72, "有界动画编码"))
        encoded = build_encoded_frames(plan, digital)
        media = encode_media(plan, encoded, frames_dir, media_dir,
                             cancel=cancelled)
        written_bytes += int(media["bytes"])
        if written_bytes > MAX_TEMP_BYTES:
            raise TemporalMediaError(
                "RESOURCE_BUDGET_EXCEEDED",
                f"temporary bytes {written_bytes} (frames + container) exceed "
                f"runtime cap {MAX_TEMP_BYTES}",
                status_code=413)
        # GIF writer 会合并相邻相同 P 帧；核验/manifest 必须用**实际容器帧映射**。
        effective = media.get("effective_encoded") or encoded

        if cancelled():
            raise _Cancel()
        publish_state(lambda s, e=len(effective): _phase_mutate(
            s, "verifying", 84, "独立解码与内容 oracle", encoded_frames=e))
        verification = verify_delivery(plan, effective, media, digital,
                                       frames_dir, svgs_dir, cancel=cancelled)
        if not verification["passed"]:
            raise TemporalMediaError(
                "VERIFICATION_FAILED",
                "independent decode/content oracle failed; media not published",
                status_code=502,
                details={"failures": verification["failures"][:12]})

        # WP04：发布前复检源修订/文档指纹，中途变化不发布混合 revision 作品。
        recheck()
        if cancelled():
            raise _Cancel()
        publish_state(lambda s: _phase_mutate(s, "publishing", 92, "原子发布与授权下载"))
        manifest = build_manifest(plan, effective, media, verification, digital)
        evidence_zip = package_evidence(manifest, frames_dir, svgs_dir)
        artifacts = publish_manifest(plan, principal, job_id, manifest,
                                     media, evidence_zip)

        def finalize(state: Dict[str, Any]) -> None:
            state["status"] = "completed"
            state["phase"] = "completed"
            state["progress"] = 100
            state["message"] = "动画与逐帧证据已独立核验并授权发布"
            state["artifacts"] = artifacts
            state["verification"] = {
                "passed": True,
                "decoded_frames": verification["decoded_frames"],
                "checks_count": len(verification["checks"]),
            }
            state["error"] = None
        final = update_state(principal, job_id, finalize)
        if final.get("status") != "completed":
            return {"status": "cancelled", "job_id": job_id,
                    "message": "取消终态保持，迟到成果未登记"}
        return {
            "status": "completed",
            "job_id": job_id,
            "plan_hash": plan["plan_hash"],
            "artifacts": artifacts,
            "verification": final["verification"],
            "resources": {
                "unique_rendered_frames": rendered_count,
                "cache_hits": cache_hits,
                "encoded_frames": len(effective),
                "container_bytes": media["bytes"],
                "total_duration_ms": verification["total_duration_ms"],
            },
        }
    except _Cancel:
        _cleanup(work)
        return {"status": "cancelled", "job_id": job_id,
                "message": "媒体任务已取消：后续帧与编码停止，临时文件已清理"}
    except TemporalMediaError as exc:
        _cleanup(work)
        # 异常变量在 except 结束即被清除；先取值再进 lambda（否则迟到调用未定义）。
        err_payload, err_message = exc.to_payload(), exc.message
        update_state(principal, job_id, lambda s: s.update({
            "status": "failed", "phase": "failed", "progress": None,
            "message": err_message, "error": err_payload,
        }))
        raise
    except Exception as exc:  # noqa: BLE001 — 未知失败同样不标 completed
        _cleanup(work)
        err_type = type(exc).__name__
        update_state(principal, job_id, lambda s: s.update({
            "status": "failed", "phase": "failed", "progress": None,
            "message": "unexpected media job failure",
            "error": {"code": "UNEXPECTED_MEDIA_FAILURE", "message": err_type},
        }))
        raise
    finally:
        _cleanup(work)


def _phase_mutate(state: Dict[str, Any], phase: str, progress: int,
                  message: str, **fields: Any) -> None:
    state["phase"] = phase
    state["status"] = "running"
    state["progress"] = int(progress)
    state["message"] = message
    for key, value in fields.items():
        state[key] = value


def _cleanup(work: Path) -> None:
    """只清理本任务登记的临时目录；缓存与已发布产物不动。"""
    shutil.rmtree(work, ignore_errors=True)


__all__ = ["execute_media_job"]
