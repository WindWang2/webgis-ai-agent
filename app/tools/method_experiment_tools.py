"""方法实验工具面（method-experiment WP5）—— Pi/Agent/普通 UI 同链入口。

六个按 analysis 域载入的 tier-2 工具（与 analysis_workflow_tools 同款
纪律）：

- ``webgis_method_experiment_inspect``  只读：方法卡/候选空间/协议建议/
                                        预算预览（零执行）
- ``webgis_method_experiment_plan``     确定性编译计划（typed 预检拒绝）
- ``webgis_method_experiment_run``      执行 pending 试验（真实 dispatch/
                                        内容寻址复用/有界窗口/可续跑）
- ``webgis_method_experiment_status``   状态投影（诚实计数，不虚构 ETA）
- ``webgis_method_experiment_compare``  可比组排名 + Pareto + 证据推荐
- ``webgis_method_experiment_adopt``    用户采纳回执（不自动改地图）

纪律：执行真相 = 共享 ToolDispatchService（与 Pi 直调同缝）；状态单键
``method_experiment.v1:<id>`` + 实验级分布式锁；返回载荷有界（FC 本体
绝不进上下文）；``tool`` 键自标识（``command`` 是地图命令保留键）。
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from app.services.gis_harness.method_experiment.cards import (
    EXPERIMENT_FAMILY_IDS,
    all_cards,
    match_family_by_question,
)
from app.services.gis_harness.method_experiment.evaluate import (
    evaluate_experiment,
)
from app.services.gis_harness.method_experiment.plan import (
    DEFAULT_MAX_TRIALS,
    MAX_TRIALS_HARD_CAP,
    MAX_CANDIDATES,
    MAX_VARIANTS_PER_CANDIDATE,
    PlanCompileError,
    compile_experiment_plan,
)
from app.services.gis_harness.method_experiment.protocol import (
    CV_SCHEMES,
)
from app.services.gis_harness.method_experiment.runner import (
    DEFAULT_MAX_TRIALS_PER_CALL,
)
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def register_method_experiment_tools(registry: ToolRegistry) -> None:
    """注册方法实验工具族（独立模块防并发冲突）。"""

    # ── 内部助手（共享服务层——与 REST 路由同链，UI 与 Agent 不分叉）──────
    from app.services.gis_harness.method_experiment.service import (
        load_state as _load_state,
    )

    # ── 工具 1：inspect（只读）──────────────────────────────────────────
    @tool(
        registry, name="webgis_method_experiment_inspect",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "只读检视科学方法实验能力面：按问题文本或族 id 返回方法卡"
            "（候选方法/参数角色与网格/评价协议/指标/可比性声明/局限）"
            "与预算预览。零执行、零副作用。"
            "\n何时用：用户问『该用哪种方法/参数』或准备设计方法比较前。"
            "\n何时不用：已确定单一方法（直接调对应分析工具）。"
        ),
        param_descriptions={
            "query": "自然语言问题（可选；词法提示匹配族）",
            "family_id": "显式方法族 id（可选；优先于 query 匹配）",
        },
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("方法实验", "方法卡", "选型", "检视", "experiment"),
        output_semantic_type="table",
        result_size_policy="bounded",
        failure_modes=("invalid_args",),
    )
    def webgis_method_experiment_inspect(
        query: str = "", family_id: str = "",
    ) -> dict:
        matches = match_family_by_question(query) if query else []
        resolved = ""
        if family_id:
            if family_id not in EXPERIMENT_FAMILY_IDS:
                return {
                    "success": False, "tool": "webgis_method_experiment_inspect",
                    "reason_code": "MEXP_UNKNOWN_FAMILY",
                    "message": f"未知族 {family_id}",
                    "alternatives": list(EXPERIMENT_FAMILY_IDS),
                }
            resolved = family_id
        elif matches:
            resolved = matches[0]

        cards_payload = []
        for card in all_cards():
            if resolved and card.family_id != resolved:
                continue
            cards_payload.append({
                "family_id": card.family_id,
                "label_zh": card.label_zh,
                "protocol_kind": card.protocol_kind,
                "default_scheme": card.default_scheme,
                "default_folds": card.default_folds,
                "default_metrics": list(card.default_metrics),
                "requires_value_field": card.requires_value_field,
                "extra_inputs": list(card.extra_inputs),
                "comparability_notes": list(card.comparability_notes)[:4],
                "candidates": [
                    {
                        "candidate_id": c.candidate_id,
                        "method_id": c.method_id,
                        "eval_tool": c.eval_tool,
                        "label_zh": c.label_zh,
                        "sample_floor": c.sample_floor,
                        "param_specs": {
                            name: {
                                "role": spec.role, "unit": spec.unit,
                                "default": spec.default,
                                "grid": list(spec.grid_values()),
                                "minimum": spec.minimum,
                                "maximum": spec.maximum,
                                "note": spec.note[:80],
                            }
                            for name, spec in c.param_specs.items()},
                        "metric_ids": list(c.metric_ids),
                        "limitations": list(c.limitations)[:3],
                        "default_variants": len(c.expand_grid()),
                    }
                    for c in card.candidates],
            })
        return {
            "success": True,
            "tool": "webgis_method_experiment_inspect",
            "query_matched_families": matches[:5],
            "resolved_family": resolved or None,
            "family_vocab": list(EXPERIMENT_FAMILY_IDS),
            "cards": cards_payload,
            "budget_defaults": {
                "max_trials": DEFAULT_MAX_TRIALS,
                "max_trials_hard_cap": MAX_TRIALS_HARD_CAP,
                "max_candidates": MAX_CANDIDATES,
                "max_variants_per_candidate": MAX_VARIANTS_PER_CANDIDATE,
                "cv_schemes": list(CV_SCHEMES),
            },
            "next_steps": (
                ["webgis_method_experiment_plan（编译计划，typed 预检）"]
                if resolved else
                ["再提供 query 或 family_id 以聚焦方法卡",
                 "webgis_method_experiment_plan 需显式 family_id"]),
        }

    # ── 工具 2：plan（确定性编译）───────────────────────────────────────
    @tool(
        registry, name="webgis_method_experiment_plan",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "编译方法实验计划：候选 × 参数网格 → 确定性试验清单（可版本化、"
            "可重放）。执行前 typed 预检（缺 ref/缺值字段/超预算 → 拒绝）。"
            "零执行——编译只产出计划。"
            "\n何时用：inspect 之后确定要比较的候选与协议。"
            "\n何时不用：只想跑单一方法（直接调分析工具）。"
        ),
        param_descriptions={
            "family_id": "方法族（interpolation/density/clustering/network_accessibility/spatial_autocorrelation）",
            "input_ref": "主输入 ref（点要素集 ref:xxx）",
            "value_field": "数值字段（cv/自相关族必需）",
            "extra_inputs": '族额外输入 JSON：{"network": "ref:...", "facilities": "ref:..."}',
            "candidate_ids": '候选 id JSON 数组（缺省=卡全候选）',
            "scheme": "折分配（cv 族）：spatial_block(默认)/index",
            "folds": "折数（2-10，默认 5）",
            "max_trials": "试验预算（默认 12，上限 24）",
            "session_id": "会话 id",
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("方法实验", "计划", "编译", "experiment"),
        output_semantic_type="table",
        result_size_policy="bounded",
        failure_modes=("invalid_args", "missing_data"),
    )
    async def webgis_method_experiment_plan(
        family_id: str,
        input_ref: str,
        value_field: str = "",
        extra_inputs: Optional[str] = None,
        candidate_ids: Optional[str] = None,
        scheme: str = "",
        folds: int = 5,
        max_trials: int = DEFAULT_MAX_TRIALS,
        title: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        tool_name = "webgis_method_experiment_plan"
        extra: Dict[str, str] = {}
        if extra_inputs:
            try:
                parsed = json.loads(extra_inputs) if isinstance(
                    extra_inputs, str) else dict(extra_inputs)
            except (json.JSONDecodeError, TypeError):
                return {"success": False, "tool": tool_name,
                        "reason_code": "MEXP_BAD_JSON",
                        "message": "extra_inputs 必须是 {role: ref} JSON"}
            if not isinstance(parsed, dict):
                return {"success": False, "tool": tool_name,
                        "reason_code": "MEXP_BAD_JSON",
                        "message": "extra_inputs 必须是对象"}
            extra = {str(k): str(v) for k, v in parsed.items()}
        cands: Optional[List[str]] = None
        if candidate_ids:
            try:
                parsed_c = json.loads(candidate_ids) if isinstance(
                    candidate_ids, str) else list(candidate_ids)
            except (json.JSONDecodeError, TypeError):
                return {"success": False, "tool": tool_name,
                        "reason_code": "MEXP_BAD_JSON",
                        "message": "candidate_ids 必须是字符串数组 JSON"}
            if (not isinstance(parsed_c, list)
                    or not all(isinstance(x, str) for x in parsed_c)):
                return {"success": False, "tool": tool_name,
                        "reason_code": "MEXP_BAD_JSON",
                        "message": "candidate_ids 必须是字符串数组（非裸字符串）"}
            cands = parsed_c

        try:
            plan = compile_experiment_plan(
                family_id=family_id,
                input_ref=input_ref,
                value_field=value_field,
                extra_inputs=extra,
                candidate_ids=cands,
                scheme=scheme,
                folds=folds,
                max_trials=max_trials,
                title=title,
            )
        except PlanCompileError as exc:
            return {"success": False, "tool": tool_name,
                    "reason_code": exc.code,
                    "message": str(exc),
                    "alternatives": ["webgis_method_experiment_inspect"]}

        if session_id:
            from app.services.gis_harness.method_experiment.service import (
                ensure_state_for_plan,
            )
            _, decline = await ensure_state_for_plan(session_id, plan)
            if decline:
                return {"success": False, "tool": tool_name,
                        "reason_code": decline,
                        "message": (
                            f"实验 id {plan.experiment_id} 已被不同内容的"
                            "计划占用（指纹失配）——改参数/候选得到新 id"),
                        "alternatives": ["webgis_method_experiment_status"]}
        summary = plan.to_bounded_summary()
        return {
            "success": True,
            "tool": tool_name,
            "plan": summary,
            "run_hint": (
                f"webgis_method_experiment_run(experiment_id="
                f"{plan.experiment_id}, session_id=...)——窗口有界可续跑"),
        }

    # ── 工具 3：run（真实 dispatch 执行窗口）────────────────────────────
    @tool(
        registry, name="webgis_method_experiment_run",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "执行方法实验的 pending 试验：每个试验经真实工具分发（单一执行"
            "真相），内容指纹一致的结果直接复用。窗口有界（试验数/墙钟），"
            "可多次调用续跑；单试验失败不影响其余；取消保留已完成部分。"
            "\n何时用：plan 之后。"
            "\n何时不用：未经 plan（先编译计划）。"
        ),
        param_descriptions={
            "experiment_id": "plan 返回的实验 id",
            "max_trials_per_call": "单次调用窗口（默认 6，上限 6——调小可控节奏）",
            "session_id": "会话 id",
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="slow",
        memory_class="heavy",
        scale_class="large",
        tags=("方法实验", "执行", "run", "evidence"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "missing_data", "memory"),
    )
    async def webgis_method_experiment_run(
        experiment_id: str,
        max_trials_per_call: int = DEFAULT_MAX_TRIALS_PER_CALL,
        session_id: Optional[str] = None,
    ) -> dict:
        tool_name = "webgis_method_experiment_run"
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "tool": tool_name}
        state = await _load_state(session_id, experiment_id)
        if state is None or not state.experiment_id:
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": "MEXP_EXPERIMENT_NOT_FOUND",
                    "message": f"实验 {experiment_id} 不存在——先 plan",
                    "alternatives": ["webgis_method_experiment_plan",
                                     "webgis_method_experiment_inspect"]}
        from app.services.gis_harness.method_experiment.service import (
            run_window,
        )
        state, decline = await run_window(
            session_id, experiment_id,
            max_trials_per_call=int(max_trials_per_call),
        )
        if state is None:
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": decline,
                    "message": {
                        "MEXP_EXPERIMENT_NOT_FOUND":
                            f"实验 {experiment_id} 不存在——先 plan",
                        "MEXP_PLAN_SNAPSHOT_CORRUPT":
                            "计划快照不可解析（需重新 plan）",
                        "MEXP_PLAN_MISMATCH":
                            "计划快照与实验 id 不符（需重新 plan）",
                        "MEXP_REGISTRY_UNAVAILABLE":
                            "工具注册表/分发服务不可用",
                    }.get(decline, decline),
                    "alternatives": (
                        ["webgis_method_experiment_plan"]
                        if decline.startswith("MEXP_PLAN")
                        or decline == "MEXP_EXPERIMENT_NOT_FOUND" else [])}

        counts = state.counts()
        return {
            "success": state.status in ("completed",),
            "tool": tool_name,
            "experiment_id": experiment_id,
            "status": state.status,
            "counts": counts,
            "total_wall_ms": state.total_wall_ms(),
            "trials": [
                {"trial_id": t.trial_id, "candidate_id": t.candidate_id,
                 "state": t.state, "metrics": dict(t.metrics),
                 "reason_code": t.reason_code}
                for t in state.trials.values()],
            "run_log_tail": state.run_log[-6:],
            "next_steps": (
                ["webgis_method_experiment_compare（排名与推荐）"]
                if state.status == "completed" else
                ["继续 webgis_method_experiment_run 续跑剩余试验"]),
        }

    # ── 工具 4：status（只读投影）───────────────────────────────────────
    @tool(
        registry, name="webgis_method_experiment_status",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "只读投影方法实验状态：试验清单与结局（completed/reused/"
            "not_evaluated/failed…）、真实成本（wall_ms）、采纳回执。"
            "不执行、不排名。"
        ),
        param_descriptions={
            "experiment_id": "实验 id", "session_id": "会话 id",
        },
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("方法实验", "状态", "检视"),
        output_semantic_type="table",
        result_size_policy="bounded",
        failure_modes=("invalid_args",),
    )
    async def webgis_method_experiment_status(
        experiment_id: str, session_id: Optional[str] = None,
    ) -> dict:
        tool_name = "webgis_method_experiment_status"
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "tool": tool_name}
        state = await _load_state(session_id, experiment_id)
        if state is None:
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": "MEXP_EXPERIMENT_NOT_FOUND",
                    "message": f"实验 {experiment_id} 不存在",
                    "alternatives": ["webgis_method_experiment_plan"]}
        return {
            "success": True,
            "tool": tool_name,
            "experiment_id": experiment_id,
            "status": state.status,
            "counts": state.counts(),
            "total_wall_ms": state.total_wall_ms(),
            "adoption": (
                state.adoption.model_dump() if state.adoption else None),
            "trials": [
                {"trial_id": t.trial_id, "candidate_id": t.candidate_id,
                 "variant_index": t.variant_index, "params": dict(t.params),
                 "state": t.state, "metrics": dict(t.metrics),
                 "basis": {k: dict(v) for k, v in
                           list(t.metric_basis.items())[:4]},
                 "reason_code": t.reason_code, "message": t.message[:160],
                 "wall_ms": t.wall_ms, "attempts": t.attempts,
                 "fingerprint": t.content_fingerprint}
                for t in state.trials.values()],
            "run_log_tail": state.run_log[-8:],
        }

    # ── 工具 5：compare（评价/排名/推荐）────────────────────────────────
    @tool(
        registry, name="webgis_method_experiment_compare",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "评价方法实验结果：同协议+同指标的可比组确定性排名（并列披露）、"
            "Pareto 前沿、证据推荐或诚实弃权（未评估绝不冒充排名）。"
            "\n何时用：≥2 个试验完成后。"
            "\n何时不用：单一候选没有比较语义。"
        ),
        param_descriptions={
            "experiment_id": "实验 id", "session_id": "会话 id",
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("方法实验", "比较", "排名", "推荐"),
        output_semantic_type="table",
        result_size_policy="bounded",
        failure_modes=("invalid_args",),
    )
    async def webgis_method_experiment_compare(
        experiment_id: str, session_id: Optional[str] = None,
    ) -> dict:
        tool_name = "webgis_method_experiment_compare"
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "tool": tool_name}
        state = await _load_state(session_id, experiment_id)
        if state is None or not state.experiment_id:
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": "MEXP_EXPERIMENT_NOT_FOUND",
                    "message": f"实验 {experiment_id} 不存在",
                    "alternatives": ["webgis_method_experiment_plan"]}
        from app.services.gis_harness.method_experiment.service import (
            rebuild_plan,
        )
        plan, decline = rebuild_plan(state)
        if plan is None:
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": decline,
                    "message": "计划快照不可解析或与实验 id 不符（需重新 plan）",
                    "alternatives": ["webgis_method_experiment_plan"]}
        evaluation = evaluate_experiment(plan, state)
        return {
            "success": True,
            "tool": tool_name,
            "evaluation": evaluation.to_bounded_dict(),
            "adopt_hint": (
                "webgis_method_experiment_adopt(trial_id=..., session_id=...)"
                "——采纳是用户决定，记录回执不自动改图"),
        }

    # ── 工具 6：adopt（用户决定回执）────────────────────────────────────
    @tool(
        registry, name="webgis_method_experiment_adopt",
        capabilities=["plan_workflow_orchestration"],
        tier=2, domains=["analysis"],
        description=(
            "记录用户对实验结果的方法采纳决定（decision receipt）：保存"
            "所选候选 + 证据快照 + 用户理由。不自动修改地图/数据——后续"
            "产品化走既有分析/制图工具链。"
            "\n何时用：用户明确选择某候选后。"
            "\n何时不用：未完成试验或用户未做选择（绝不代用户决定）。"
        ),
        param_descriptions={
            "experiment_id": "实验 id",
            "trial_id": "采纳的试验 id（必须已完成）",
            "reason": "用户理由（可选，有界）",
            "session_id": "会话 id",
        },
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("方法实验", "采纳", "决定"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args",),
    )
    async def webgis_method_experiment_adopt(
        experiment_id: str,
        trial_id: str,
        reason: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        tool_name = "webgis_method_experiment_adopt"
        if not session_id:
            return {"success": False, "message": "Missing session_id",
                    "tool": tool_name}
        from app.services.gis_harness.method_experiment.service import (
            record_adoption,
        )
        receipt, decline = await record_adoption(
            session_id, experiment_id, trial_id, reason)
        if receipt is None:
            messages = {
                "MEXP_EXPERIMENT_NOT_FOUND": f"实验 {experiment_id} 不存在",
                "MEXP_PLAN_SNAPSHOT_CORRUPT": "计划快照不可解析",
                "MEXP_TRIAL_NOT_FOUND": "采纳目标试验不存在",
                "MEXP_TRIAL_NOT_COMPLETED":
                    "采纳目标试验未完成——未评估的候选绝不进入采纳回执",
            }
            return {"success": False, "tool": tool_name, "declined": True,
                    "reason_code": decline,
                    "message": messages.get(decline, decline),
                    "alternatives": ["webgis_method_experiment_status",
                                     "webgis_method_experiment_compare"]}
        return {
            "success": True,
            "tool": tool_name,
            "experiment_id": experiment_id,
            "adoption": receipt.model_dump(),
            "note": "回执已记录（decision receipt）；后续产品化请走既有"
                    "分析/制图工具链（本工具不改地图）",
        }
