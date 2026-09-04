"""编排（validate → repair → revalidate，≤ MAX_FINALIZATION_PASSES）+ 章节持久化/披露
— ADR-0081 / ADR-0091。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .contracts import (
    F_COMPONENT_DISABLED,
    F_COMPONENT_MISSING,
    F_EXECUTION_BLOCKED,
    F_LAYER_HIDDEN,
    F_LAYER_MISSING,
    F_NEEDS_EXECUTION,
    F_NO_RESULT_LAYER,
    F_SOURCE_MISSING,
    F_VIEWPORT_NO_BBOX,
    MAX_DISCLOSED_REPAIRS,
    MAX_FINALIZATION_PASSES,
    MAX_FINDINGS,
    MAX_REPAIR_MEMORY,
    RUNTIME_RENDER_CODES,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_NEEDS_REPAIR,
    STATUS_PENDING,
    MapCompletionFinding,
    MapCompletionResult,
    _spec_layers,
)
from .inputs import gather_completion_inputs
from .repairs import _apply_repairs
from .validators import (
    assess_export_parity,
    derive_result_bbox,
    validate_artifacts,
    validate_components,
    validate_execution,
    validate_layers,
    validate_layout,
    validate_semantics,
)

logger = logging.getLogger(__name__)


def _validate_all(inputs: Dict[str, Any], chapter: Dict[str, Any]) -> List[MapCompletionFinding]:
    mapspec = inputs["mapspec"]
    findings: List[MapCompletionFinding] = []
    findings.extend(validate_artifacts(chapter, inputs["descriptors"]))
    findings.extend(validate_layers(chapter, mapspec, inputs["descriptors"]))
    findings.extend(
        validate_components(
            mapspec,
            inputs["required_slots"],
            [str(ly.get("id") or "") for ly in _spec_layers(mapspec)],
        )
    )
    findings.extend(validate_layout(mapspec))
    findings.extend(
        validate_semantics(
            chapter,
            mapspec,
            inputs["required_slots"],
            contract=inputs.get("facet_contract"),
            records=inputs.get("artifact_records"),
        )
    )
    return findings[:MAX_FINDINGS]


async def run_map_finalization(
    session_id: str,
    *,
    chapter: Optional[Dict[str, Any]] = None,
    max_passes: int = MAX_FINALIZATION_PASSES,
    reason: str = "manual",
    prior_repairs: Optional[List[str]] = None,
) -> Optional[MapCompletionResult]:
    """对一个会话运行完成度终验。无 GIS 章节 → None（无事可终验）。

    有界：至多 ``max_passes`` 轮 validate→repair→revalidate；每轮 repair
    后重读 MapSpec（修复改变 desired state）。不可修复的 error 直接落
    needs_repair/failed，绝不循环。
    """
    from app.services.session_plan import load_session_plan

    if chapter is None:
        plan = await load_session_plan(session_id)
        chapter = plan.gis_chapter if plan is not None else None
    if not isinstance(chapter, dict) or not chapter:
        return None

    logger.info("[MapFinalizer] finalization_started session=%s reason=%s", session_id, reason)
    result = MapCompletionResult()
    exec_findings = validate_execution(chapter)
    if exec_findings:
        blocked = [f for f in exec_findings if f.code == F_EXECUTION_BLOCKED]
        has_open = any(f.code == F_NEEDS_EXECUTION for f in exec_findings)
        if has_open or not blocked:
            result.status = STATUS_PENDING
            result.findings = exec_findings[:MAX_FINDINGS]
            result.summary = "DAG not terminal — execution still owed"
            result.passes = 0
            return result
        # blocked-only（failed/unavailable 终态行）：DAG 已终态、执行欠账
        # 不会自愈 —— pending 会被静默吞掉（不落块不披露），turn 结束时
        # 零产品级披露（review H-3）。按 failed 披露欠重试/欠降级，交还
        # DAG/重试语义；finalizer 绝不自己重跑算法（ADR-0081）。
        result.status = STATUS_FAILED
        result.findings = blocked[:MAX_FINDINGS]
        result.summary = f"{len(blocked)} blocked nodes await retry/replan"
        result.passes = 0
        result.viewport_status = "not_applicable"
        result.layer_status = "unknown"
        result.component_status = "unknown"
        result.export_status = "unknown"
        return result


    inputs = await gather_completion_inputs(session_id, chapter)
    all_repairs: List[str] = []
    findings: List[MapCompletionFinding] = []
    passes = 0
    repaired_last_pass = False
    while passes < max_passes:
        passes += 1
        findings = _validate_all(inputs, chapter)
        fatal = [
            f
            for f in findings
            if f.severity == "error"
            and f.repair is None
            and f.code in (F_NO_RESULT_LAYER, F_LAYER_MISSING, F_SOURCE_MISSING)
        ]
        if fatal:
            # review P3：存在不可修复的结构性 error 时不再做组件修复 ——
            # 修复只会白付两轮 revision 而 status 仍 failed。
            repaired_last_pass = False
            break
        repairable = [f for f in findings if f.repair is not None]
        if not repairable or not findings:
            repaired_last_pass = False
            break
        repairs = await _apply_repairs(
            session_id, findings, inputs["mapspec"], prior_repairs=prior_repairs
        )
        all_repairs.extend(repairs)
        if not repairs:
            repaired_last_pass = False
            break  # 修复通道全部失败 → 再验也不会变，避免空转
        # 修复改变了 desired state —— 重读输入再验
        inputs = await gather_completion_inputs(session_id, chapter)
        repaired_last_pass = True

    # review P1：末轮刚应用过修复时，findings 还是修复前的快照 —— 用
    # 重读后的输入做一次终验（纯函数，零 I/O），状态才与新 desired state
    # 一致（否则 repairs_applied 与 findings 自相矛盾）。
    if repaired_last_pass:
        findings = _validate_all(inputs, chapter)

    # P9 渲染级校验（ADR-0086）：RenderObservation 是观察不是真相 —— 只产
    # 出披露 findings，无修复动作；stale/unknown 如实降级（不 false
    # complete），runtime 缺席归 needs_repair（可自愈），不落 failed。
    try:
        from app.services.gis_harness.render_observation import (
            validate_render_observation,
        )

        render_status, render_findings = validate_render_observation(
            chapter,
            inputs["mapspec"],
            inputs.get("render_observation"),
            int(inputs.get("mapspec_revision") or 0),
            inputs["required_slots"],
        )
        result.render_status = render_status
        findings = list(findings) + list(render_findings)
    except Exception:  # noqa: BLE001 — 渲染校验是增值披露，绝不阻断终验
        logger.warning("[MapFinalizer] render validation failed session=%s", session_id)
        result.render_status = "unknown"

    result.passes = passes
    result.result_bbox = derive_result_bbox(chapter, inputs["descriptors"])
    result.export_status = assess_export_parity(inputs["mapspec"])

    has_layers = bool(_spec_layers(inputs["mapspec"]))
    if result.result_bbox:
        # 相机真相在前端：bbox 已导出 → 前端 finalizer 校验并（必要时）修复
        result.viewport_status = "repairable"
    elif has_layers:
        result.viewport_status = "invalid"
        findings.append(
            MapCompletionFinding(
                code=F_VIEWPORT_NO_BBOX,
                severity="warning",
                target="viewport",
                detail="no artifact bbox available to verify result visibility",
            )
        )
    else:
        result.viewport_status = "not_applicable"

    # 状态先于披露截断计算（review 终审 F6）：findings[:MAX_FINDINGS] 只是
    # 披露上界 —— 用全量 findings 判状态，否则 >12 条发现时第 13 条起的
    # error 会被静默丢弃、误判 complete。
    all_errors = [f for f in findings if f.severity == "error"]
    result.findings = findings[:MAX_FINDINGS]
    result.repairs_applied = all_repairs[:MAX_DISCLOSED_REPAIRS]

    layer_err = [f for f in findings if f.code in (
        F_NO_RESULT_LAYER, F_LAYER_MISSING, F_SOURCE_MISSING, F_LAYER_HIDDEN,
    )]
    result.layer_status = "issues" if layer_err else ("valid" if has_layers else "unknown")
    comp_err = [f for f in findings if f.code in (
        F_COMPONENT_MISSING, F_COMPONENT_DISABLED,
    )]
    result.component_status = "issues" if comp_err else "valid"

    unrepairable = [
        f
        for f in all_errors
        # P9：runtime 渲染缺口（层/源/组件未挂载、观察错误）不进 failed ——
        # 期望态正确、可经 re-render/re-observation 自愈，归 needs_repair。
        if f.repair is None and f.code not in RUNTIME_RENDER_CODES
    ]
    still_repairable = [f for f in all_errors if f.repair is not None]
    runtime_render = [f for f in all_errors if f.code in RUNTIME_RENDER_CODES]
    if not all_errors:
        result.status = STATUS_COMPLETE
        result.summary = "map product validated"
    elif unrepairable:
        # 不可修复 error 在场 → failed 优先于 needs_repair（只靠修复到不了
        # complete，"needs repair" 会误导下一动作）。
        result.status = STATUS_FAILED
        result.summary = f"{len(all_errors)} blocking findings ({len(unrepairable)} unrepairable)"
    elif runtime_render and not still_repairable:
        result.status = STATUS_NEEDS_REPAIR
        result.summary = (
            f"{len(runtime_render)} render findings await runtime re-observation"
        )
    else:
        result.status = STATUS_NEEDS_REPAIR
        result.summary = f"{len(still_repairable)} repairable findings remain"

    logger.info(
        "[MapFinalizer] finalization_pass session=%s status=%s passes=%d repairs=%d",
        session_id, result.status, result.passes, len(result.repairs_applied),
    )
    return result


def _rows_fingerprint(chapter: Dict[str, Any]) -> str:
    """行状态指纹（去重门输入）：capability 行的状态/ref 绑定变化即改变。

    比「行全终态」检查更强（review A-2/B-3/F-4）：行回退（重试标 failed、
    重绑定新 ref）都会改变指纹 → 触发重验；同时让 needs_repair/failed
    会话在无变化时跳过整轮重跑（此前只有 complete 享受去重门，异常会话
    每个工具结果都重放整轮 finalization + SSE + toast）。
    """
    parts: List[str] = []
    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        parts.append(
            f"{row.get('capability')}:{row.get('status')}:{row.get('bound_ref') or ''}"
        )
    return "|".join(sorted(parts))


def map_product_block(
    result: MapCompletionResult,
    checked_revision: int,
    *,
    all_repairs: Optional[List[str]] = None,
    rows_fingerprint: str = "",
    render_observation_seq: int = 0,
) -> Dict[str, Any]:
    """章节持久化块（additive、bounded、单一键 ``map_product``）。

    ``all_repairs``：跨轮累积修复记忆（prior ∪ 本轮 applied）。one-shot
    语义依赖它跨轮存活 —— 只写本轮 applied 时，下一次无修复运行会把
    记忆清零，finalizer 将隔轮重新对抗用户决策（review B-4）。披露面
    ``repairs`` 有界（≤6）；完整记忆落 ``repair_memory``（≤32 —— 6 条
    上限会在多组件/多层会话里挤掉最老记忆，复活同一回归，review 终审 F7）。

    ``render_observation_seq``（P9）：验证所依据的 render observation 代次 ——
    幂等门的第三把钥匙：新观察到达（seq 前进）即打破门，重验把披露从
    unverified/stale 升级为 verified（或反向暴露 render 缺席）。
    """
    block = result.to_dict()
    if all_repairs is not None:
        merged = list(dict.fromkeys(all_repairs))
        block["repairs"] = merged[:MAX_DISCLOSED_REPAIRS]
        block["repair_memory"] = merged[:MAX_REPAIR_MEMORY]
    block["checked_revision"] = int(checked_revision)
    block["render_observation_seq"] = int(render_observation_seq)
    if rows_fingerprint:
        block["rows_fingerprint"] = rows_fingerprint[:512]
    block["projection"] = result.projection_line()
    return block


async def _current_mapspec_revision(session_id: str) -> int:
    from app.services.session_data import session_data_manager

    try:
        state = await session_data_manager.get_map_state(session_id)
        return int(state.get("_cartographic_mutation_revision") or 0)
    except Exception:  # noqa: BLE001 — revision 读失败按 0 处理（只影响去重）
        return 0


def _stored_checked_revision(stored: Dict[str, Any]) -> Optional[int]:
    """合法 0 不被误判（review P3：``int(x or -1)`` 把 0 洗成 -1）。"""
    raw = stored.get("checked_revision")
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _stored_render_seq(stored: Dict[str, Any]) -> int:
    """已存块记录的 render observation 代次（旧块无键 → -1 触发一次重验自愈）。"""
    raw = stored.get("render_observation_seq")
    if isinstance(raw, bool) or raw is None:
        return -1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


async def maybe_finalize_map_product(
    session_id: str,
    *,
    reason: str = "tool_result",
    force: bool = False,
) -> Optional[MapCompletionResult]:
    """Harness 侧触发入口：廉价门 + 终验 + 章节持久化（幂等、有界）。

    去重门（review 加固）：章节已有终态 ``map_product``（不止 complete）
    且 checked_revision 与当前 MapSpec revision 一致、行指纹一致 → 跳过。
    行状态/ref 或 spec revision 任一变化都会打破门 → 重验。

    pending 不持久化、不披露 —— 除非章节里已有终态结论（review A-2/B-3：
    重试把行标 failed 后，陈旧的 "final" 投影必须收回，落降级 pending 块；
    [GIS Plan] 的行投影已披露未完成态，不发 SSE）。

    写入路径复用 SessionPlan 的 per-session lock（fail-closed）；只覆盖
    ``gis_chapter["map_product"]`` 单键，不触碰行状态（无第二事实源）。
    锁内做两道守卫：goal 变化（supersede 竞态）与 revision 漂移（验证后
    突变）都不落块 —— 让下一个触发点对真实状态重新终验。
    """
    from app.services.session_plan import goal_key, load_session_plan, save_session_plan
    from app.services.distributed_lock import session_lock_registry
    from app.services.session_data import session_data_manager
    from app.services.gis_harness.render_observation import (
        load_render_observation,
        observation_sequence,
    )

    if not session_id:
        return None
    plan = await load_session_plan(session_id)
    if plan is None or not isinstance(plan.gis_chapter, dict):
        return None
    chapter = plan.gis_chapter
    # P9：revision + render observation 一次读取（门输入同源，不双拉状态）。
    try:
        map_state = await session_data_manager.get_map_state(session_id)
    except Exception:  # noqa: BLE001 — 状态读失败按 0/None 处理（只影响去重门）
        map_state = None
    try:
        revision = int((map_state or {}).get("_cartographic_mutation_revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    render_obs = await load_render_observation(session_id, map_state)
    render_seq = observation_sequence(render_obs)
    stored = chapter.get("map_product")
    # 去重门（review 加固 + F-4）：任何终态结论（不止 complete）在
    # 「MapSpec revision 一致 + 行指纹一致 + render observation 代次一致」
    # 时跳过重验 —— 行状态/ref 变化、任何 cartographic 突变或新观察到达
    # 都会打破门，交给下一触发点重验。
    # 旧块无 rows_fingerprint 键 → 首次不跳过，重验一次即自愈补齐。
    # 比较双侧截断（review 终审 F2）：存储侧 [:512]，比较侧同宽 ——
    # 此前存储截断/比较全量，≥8 行章节永不匹配 → 门失效、每触发点重跑。
    if (
        not force
        and isinstance(stored, dict)
        and stored.get("status")
        in (STATUS_COMPLETE, STATUS_NEEDS_REPAIR, STATUS_FAILED)
        and _stored_checked_revision(stored) == revision
        and _stored_render_seq(stored) == render_seq
        and str(stored.get("rows_fingerprint") or "")
        == _rows_fingerprint(chapter)[:512]
    ):
        return None

    validated_fingerprint = _rows_fingerprint(chapter)
    result = await run_map_finalization(
        session_id,
        chapter=chapter,
        reason=reason,
        prior_repairs=(
            list(
                dict.fromkeys(
                    list(stored.get("repair_memory") or [])
                    + list(stored.get("repairs") or [])
                )
            )
            if isinstance(stored, dict)
            else None
        ),
    )
    if result is None:
        return None
    stored_terminal = isinstance(stored, dict) and stored.get("status") in (
        STATUS_COMPLETE,
        STATUS_NEEDS_REPAIR,
        STATUS_FAILED,
    )
    if result.status == STATUS_PENDING and not stored_terminal:
        # 不持久化、不披露（见 docstring）；调用方拿 result 只做日志。
        return result
    demoted = result.status == STATUS_PENDING
    if demoted:
        # 回退降级（review A-2/B-3）：已存终态结论的章节出现新的执行缺口
        # （重试把行标 failed / 新增 pending 行）→ 陈旧的 "final" 投影必须
        # 收回。落 pending 块（行投影已表达欠执行，不发 SSE、不 toast）。
        result.summary = "execution re-owed — prior verdict withdrawn"

    validated_goal = goal_key(chapter, plan.user_goal)
    revision_after_run = await _current_mapspec_revision(session_id)

    # 持久化（锁内重读——终验本身的 repair 突变可能已推进 revision）
    try:
        async with session_lock_registry.lock(session_id, fail_on_degraded=True) as lock:
            fresh = await load_session_plan(session_id)
            if fresh is not None and isinstance(fresh.gis_chapter, dict):
                if lock.lost:
                    return result
                # supersede/replace 竞态：验证的章节已不是当前章节 → 不落块
                if goal_key(fresh.gis_chapter, fresh.user_goal) != validated_goal:
                    logger.info(
                        "[MapFinalizer] chapter superseded mid-run session=%s — persist skipped",
                        session_id,
                    )
                    return result
                # 验证后 revision 又被并发突变 → complete@R' 会盖住未验证的
                # 状态；留给下一触发点重验。
                if await _current_mapspec_revision(session_id) != revision_after_run:
                    logger.info(
                        "[MapFinalizer] revision moved mid-run session=%s — persist skipped",
                        session_id,
                    )
                    return result
                # 行漂移守卫（review 终审 F1）：终验期间并行工具回调改了行
                # 状态（行不推 revision）—— 旧指纹的结论不得盖上新指纹的
                # 章节（否则陈旧 failed/complete 被门永久保护）。
                if _rows_fingerprint(fresh.gis_chapter)[:512] != validated_fingerprint[:512]:
                    logger.info(
                        "[MapFinalizer] rows changed mid-run session=%s — persist skipped",
                        session_id,
                    )
                    return result
                # P9 观察漂移守卫：验证依据的 render observation 已被更新的
                # 观察覆盖（新 POST 在锁外落账、等锁写入）→ 旧观察的结论不得
                # 盖章 —— 留给下一触发点（含 POST 触发本身）按新观察重验。
                try:
                    fresh_state = await session_data_manager.get_map_state(session_id)
                except Exception:  # noqa: BLE001 — 读失败按无漂移处理
                    fresh_state = None
                if observation_sequence(
                    await load_render_observation(session_id, fresh_state)
                ) != render_seq:
                    logger.info(
                        "[MapFinalizer] render observation advanced mid-run session=%s — persist skipped",
                        session_id,
                    )
                    return result
                prior_repairs_merged = (
                    list(stored.get("repair_memory") or [])
                    + list(stored.get("repairs") or [])
                    if isinstance(stored, dict)
                    else []
                )
                merged_repairs = list(
                    dict.fromkeys(prior_repairs_merged + list(result.repairs_applied))
                )
                fresh.gis_chapter["map_product"] = map_product_block(
                    result,
                    revision_after_run,
                    all_repairs=merged_repairs,
                    rows_fingerprint=_rows_fingerprint(fresh.gis_chapter),
                    render_observation_seq=render_seq,
                )
                await save_session_plan(fresh)
    except Exception:  # noqa: BLE001 — 披露失败不阻断 turn；下一触发点重试
        logger.warning(
            "[MapFinalizer] chapter persist failed session=%s (will retry on next trigger)",
            session_id,
        )
    # ADR-0088 P7：内部 trace（best-effort，绝不影响业务路径）
    try:
        from app.services.gis_harness.trace import (
            COUNTER_FINALIZATION_REPAIRS,
            COUNTER_FINALIZATIONS,
            STAGE_FINALIZATION,
            get_runtime_trace,
        )
        _trace = get_runtime_trace()
        _trace.record(
            session_id, STAGE_FINALIZATION,
            status=result.status,
            render=result.render_status,
            passes=result.passes,
            repairs=len(result.repairs_applied),
        )
        _trace.bump(COUNTER_FINALIZATIONS)
        if result.repairs_applied:
            _trace.bump(COUNTER_FINALIZATION_REPAIRS, len(result.repairs_applied))
    except Exception:  # noqa: BLE001 — trace 故障不影响业务
        pass
    if result.status == STATUS_COMPLETE:
        logger.info("[MapFinalizer] finalization_complete session=%s", session_id)
    else:
        logger.info(
            "[MapFinalizer] finalization_failed session=%s status=%s",
            session_id, result.status,
        )
    return result


async def read_stored_map_product(session_id: str) -> Optional[Dict[str, Any]]:
    """读取已持久化的完成块（turn 收尾的 task_complete 披露兜底）。

    幂等门跳过终验时（complete + revision 一致），task_complete 仍应携带
    完成态 —— 否则 happy path 下该字段永远缺席（review P2）。
    """
    from app.services.session_plan import load_session_plan

    if not session_id:
        return None
    plan = await load_session_plan(session_id)
    # gis_chapter is None for sessions whose plan never opened a GIS chapter
    # (plain chat) — a bare ``plan.gis_chapter.get`` crashed the whole
    # disclosure on every such turn's agent_settled.
    chapter = getattr(plan, "gis_chapter", None) if plan is not None else None
    stored = chapter.get("map_product") if isinstance(chapter, dict) else None
    if not isinstance(stored, dict):
        return None
    return {
        # session_id 参与 frontend INV-2 跨会话守卫（review B-P3）：缺 sid
        # 的载荷绕过守卫，可能把别的会话相机 fit 走 / 弹错 toast。
        "session_id": session_id,
        "status": str(stored.get("status") or STATUS_PENDING),
        "summary": str(stored.get("summary") or "")[:120],
    }


def finalization_sse_payload(
    result: MapCompletionResult,
    session_id: str = "",
    *,
    mapspec: Optional[Dict[str, Any]] = None,
    mutation_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """前端 finalizer 消费的有界载荷（视口修复需要 bbox 与状态）。

    session_id 参与 frontend INV-2 跨会话守卫（review P1：载荷缺 sid 时
    旧会话的迟到事件会把新会话相机 fit 走）。repair 改写了 desired state
    时携带 mapspec + mutation_revision —— 前端通用 spec 提交通道
    （use-sse-stream 对 data.mapspec 的既有消费）会把修复同步到 live
    chrome/exporter，否则"complete"对着一张用户看不见的 spec 宣称。
    """
    payload = {
        "status": result.status,
        "viewport_status": result.viewport_status,
        "result_bbox": result.result_bbox,
        "summary": result.summary[:120],
        "issues": [f.to_dict() for f in result.findings[:4]],
        "repairs": list(result.repairs_applied[:4]),
    }
    if session_id:
        payload["session_id"] = session_id
    if mapspec is not None and result.repairs_applied:
        payload["mapspec"] = mapspec
        payload["mutation_revision"] = mutation_revision
    return payload


async def current_mapspec_for_disclosure(session_id: str) -> tuple[Optional[Dict[str, Any]], Optional[int]]:
    """修复披露用的当前 spec 快照（只在实际应用过修复时被读取）。"""
    from app.services.mapspec_store import mapspec_store

    try:
        spec = await mapspec_store.get_mapspec(session_id)
        return spec, await _current_mapspec_revision(session_id)
    except Exception:  # noqa: BLE001 — 快照失败只影响附带披露
        return None, None
