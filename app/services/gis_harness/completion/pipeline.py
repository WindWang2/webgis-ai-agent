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
    FINAL_MAP_DEGRADED,
    FINAL_MAP_VERIFIED,
    MAX_DISCLOSED_REPAIRS,
    MAX_FINALIZATION_PASSES,
    MAX_FINDINGS,
    MAX_REPAIR_MEMORY,
    RUNTIME_RENDER_CODES,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_NEEDS_REPAIR,
    STATUS_PENDING,
    VERDICT_READY,
    VERDICT_READY_WITH_WARNINGS,
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

def _emit_finalization_chain(result: MapCompletionResult, *, passes: int = 0) -> None:
    """终验链发射（阶段 15/16/17；turn 上下文缺席时静默跳过）。"""
    try:
        from app.lib.runtime.chain_emitters import emit_chain, emit_chain_once
        from app.lib.runtime.gis_trace import Stage

        emit_chain_once(
            Stage.VERIFICATION,
            status=result.status,
            render_status=result.render_status,
            finding_codes=sorted({f.code for f in result.findings})[:8],
        )
        if result.repairs_applied:
            emit_chain(
                Stage.REPAIR,
                applied=list(result.repairs_applied[:6]),
                passes=int(passes),
            )
        emit_chain_once(
            Stage.FINAL_VERDICT,
            verdict=result.product_verdict,
            final_map_status=result.final_map_status,
        )
    except Exception:  # noqa: BLE001 — 记录面绝不阻断终验
        pass


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
    # V4 Wave 7（ADR-0104）：completion-time 模型兼容/全透明结构代理审计
    # （warning 级增值披露；validators import 放函数内防环）。
    try:
        from .validators.observation import (
            validate_map_model_compat as _vmmc,
            validate_layer_visibility_quality as _vlvq,
        )

        findings.extend(_vmmc(chapter, mapspec))
        findings.extend(_vlvq(chapter, mapspec))
    except Exception:  # noqa: BLE001 — 增值审计缺席不阻断终验
        pass
    # V7（ADR-0134 D5）：地图感知批评（blank map / 出版件完整性 / label
    # collision / 聚合错位 —— 纯函数；增值披露，缺席不阻断终验）。
    try:
        from app.services.gis_harness.map_critique import critique_map_state

        findings.extend(critique_map_state(
            chapter, mapspec, inputs.get("render_observation")))
    except Exception:  # noqa: BLE001 — 增值批评缺席不阻断终验
        pass
    return findings[:MAX_FINDINGS]


async def run_map_finalization(
    session_id: str,
    *,
    chapter: Optional[Dict[str, Any]] = None,
    max_passes: int = MAX_FINALIZATION_PASSES,
    reason: str = "manual",
    prior_repairs: Optional[List[str]] = None,
    acceptance_out: Optional[Dict[str, Any]] = None,
) -> Optional[MapCompletionResult]:
    """对一个会话运行完成度终验。无 GIS 章节 → None（无事可终验）。

    有界：至多 ``max_passes`` 轮 validate→repair→revalidate；每轮 repair
    后重读 MapSpec（修复改变 desired state）。不可修复的 error 直接落
    needs_repair/failed，绝不循环。

    ``acceptance_out``（V7 D6）：可变 dict 出参 —— 调用方传入时回填意图
    验收判定（accepted / intent_verified / unmet），随 map_product 持久化。
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

    # V3 Final Map Verification（Goal §九）：finalize 前的最终地图状态
    # 裁决 —— 补齐图层顺序 / 结果越界 / 陈旧覆盖层三组校验缺口。warning
    # 级增值披露，不改写既有 status 语义（零回归）；裁决在 result.status
    # 定格后聚合。
    has_planned_layers = bool(_planned_layers_v3(chapter))
    v3_findings: List[MapCompletionFinding] = []
    try:
        from .map_verification import (
            aggregate_final_map_status,
            collect_final_map_findings,
        )
        v3_findings = collect_final_map_findings(
            chapter, inputs["mapspec"],
            descriptors=inputs.get("descriptors"),
            result_bbox=result.result_bbox,
            render_observation=inputs.get("render_observation"),
        )
        findings = list(findings) + list(v3_findings)
    except Exception:  # noqa: BLE001 — V3 校验是增值披露，绝不阻断终验
        logger.warning("[MapFinalizer] v3 map verification failed session=%s", session_id)
        has_planned_layers = False

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

    # V3 最终裁决聚合（result.status 定格后；goal §九）
    if has_planned_layers:
        try:
            from .map_verification import aggregate_final_map_status
            result.final_map_status = aggregate_final_map_status(
                has_planned_layers=True,
                base_status=result.status,
                render_status=result.render_status,
                v3_findings=v3_findings,
            )
        except Exception:  # noqa: BLE001 — 聚合失败诚实留 unknown
            result.final_map_status = "unknown"

    # V4 Wave 7：裁决快照上 result（SSE task_complete 消费；与
    # map_product_block 的 derive 同源——同一纯函数、同一章节输入）。
    try:
        from .contracts import derive_product_verdict
        result.product_verdict = str(derive_product_verdict(
            result,
            [w for w in chapter.get("methodology_warnings") or [] if isinstance(w, dict)],
            chapter=chapter,
        ).get("verdict") or "")
    except Exception:  # noqa: BLE001 — 快照失败留空（旧路径语义）
        result.product_verdict = ""

    # V7（ADR-0134 D6）：意图验收独立判定（打破 intent_verified=complete
    # 的循环论证）。verdict + desired(spec) + observed(渲染证据) 三面核对；
    # acceptance_out 由调用方提供时回填（验收摘要随 map_product 持久化）。
    try:
        from app.services.gis_harness.intent_acceptance import (
            assess_intent_acceptance,
        )

        _acceptance = assess_intent_acceptance(
            chapter, inputs.get("mapspec"), inputs.get("render_observation"),
            product_verdict=result.product_verdict,
        )
        if acceptance_out is not None:
            acceptance_out.clear()
            acceptance_out.update(_acceptance)
    except Exception:  # noqa: BLE001 — 验收缺席退回诚实未知
        if acceptance_out is not None:
            acceptance_out.clear()
            acceptance_out.update({"accepted": False, "intent_verified": False,
                                   "unmet": ["acceptance_failed"]})

    # V4 Wave 8：证据链阶段 15/16/17（VERIFICATION / REPAIR /
    # FINAL_VERDICT）—— 终验事实入链（turn 上下文缺席时静默跳过）。
    _emit_finalization_chain(result, passes=result.passes)

    logger.info(
        "[MapFinalizer] finalization_pass session=%s status=%s passes=%d repairs=%d",
        session_id, result.status, result.passes, len(result.repairs_applied),
    )
    return result


def _planned_layers_v3(chapter: Dict[str, Any]) -> List[Dict[str, Any]]:
    """章节计划图层（V3 final map verification 的 has_planned 判定输入）。"""
    return [ly for ly in (chapter.get("map_layers") or [])
            if isinstance(ly, dict) and ly.get("layer_id")]


#: READY 裁决集合：final_gate 只对非 READY 会话强制重验（复用 contracts
#: 权威 token，不手写字面量 —— 互锁见 test_product_verdict.py）。
_READY_VERDICTS = (VERDICT_READY, VERDICT_READY_WITH_WARNINGS)


def _dedup_gate_blocks(
    stored: Any,
    chapter: Dict[str, Any],
    revision: int,
    render_seq: int,
    *,
    force: bool = False,
    final_gate: bool = False,
) -> bool:
    """幂等去重门（纯函数；True = 跳过重验）。

    V3 final_gate：已存裁决非 READY（needs_repair / blocked / 缺失——
    旧块/异常路径）→ turn 收尾强制重验（diagnose → repair → re-observe →
    re-verify 闭环），未解决会话不得靠幂等门滑过 turn 边界。READY 会话
    保持幂等跳过（happy path 零开销）。
    """
    if force:
        return False
    if not isinstance(stored, dict):
        return False
    if stored.get("status") not in (
            STATUS_COMPLETE, STATUS_NEEDS_REPAIR, STATUS_FAILED):
        return False
    if final_gate and str(stored.get("product_verdict") or "") not in _READY_VERDICTS:
        return False
    return (
        _stored_checked_revision(stored) == revision
        and _stored_render_seq(stored) == render_seq
        and str(stored.get("rows_fingerprint") or "")
        == _rows_fingerprint(chapter)[:2048]
    )


def _rows_fingerprint(chapter: Dict[str, Any]) -> str:
    """行状态指纹（去重门输入）：capability 行的状态/ref/算法/参数变化即改变。

    V4（ADR-0104 Wave 1）：实现移入 workflow_instance.rows_fingerprint
    （单一计算源），并在 V1 的 capability:status:bound_ref 之上纳入
    ``resolved_algorithm`` 与 ``params`` 内容哈希——修复审计 02 §A4 的洞：
    旧签名对 parameter/algorithm 编辑失明，参数-only 编辑后陈旧 verdict
    被门永久保护。指纹内容变化会让旧持久化块一次性打破门重验（设计目的，
    ADR-0104 兼容性节已披露）；同输入同指纹契约由测试钉住。
    """
    from app.services.gis_harness.workflow_instance import rows_fingerprint

    return rows_fingerprint(chapter)


def map_product_block(
    result: MapCompletionResult,
    checked_revision: int,
    *,
    all_repairs: Optional[List[str]] = None,
    rows_fingerprint: str = "",
    render_observation_seq: int = 0,
    methodology_warnings: Optional[List[Dict[str, Any]]] = None,
    chapter: Optional[Dict[str, Any]] = None,
    repair_plan: Optional[Dict[str, Any]] = None,
    observation: Optional[Dict[str, Any]] = None,
    intent_verified: bool = False,
    intent_acceptance: Optional[Dict[str, Any]] = None,
    continuation: Optional[Dict[str, Any]] = None,
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

    ``chapter``（Workflow V2 / Goal C / C7）：ganchapter 引用，用于读取
    planner 落盘的 ``workflow_contract`` 摘要 —— 七维完成契约与 BLOCKED
    裁决（方法/数据硬违反）参与推导；缺省 None 时行为与历史一致。
    """
    block = result.to_dict()
    if all_repairs is not None:
        merged = list(dict.fromkeys(all_repairs))
        block["repairs"] = merged[:MAX_DISCLOSED_REPAIRS]
        block["repair_memory"] = merged[:MAX_REPAIR_MEMORY]
    block["checked_revision"] = int(checked_revision)
    block["render_observation_seq"] = int(render_observation_seq)
    if rows_fingerprint:
        block["rows_fingerprint"] = rows_fingerprint[:2048]
    block["projection"] = result.projection_line()
    # VNext §14：单字产品裁决（READY / READY_WITH_WARNINGS / NEEDS_REPAIR /
    # BLOCKED_BY_DATA / BLOCKED_BY_METHOD）—— 章节方法论警告参与推导
    # （有披露永不 READY）。additive：旧读者忽略该键零漂移。
    try:
        from app.services.gis_harness.completion.contracts import (
            derive_product_verdict,
        )

        block["product_verdict"] = derive_product_verdict(
            result, methodology_warnings, chapter=chapter)
    except Exception:  # noqa: BLE001 — 裁决是增值投影，绝不阻断 finalization
        pass
    # V6 W10/W11：修复计划快照（additive；finding→分类→护栏→计划的证据面）。
    if repair_plan:
        block["repair_plan"] = repair_plan
    # V7（ADR-0134 D6）：意图验收摘要（additive；accepted/intent_verified/
    # unmet —— 打破 intent_verified=complete 的循环论证的持久化证据面）。
    if isinstance(intent_acceptance, dict):
        block["intent_acceptance"] = {
            "accepted": bool(intent_acceptance.get("accepted")),
            "intent_verified": bool(intent_acceptance.get("intent_verified")),
            "observed_confirmed": bool(
                intent_acceptance.get("observed_confirmed")),
            "unmet": [str(u)[:96]
                      for u in (intent_acceptance.get("unmet") or [])[:8]],
        }
    # V7（ADR-0134 D6）：终验出口 continuation 裁决（decide_continuation
    # 直连 —— V6 follow-up 兑现；repair 不可达时经 request_replan 路由）。
    if isinstance(continuation, dict):
        replan = continuation.get("replan")
        block["continuation"] = {
            "verdict": str(continuation.get("verdict") or "")[:40],
            "loop": str(continuation.get("loop") or "")[:16],
            "reason": str(continuation.get("reason") or "")[:160],
            "replan_pending": bool((replan or {}).get("replan_pending")
                                   if isinstance(replan, dict)
                                   else continuation.get("replan_pending")),
            "disclosure": [str(d)[:160]
                           for d in (continuation.get("disclosure") or ())[:4]],
        }
    # V6（ADR-0119 D9）：observation 状态阶梯摘要 —— mounted/loaded/
    # rendered/data_present/semantically_correct + workflow health 词汇。
    # 恒发射：缺席 observation → aggregate=unknown / health=blocked
    # （诚实缺席，workflow 消费方按三态裁决，绝不假通过）。
    try:
        from app.services.gis_harness.observation_states import (
            build_observation_summary,
        )

        block["observation_health"] = build_observation_summary(
            observation, intent_verified=intent_verified)
    except Exception:  # noqa: BLE001 — 摘要是增值投影，绝不阻断
        pass
    # V7（ADR-0134 D6 修复评审 F1）：task_complete 随块持久化 —— 此前
    # 只在 read_stored_map_product 读时折叠，生产块上无此键，导致
    # runtime_state_machine 的 COMMITTED 判定与 commit_runtime_context
    # 守卫恒假（键契约错位，新功能死代码）。同一折叠单一来源。
    try:
        block["task_complete"] = _is_task_complete(block)
    except Exception:  # noqa: BLE001 — 折叠失败按旧形状（缺键）
        pass
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
    final_gate: bool = False,
) -> Optional[MapCompletionResult]:
    """Harness 侧触发入口：廉价门 + 终验 + 章节持久化（幂等、有界）。

    去重门（review 加固）：章节已有终态 ``map_product``（不止 complete）
    且 checked_revision 与当前 MapSpec revision 一致、行指纹一致 → 跳过。
    行状态/ref 或 spec revision 任一变化都会打破门 → 重验。

    ``final_gate``（V3 Goal §九）：turn 收尾的强制终验门 —— 已存裁决非
    READY（needs_repair / blocked）时绕过去重门重新诊断（diagnose →
    repair → re-observe → re-verify 闭环的最后一段）；READY 会话保持幂等
    跳过（happy path 零开销）。与 ``force`` 的差别：force 无条件重验，
    final_gate 只对未解决会话强制。

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
    if _dedup_gate_blocks(
        stored, chapter, revision, render_seq,
        force=force, final_gate=final_gate,
    ):
        return None

    validated_fingerprint = _rows_fingerprint(chapter)
    acceptance: Dict[str, Any] = {}
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
        acceptance_out=acceptance,
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

    # V6 W10/W11：修复计划（findings 统一投影 → 护栏 → 计划 → 账本落账）。
    # 增值披露：任何失败不影响块写入；锁集读自 mapspec workbench doc。
    repair_plan_dict: Optional[Dict[str, Any]] = None
    try:
        from app.services.gis_harness.repair_planner import plan_repairs_for_chapter
        from app.services.mapspec_store import mapspec_store

        mapspec_now: Optional[Dict[str, Any]] = None
        try:
            mapspec_now = await mapspec_store.get_mapspec(session_id) or None
        except Exception:  # noqa: BLE001 — spec 读失败按无锁集处理
            mapspec_now = None
        repair_plan_dict = await plan_repairs_for_chapter(
            session_id, chapter, result, mapspec=mapspec_now)
    except Exception:  # noqa: BLE001 — 修复计划是增值披露，绝不阻断终验
        logger.debug("[MapFinalizer] repair plan failed session=%s", session_id,
                     exc_info=True)

    # V7（ADR-0134 D6）：终验出口直连 decide_continuation（V6 follow-up
    # 兑现）—— needs_repair/failed 时裁决 repair/replan/abort；修复不可达
    # 且 replan 预算有余 → request_replan 生产驱动点（replan_pending 置位 +
    # durable 记账）。增值披露，绝不阻断终验。
    continuation_dict: Optional[Dict[str, Any]] = None
    if result.status in (STATUS_NEEDS_REPAIR, STATUS_FAILED):
        try:
            continuation_dict = await _finalizer_continuation(
                session_id, result, chapter)
        except Exception:  # noqa: BLE001 — 裁决缺席按既有回路
            logger.debug("[MapFinalizer] continuation decision failed session=%s",
                         session_id, exc_info=True)

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
                if _rows_fingerprint(fresh.gis_chapter)[:2048] != validated_fingerprint[:2048]:
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
                # 与原实现同语义：state 读失败 → load_render_observation(sid, None)
                # 内部再读一次（瞬态失败不误判「观察已推进」，审查 R1 M-minor-8）
                current_observation = await load_render_observation(
                    session_id, fresh_state)
                if observation_sequence(
                    current_observation
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
                    methodology_warnings=list(
                        fresh.gis_chapter.get("methodology_warnings") or []),
                    chapter=fresh.gis_chapter,
                    repair_plan=repair_plan_dict,
                    observation=current_observation,
                    # V7（D6）：意图验收的独立判定（打破循环论证 —— 渲染
                    # 证据缺席时不再自证 semantically_correct）。
                    intent_verified=bool(acceptance.get("intent_verified")),
                    intent_acceptance=acceptance or None,
                    continuation=continuation_dict,
                )
                await save_session_plan(fresh)
    except Exception:  # noqa: BLE001 — 披露失败不阻断 turn；下一触发点重试
        logger.warning(
            "[MapFinalizer] chapter persist failed session=%s (will retry on next trigger)",
            session_id,
        )
    # V7（ADR-0134 D6）：READY → 上下文提交（九域 checkpoint + commit 标记
    # + 阶段推进 verdict_ready）。受状态机 kill switch 门控（评审 F6 ——
    # 关停时逐位回退，不写任何 V7 键）。增值披露，绝不阻断终验返回。
    if result.status == STATUS_COMPLETE:
        try:
            from app.services.gis_harness.runtime_state_machine import (
                runtime_state_enabled,
            )
            _rsm_on = runtime_state_enabled()
        except Exception:  # noqa: BLE001
            _rsm_on = False
        if _rsm_on:
            try:
                from app.services.gis_harness.context_layers import (
                    checkpoint_context_layers,
                )
                from app.services.gis_harness.runtime_state_machine import (
                    commit_runtime_context,
                )

                _layers_block = await checkpoint_context_layers(session_id)
                await commit_runtime_context(
                    session_id,
                    domains_digest=str(
                        (_layers_block or {}).get("content_fingerprint") or ""),
                )
            except Exception:  # noqa: BLE001 — 提交缺席可由下个触发点补
                logger.debug("[MapFinalizer] context commit failed session=%s",
                             session_id, exc_info=True)
            try:
                from app.services.gis_harness.runtime_state_machine import (
                    maybe_update_runtime_state,
                )

                await maybe_update_runtime_state(
                    session_id, reason=reason, trigger="verdict_ready")
            except Exception:  # noqa: BLE001 — 阶段投影是增值披露
                logger.debug("[MapFinalizer] runtime state update failed session=%s",
                             session_id, exc_info=True)
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
            final_map=result.final_map_status,
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


async def _finalizer_continuation(
    session_id: str,
    result: MapCompletionResult,
    chapter: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """终验出口的 continuation 裁决（V7 D6；decide_continuation 直连）。

    - needs_repair/failed → decide_continuation（recovery 预算 + 渲染失败
      分类）；
    - verdict=remediate_and_retry/reobserve → 原样披露（修复回路已由
      repair planner / observation POST 驱动）；
    - verdict=abort_with_disclosure（repair 预算尽）→ request_replan
      生产驱动点（replan 预算有余 → 置 replan_pending；否则诚实 abort）。
    """
    from app.services.gis_harness.continuation import decide_continuation
    from app.services.gis_harness.durable_context import load_recovery_state

    recovery = await load_recovery_state(session_id)
    render_state = str(result.render_status or "")
    failure = {
        "class": "renderer_failure" if render_state in ("stale", "unknown", "error")
        else "tool_error",
        "tool": "render",
        "code": str(result.findings[0].code) if result.findings else "",
    }
    decision = decide_continuation(
        recovery_state=recovery,
        observation={"state": render_state or "unknown"},
        failure=failure,
    )
    payload: Dict[str, Any] = dict(decision.to_payload())
    if decision.verdict == "abort_with_disclosure":
        # deep/requalify/repair 全耗尽（replan 不参与 abort 门槛 —— 评审
        # F2：它是 abort 的逃生舱）→ 重规划生产驱动点（预算有余则置
        # replan_pending；耗尽则诚实 abort 披露）。不可恢复类（cancelled/
        # budget_exhausted）在规则 2 直接 abort，不经此处 —— 不与用户取消
        # 或资源上限做结构级对抗。
        try:
            from app.services.gis_harness.plan_runtime import request_replan

            replan = await request_replan(
                session_id,
                reason=str(result.summary or "")[:160],
                from_verdict=str(result.product_verdict or result.status)[:32],
            )
            # abort 裁决保持权威 —— replan 路由结果挂子对象（覆盖 verdict
            # 会让 continuation 消费方把「已诚实中止」误读为「已转重规划」）
            payload["replan"] = replan
        except Exception:  # noqa: BLE001 — 驱动点失败保留 abort 披露
            logger.debug("[MapFinalizer] replan driver failed session=%s",
                         session_id, exc_info=True)
    return payload


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
    payload = {
        # session_id 参与 frontend INV-2 跨会话守卫（review B-P3）：缺 sid
        # 的载荷绕过守卫，可能把别的会话相机 fit 走 / 弹错 toast。
        "session_id": session_id,
        "status": str(stored.get("status") or STATUS_PENDING),
        "summary": str(stored.get("summary") or "")[:120],
        # V4 Wave 7（审计 06 建议 4）：任务级「真完成」判定面 —— 观测/
        # 裁决进入最终完成判定：BLOCKED_* / 渲染未证实的会话不再是
        # disclosure-only 的 complete。additive 键，旧读者忽略。
        "task_complete": _is_task_complete(stored),
    }
    # V7（ADR-0134 D6）：最终显示确认（默认 auto → True，零行为变化；
    # required 模式等待显式 ack —— human confirmation seam）。
    try:
        from app.services.gis_harness.display_confirmation import (
            is_display_confirmed,
        )

        payload["display_confirmed"] = await is_display_confirmed(
            session_id,
            render_seq=int(stored.get("render_observation_seq") or 0),
        )
    except Exception:  # noqa: BLE001 — 确认面缺席按 auto（True）
        payload["display_confirmed"] = True
    return payload


def _product_verdict_token(stored: Dict[str, Any]) -> str:
    """stored 块的裁决 token（兼容双形状）：map_product_block 持久化的是
    ``derive_product_verdict`` 的完整 dict（含 reasons/dimensions），而
    result.product_verdict / 旧读方语义是字符串 —— 形状错位使字符串
    判定在真实块上恒 False（评审 F1 的深层根因，V4 起既有）。"""
    verdict = stored.get("product_verdict")
    if isinstance(verdict, dict):
        return str(verdict.get("verdict") or "")
    return str(verdict or "")


def _is_task_complete(stored: Dict[str, Any]) -> bool:
    """stored map_product 块 → 任务级完成布尔（纯函数，有界输入）。

    完成 = 产品裁决 ∈ {READY, READY_WITH_WARNINGS} 且最终地图状态 ∈
    {verified, verified_with_degradation}。needs_repair / pending /
    BLOCKED_BY_* / failed / unknown 一律不算完成。
    """
    from .contracts import (
        FINAL_MAP_DEGRADED,
        FINAL_MAP_VERIFIED,
        VERDICT_READY,
        VERDICT_READY_WITH_WARNINGS,
    )

    verdict = _product_verdict_token(stored)
    final_status = str(stored.get("final_map_status") or "")
    return (
        verdict in (VERDICT_READY, VERDICT_READY_WITH_WARNINGS)
        and final_status in (FINAL_MAP_VERIFIED, FINAL_MAP_DEGRADED)
    )


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
    # V4 Wave 7：任务级完成布尔 = 裁决 ∈ READY* 且最终地图状态 ∈ verified*
    # （与 read_stored_map_product 的 task_complete 同一折叠；verdict 来自
    # finalize 管线的推导快照，载荷侧零重复推导；字面量复用 contracts 权威
    # token，见 test_product_verdict.py 互锁）。
    payload["task_complete"] = (
        str(result.product_verdict) in (VERDICT_READY, VERDICT_READY_WITH_WARNINGS)
        and result.final_map_status in (FINAL_MAP_VERIFIED, FINAL_MAP_DEGRADED)
    )
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
