"""G30 runner —— prepared call 的真实执行（durable job + 统一派发）。

执行语义（与 G23 durable 范式一致，不另建执行权威）：

- **幂等提交**：同 prepared 内容（同 owner）= 同 idempotency key = 同一行
  durable job —— 双击/网络重试不重复执行必要副作用；
- **活跃去重**：既有行仍活跃 → 返回 already_running 视图，不补启动第二
  个执行体；终态 failed/cancelled → 释放旧键新建行（重试 = 显式新执行）；
- **start 期复核**：技能资产 digest 漂移 → SKILL_ASSET_DRIFT typed 拒绝；
  输入 ref 内容 digest 漂移 → INPUT_DRIFT typed 拒绝（重新准备）；权限/
  资格复核由每步 dispatch 的既有闸执行（prepare 授权不缓存到执行）；
- **逐步统一派发**：每个具体工具调用经 ToolDispatchService（同一
  owner/scope/side-effect 闸）；步骤结果经 ``apply_tool_result`` 喂
  SessionPlan（既有步骤真相；headless 会话无计划时 best-effort 跳过）；
- **Pi 回调上下文**：start 绑定当前 turn/tool_call（JobOrigin 关联），
  有界等待执行体完成 —— 不开新 turn、不递归 prompt、不另持 writer；
- **取消**：既有 durable 两阶段取消（DB cancel_requested_at → 看门狗 →
  协作检查点）—— 步间检查，已提交合法结果不虚假回滚。

run 记录（result_summary，有界）：步骤分支/状态/产物 refs/证据覆盖/
未覆盖义务/降级披露 —— 只保存技能选择与绑定出处快照，步骤状态真相仍在
SessionPlan / durable job。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.gis_harness.skill_jobs.contracts import (
    PreparedSkillCall,
    SkillJobErrorCode,
)

logger = logging.getLogger(__name__)

#: 运行期 typed 拒绝码（prepare 词表之外）。
RUN_ERROR_CODES = (
    "SKILL_ASSET_DRIFT",      # 同版本资产内容被替换（digest 不匹配）
    "INPUT_DRIFT",            # 输入 ref 内容与 prepare 时不一致
    "SESSION_BUSY",           # UI 启动时会话有活跃 Pi turn（busy，不自等待）
    "PREPARED_EXPIRED",       # prepared_id 过期/不存在（重新准备）
    "PREPARED_SESSION_MISMATCH",
    "STEP_FAILED",            # 必需步骤失败（run 内步状态见 result_summary）
)

#: start 有界等待默认（Pi 回调内等结果；超时=仍在运行，轮询 status）。
DEFAULT_WAIT_TIMEOUT_S = 45.0

#: 单步结果的内联投影上限（大 geojson 只留 ref）。
_STEP_RESULT_INLINE_KEYS = 12


@dataclass
class SkillJobOwner:
    """start 请求的归属上下文（权限/关联链）。"""
    session_id: str = ""
    user_id: str = ""
    org_id: Optional[int] = None
    project_id: str = ""
    owner_token: str = ""
    turn_id: str = ""          # Pi 回调内启动时绑定当前 turn
    tool_call_id: str = ""


@dataclass
class _RunState:
    """进程内运行簿记（job 完成事件供有界等待；跨刷新不依赖它）。"""
    events: Dict[int, asyncio.Event] = field(default_factory=dict)
    tasks: Dict[int, asyncio.Task] = field(default_factory=dict)


_run_state = _RunState()


def _record_step_event(event: str, *, skill_id: str, skill_version: str,
                       step_id: str = "", run_job_id: Optional[int] = None) -> None:
    """把 ADR-0220 记录的零调用 step/skill 事件接入生产（fail-open）。"""
    try:
        from app.services.gis_harness.skills.evidence import SkillEvidenceRecord
        from app.tools.skill_library_tools import get_skill_evidence_recorder

        get_skill_evidence_recorder().record(SkillEvidenceRecord(
            event=event,
            skill_id=skill_id,
            skill_version=skill_version,
            step_id=step_id or "",
            notes=(f"job:{run_job_id}" if run_job_id else "")[:200],
        ))
    except Exception:  # noqa: BLE001 — 证据面绝不阻断执行
        logger.debug("[skill-job] evidence record skipped: %s", event, exc_info=True)


async def _resolve_role_data(
    prepared: PreparedSkillCall,
    inline_payloads: Dict[str, Any],
) -> Dict[str, Any]:
    """start 期数据解析：inline 落会话存储；ref 重读 + digest 复核。

    返回 role → 实际数据（工具实参用）。任何漂移 → typed INPUT_DRIFT。
    """
    from app.services.session_data import session_data_manager
    from app.services.gis_harness.skill_jobs.prepare import extract_binding_facts

    role_data: Dict[str, Any] = {}
    for b in prepared.bindings:
        if b.inline:
            payload = inline_payloads.get(b.role)
            if payload is None:
                raise SkillJobErrorCode(
                    "INPUT_DRIFT",
                    f"角色 {b.role} 的内联数据在 start 时缺席（重新准备）")
            facts, err = extract_binding_facts(payload)
            if err or facts.content_digest != b.facts.content_digest:
                raise SkillJobErrorCode(
                    "INPUT_DRIFT",
                    f"角色 {b.role} 的内联数据与 prepare 时不一致（重新准备）")
            # inline 数据 start 时落会话存储（prepare 零副作用；此处是
            # start 的合法副作用）—— 铸造的 ref 记入运行时绑定视图。
            minted_ref = await session_data_manager.store(
                prepared.session_id, payload, prefix="geojson")
            b.ref = minted_ref
            b.inline = False
            role_data[b.role] = payload
        else:
            data = await session_data_manager.get(prepared.session_id, b.ref)
            if data is None:
                raise SkillJobErrorCode(
                    "INPUT_DRIFT",
                    f"角色 {b.role} 的数据 ref {b.ref} 启动时不可读（过期）—— 重新绑定")
            facts, err = extract_binding_facts(data)
            if err or facts.content_digest != b.facts.content_digest:
                raise SkillJobErrorCode(
                    "INPUT_DRIFT",
                    f"角色 {b.role} 的数据 ref {b.ref} 内容已变化（版本竞争）—— 重新准备",
                    detail={"role": b.role, "ref": b.ref})
            role_data[b.role] = data
    return role_data


def _verify_asset(prepared: PreparedSkillCall) -> None:
    from app.services.gis_harness.skills.loader import get_skill_library

    skill = get_skill_library().get(prepared.skill_id)
    if skill is None or skill.version != prepared.skill_version:
        raise SkillJobErrorCode(
            "SKILL_ASSET_DRIFT",
            f"技能 {prepared.skill_id} 版本资产在 start 时已变化 —— 重新准备")
    if skill.fingerprint() != prepared.skill_digest:
        raise SkillJobErrorCode(
            "SKILL_ASSET_DRIFT",
            f"技能 {prepared.skill_id} v{prepared.skill_version} 同版本内容被替换"
            "（digest 漂移）—— 旧准备失效，请重新准备",
            detail={"prepared_digest": prepared.skill_digest[:16],
                    "current_digest": skill.fingerprint()[:16]})


def _materialize_args(args: Dict[str, Any], role_data: Dict[str, Any]) -> Dict[str, Any]:
    """prepared step args → 具体实参（$role 标记 → 实际数据）。"""

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            if set(node.keys()) == {"$role"}:
                role = node["$role"]
                if role not in role_data:
                    raise SkillJobErrorCode(
                        "INPUT_DRIFT", f"步骤实参引用未解析角色 {role}")
                return role_data[role]
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v) for v in node]
        return node

    return {k: _walk(v) for k, v in args.items()}


def _bounded_tool_result(result: Any) -> Dict[str, Any]:
    """单步结果的有界投影（大 geojson 只留关键事实；ref 保留）。"""
    if not isinstance(result, dict):
        return {"value_preview": str(result)[:120]}
    out: Dict[str, Any] = {}
    for i, (k, v) in enumerate(result.items()):
        if i >= _STEP_RESULT_INLINE_KEYS:
            out["_truncated"] = True
            break
        if isinstance(v, dict) and str(v.get("type")) == "FeatureCollection":
            out[k] = {"type": "FeatureCollection",
                      "feature_count": len(v.get("features") or [])}
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, list):
            out[k] = {"list_len": len(v)}
        else:
            out[k] = {"dict_keys": sorted(v.keys())[:8] if isinstance(v, dict) else type(v).__name__}
    return out


async def _execute_steps(
    handle: Any,
    prepared: PreparedSkillCall,
    role_data: Dict[str, Any],
    owner: SkillJobOwner,
) -> Dict[str, Any]:
    """执行体：逐步统一派发 + 步间协作取消检查 + run 记录编译。"""
    from app.agent_pi_bridge import _get_shared_dispatch_service
    from app.services.session_data import session_data_manager

    executed: set = set()
    step_records: List[Dict[str, Any]] = []
    evidence_covered: set = set()
    artifact_refs: Dict[str, str] = {}
    primary_result_ref = ""
    skill_id, skill_version = prepared.skill_id, prepared.skill_version

    total = len(prepared.steps)
    for idx, step in enumerate(prepared.steps):
        handle.checkpoint()
        _record_step_event("step_started", skill_id=skill_id,
                           skill_version=skill_version, step_id=step.step_id,
                           run_job_id=handle.job_id)
        await handle.progress(
            int(idx * 100 / max(total, 1)), f"step {step.step_id}")
        args = _materialize_args(step.args, role_data)
        # webgis_layer_upsert 等工具吃显式 session_id；统一补上（幂等：工具
        # 自行忽略多余键时无副作用，args_model 会校验）。
        tc = {
            "id": f"skilljob-{prepared.prepared_id}-{idx}",
            "function": {"name": step.tool, "arguments": args},
        }
        service = _get_shared_dispatch_service()
        dispatch_result = await service.dispatch(
            tc, prepared.session_id, executed)
        dispatch_status = str(getattr(dispatch_result, "status", "") or "")
        # review P2-9：dedup 命中（repeated）是幂等成功语义，不是步骤失败。
        success = dispatch_status in ("ok", "repeated")
        raw = getattr(dispatch_result, "raw_result", None)
        llm_payload = getattr(dispatch_result, "llm_payload", None)
        geojson_ref = getattr(dispatch_result, "geojson_ref", None) or ""

        # SessionPlan 步骤真相（E8 锚点）：统一派发结果喂既有入口
        # （headless 会话无计划 → best-effort 跳过；Pi 会话推进当前计划）。
        try:
            from app.services.session_plan import apply_tool_result

            await apply_tool_result(
                prepared.session_id, step.tool,
                raw if isinstance(raw, dict) else {"success": bool(success)},
                success=success, geojson_ref=geojson_ref or None)
        except Exception:  # noqa: BLE001 — 计划记账绝不阻断执行体
            logger.debug("[skill-job] apply_tool_result skipped step=%s",
                         step.step_id, exc_info=True)

        step_record: Dict[str, Any] = {
            "step_id": step.step_id,
            "tool": step.tool,
            "status": dispatch_status if success else "failed",
            "required": step.required,
        }
        if geojson_ref:
            step_record["geojson_ref"] = geojson_ref
            artifact_refs[f"step:{step.step_id}:geojson"] = geojson_ref
        if isinstance(raw, dict):
            for key in ("layer_id", "result_ref", "data_ref"):
                v = raw.get(key)
                if isinstance(v, str) and v:
                    step_record[key] = v
                    artifact_refs[f"step:{step.step_id}:{key}"] = v
                    if not primary_result_ref:
                        primary_result_ref = v
        step_record["result_preview"] = _bounded_tool_result(
            raw if isinstance(raw, dict) else llm_payload)
        step_records.append(step_record)
        evidence_covered.update(step.produces_evidence)
        _record_step_event(
            "step_completed" if success else "step_skipped",
            skill_id=skill_id, skill_version=skill_version,
            step_id=step.step_id, run_job_id=handle.job_id)

        if not success:
            if step.required:
                error_msg = ""
                if isinstance(raw, dict):
                    error_msg = str(raw.get("message") or raw.get("error") or "")[:200]
                raise SkillJobErrorCode(
                    "STEP_FAILED",
                    f"必需步骤 {step.step_id}（{step.tool}）失败：{error_msg or '见步骤记录'}",
                    detail={"step_id": step.step_id, "tool": step.tool,
                            "step_index": idx})
            step_record["status"] = "skipped_optional_failed"
            step_record["note"] = "可选步骤失败，按技能降级语义继续"

    # ── run 记录编译（分型结果；报告/地图各自完成证据）────────────────
    from app.services.gis_harness.skills.loader import get_skill_library
    skill = get_skill_library().get(skill_id)
    completion_evidence = list(skill.completion_evidence) if skill else []
    uncovered = [e for e in completion_evidence if e not in evidence_covered]

    if prepared.result_kind == "report" and step_records:
        # 报告型：最后一步的 bounded 结果作为报告 ref 落会话存储（可回读）
        report_payload = {
            "skill_id": skill_id,
            "skill_version": skill_version,
            "skill_digest": prepared.skill_digest[:16],
            "params": [p.model_dump() for p in prepared.params],
            "bindings": [
                {"role": b.role, "ref": b.ref, "digest": b.facts.content_digest[:16]}
                for b in prepared.bindings
            ],
            "steps": step_records,
            "evidence_covered": sorted(evidence_covered),
            "evidence_not_covered": uncovered,
        }
        report_ref = await session_data_manager.store(
            prepared.session_id, report_payload, prefix="skillreport")
        artifact_refs["report"] = report_ref
        primary_result_ref = primary_result_ref or report_ref

    run_record: Dict[str, Any] = {
        "schema_version": "skilljob.run/v1",
        "skill": {"id": skill_id, "version": skill_version,
                  "digest": prepared.skill_digest[:16],
                  "result_kind": prepared.result_kind},
        "prepared_id": prepared.prepared_id,
        "idempotency_key": prepared.idempotency_key,
        "params": [p.model_dump() for p in prepared.params],
        "bindings": [
            {"role": b.role, "ref": b.ref, "inline": b.inline,
             "digest": b.facts.content_digest[:16],
             "geometry_kind": b.facts.geometry_kind,
             "feature_count": b.facts.feature_count}
            for b in prepared.bindings
        ],
        "steps": step_records,
        "evidence_covered": sorted(evidence_covered & set(completion_evidence)),
        "evidence_not_covered": uncovered,
        "obligations_note": (
            "未覆盖义务如实保留（partial/blocked 语义），不以步骤标题冒充完成证据"),
        "artifact_refs": artifact_refs,
        "observation_pending": prepared.result_kind == "map",
        "turn_id": owner.turn_id,
    }
    _record_step_event("skill_completed", skill_id=skill_id,
                       skill_version=skill_version, run_job_id=handle.job_id)
    return {"run_record": run_record, "result_ref": primary_result_ref}


async def start_skill_job(
    prepared: PreparedSkillCall,
    inline_payloads: Dict[str, Any],
    *,
    owner: SkillJobOwner,
    wait_timeout_s: Optional[float] = DEFAULT_WAIT_TIMEOUT_S,
    session_busy_check: bool = False,
) -> Dict[str, Any]:
    """启动（幂等）执行；返回有界运行视图。

    ``wait_timeout_s``：>0 有界等待执行体（Pi 回调闭环）；0/None 立即返回
    （UI 后台运行 + 轮询）。``session_busy_check``：UI 入口传 True —— 会话
    有活跃 Pi turn 时返回 busy（不自等待、不另起执行权威）。
    """
    _verify_asset(prepared)

    # review P2-7：busy 检查先于数据解析 —— busy 拒绝不应产生任何副作用
    # （否则 inline 绑定会先铸造孤儿 ref）。
    if session_busy_check:
        try:
            from app.agent_pi_bridge import get_active_turn_entry

            if get_active_turn_entry(prepared.session_id) is not None:
                raise SkillJobErrorCode(
                    "SESSION_BUSY",
                    "会话已有活跃 agent turn：作业启动须等待其完成（busy）——"
                    "不另起执行权威",
                    detail={"session_id": prepared.session_id})
        except SkillJobErrorCode:
            raise
        except Exception:  # noqa: BLE001 — 活跃判定失败不阻断（按空闲处理）
            logger.debug("[skill-job] active-turn check unavailable", exc_info=True)

    role_data = await _resolve_role_data(prepared, inline_payloads)

    from app.services.data_operations.runner import (
        run_async_job,
        submit_async_job,
    )
    from app.services.jobs.lifecycle import JobStatus

    submit_params = {
        "skill_id": prepared.skill_id,
        "skill_version": prepared.skill_version,
        "result_kind": prepared.result_kind,
    }
    job_id, status, reused = submit_async_job(
        task_type=f"skill_job:{prepared.skill_id}",
        display_name=f"技能作业 {prepared.skill_id}",
        params=submit_params,
        idempotency_key=prepared.idempotency_key,
        kind="skill_job",
        owner_id=owner.user_id or None,
        owner_token=owner.owner_token or None,
        org_id=owner.org_id,
        session_id=prepared.session_id,
        project_id=owner.project_id or None,
    )
    if reused:
        view = _job_view_sync(job_id)
        if view is not None:
            view.setdefault("idempotent_reuse", True)
            if str(view.get("status")) in (
                    JobStatus.pending.value, JobStatus.queued.value,
                    JobStatus.running.value, JobStatus.cancelling.value):
                view["already_running"] = True
            return view
        # 行不可读（极端）：按新建继续（下方执行体会因认领失败退出）

    # dispatch_spec 落全量 prepared 快照（重跑描述符；绝不经 API 返回）
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from sqlalchemy import select

    with contextlib.closing(SessionLocal()) as db:
        row = db.execute(
            select(AnalysisTask).where(AnalysisTask.id == job_id)).scalar_one_or_none()
        if row is not None and not reused:
            row.dispatch_spec = {
                "prepared": prepared.model_dump(),
                "owner": {
                    "session_id": owner.session_id,
                    "user_id": owner.user_id,
                    "org_id": owner.org_id,
                    "project_id": owner.project_id,
                    "turn_id": owner.turn_id,
                    "tool_call_id": owner.tool_call_id,
                },
            }
            db.commit()

    done = asyncio.Event()
    _run_state.events[job_id] = done

    async def _body(handle: Any) -> Dict[str, Any]:
        return await _execute_steps(handle, prepared, role_data, owner)

    async def _runner() -> None:
        try:
            await run_async_job(
                job_id, _body,
                result_ref_builder=lambda r: str(
                    (r or {}).get("result_ref") or "")[:512] or None)
        except Exception:  # noqa: BLE001 — run_async_job 已落 failed 终态
            logger.warning("[skill-job] job %s failed", job_id, exc_info=True)
        finally:
            ev = _run_state.events.pop(job_id, None)
            _run_state.tasks.pop(job_id, None)
            if ev is not None:
                ev.set()

    task = asyncio.create_task(_runner())
    _run_state.tasks[job_id] = task

    if wait_timeout_s and wait_timeout_s > 0:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(done.wait(), timeout=wait_timeout_s)

    view = _job_view_sync(job_id) or {"job_id": str(job_id), "status": "running"}
    view["job_id"] = str(job_id)
    return view


def _job_view_sync(job_id: int) -> Optional[Dict[str, Any]]:
    """从 durable job 行构造有界运行视图（刷新回看的权威来源）。"""
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from app.services.jobs.lifecycle import coerce_status, is_active, is_cancellable, is_retryable_status
    from sqlalchemy import select

    with contextlib.closing(SessionLocal()) as db:
        row = db.execute(
            select(AnalysisTask).where(AnalysisTask.id == job_id)).scalar_one_or_none()
        if row is None:
            return None
        status = coerce_status(row.status or "pending")
        view: Dict[str, Any] = {
            "job_id": str(row.id),
            "kind": "skill_job",
            "status": status.value,
            "progress": row.progress,
            "message": (row.progress_message or "")[:200],
            "cancellable": is_cancellable(status),
            "retryable": is_retryable_status(status),
            "active": is_active(status),
            "attempt": row.attempt or 1,
            "session_id": row.session_id,
            "project_id": row.project_id,
            "error": (row.error_trace or "").splitlines()[-1][:200] if row.error_trace else None,
            "result_ref": row.result_ref,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
        summary = row.result_summary if isinstance(row.result_summary, dict) else {}
        run_record = summary.get("run_record") if isinstance(summary.get("run_record"), dict) else None
        if run_record is None:
            # run_async_job 把 body 返回值整体落 result_summary —— run 记录
            # 直接位于顶层（run_record 键）或整体即 run_record 形状。
            if summary.get("schema_version") == "skilljob.run/v1":
                run_record = summary
        if run_record is not None:
            view["run_record"] = run_record
            view["skill"] = run_record.get("skill") or {}
            view["evidence_covered"] = run_record.get("evidence_covered", [])
            view["evidence_not_covered"] = run_record.get("evidence_not_covered", [])
            view["artifact_refs"] = run_record.get("artifact_refs", {})
            view["steps"] = run_record.get("steps", [])
        params = row.parameters if isinstance(row.parameters, dict) else {}
        view["skill_id"] = params.get("skill_id", "")
        view["skill_version"] = params.get("skill_version", "")
        return view


async def get_skill_job_view(job_id: int) -> Optional[Dict[str, Any]]:
    return _job_view_sync(job_id)


async def list_skill_job_views(
    *, session_id: str, limit: int = 20,
) -> List[Dict[str, Any]]:
    """会话的技能作业运行列表（刷新回看；有界）。"""
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from sqlalchemy import select

    with contextlib.closing(SessionLocal()) as db:
        rows = db.execute(
            select(AnalysisTask.id)
            .where(AnalysisTask.session_id == session_id,
                   AnalysisTask.job_kind == "skill_job")
            .order_by(AnalysisTask.created_at.desc())
            .limit(min(max(limit, 1), 50))
        ).scalars().all()
    views = []
    for jid in rows:
        view = _job_view_sync(int(jid))
        if view is not None:
            views.append(view)
    return views
