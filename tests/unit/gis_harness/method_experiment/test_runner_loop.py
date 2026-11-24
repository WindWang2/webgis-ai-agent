"""试验执行循环回归锁（method-experiment WP3）—— fake dispatch 的诚实语义。

用注入 dispatch（不依赖 Pi 桥）验证：

- 真实调用评价工具与参数（args 形状 = family 适配器契约）；
- 指标提取（completed）+ 未评估（not_evaluated≠失败≠零分）；
- 工具失败 → 该试验 failed、其余继续（单候选失败不误报整体成功）；
- 内容寻址复用：同指纹第二候选标 reused、不重复 dispatch；
- 取消：已完成部分保留、剩余 cancelled；
- 窗口预算：pending 保留 + partial（可续跑）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.services.gis_harness.method_experiment.plan import (
    compile_experiment_plan,
)
from app.services.gis_harness.method_experiment.runner import (
    ExperimentRunner,
    trial_content_fingerprint,
)
from app.services.gis_harness.method_experiment.state import new_state_from_plan


class FakeResult:
    def __init__(self, status="ok", raw=None, error_msg=None):
        self.status = status
        self.raw_result = raw
        self.error_msg = error_msg


class DispatchRecorder:
    """记录调用的 fake dispatch（按工具名/序号返回脚本化结果）。"""

    def __init__(self, script: Optional[List[Any]] = None):
        self.calls: List[Dict[str, Any]] = []
        self.script = list(script or [])

    async def __call__(self, tool_name, args, session_id):
        self.calls.append({"tool": tool_name, "args": dict(args),
                           "session": session_id})
        default = FakeResult(status="ok", raw={
            "success": True, "rmse": 1.0, "mae": 0.8, "bias": 0.1,
            "n_samples": 40, "folds_used": 4, "scheme": "spatial_block"})
        if self.script:
            item = self.script.pop(0)
            if item is None:
                return default
            if isinstance(item, Exception):
                raise item
            return item
        return default


def _plan_and_state(**kw):
    plan = compile_experiment_plan(
        family_id="interpolation", input_ref="ref:geojson-x",
        value_field="v", candidate_ids=["idw", "ordinary_kriging"],
        max_trials=6, **kw)
    state = new_state_from_plan(plan)
    return plan, state


async def _persist_noop(state):
    return None


@pytest.mark.asyncio
async def test_run_extracts_metrics_and_calls_real_tools():
    rec = DispatchRecorder()
    runner = ExperimentRunner(dispatch=rec, persist=_persist_noop)
    plan, state = _plan_and_state()
    state = await runner.run("sess", plan, state)

    assert state.status == "completed"
    assert len(rec.calls) == 4          # idw×3 + kriging×1 全部 dispatch
    first = rec.calls[0]
    assert first["tool"] == "interpolation_block_cv"
    assert first["args"]["geojson"] == "ref:geojson-x"   # ref 直通（dispatch 解引用）
    assert first["args"]["value_field"] == "v"
    assert first["args"]["scheme"] == "spatial_block"
    completed = [t for t in state.trials.values() if t.state == "completed"]
    assert len(completed) == 4
    assert all("cv_rmse" in t.metrics for t in completed)
    # 协议裁剪：basis 只含协议指标
    assert all("cv_rmse" in t.metric_basis for t in completed)


@pytest.mark.asyncio
async def test_tool_failure_is_isolated_and_honest():
    script = [FakeResult(status="error", error_msg="boom")] + [None] * 5
    rec = DispatchRecorder(script=script)
    runner = ExperimentRunner(dispatch=rec, persist=_persist_noop)
    plan, state = _plan_and_state()
    state = await runner.run("sess", plan, state)

    failed = [t for t in state.trials.values() if t.state == "failed"]
    completed = [t for t in state.trials.values() if t.state == "completed"]
    assert len(failed) == 1
    assert failed[0].reason_code == "MEXP_TOOL_ERROR"
    assert "boom" in failed[0].message
    assert len(completed) == 3
    assert state.status == "completed"   # 其余完成 = 实验整体完成


@pytest.mark.asyncio
async def test_tool_success_false_is_failure_not_pass():
    script = [FakeResult(status="ok", raw={"success": False,
                                           "summary": "样本不足"})] + [None] * 5
    rec = DispatchRecorder(script=script)
    runner = ExperimentRunner(dispatch=rec, persist=_persist_noop)
    plan, state = _plan_and_state()
    state = await runner.run("sess", plan, state)
    failed = [t for t in state.trials.values() if t.state == "failed"]
    assert len(failed) == 1
    assert failed[0].reason_code == "MEXP_TOOL_REPORTED_FAILURE"


@pytest.mark.asyncio
async def test_missing_metrics_is_not_evaluated():
    script = [FakeResult(status="ok", raw={"success": True,
                                           "summary": "无可行方法"})] + [None] * 5
    rec = DispatchRecorder(script=script)
    runner = ExperimentRunner(dispatch=rec, persist=_persist_noop)
    plan, state = _plan_and_state()
    state = await runner.run("sess", plan, state)
    ne = [t for t in state.trials.values() if t.state == "not_evaluated"]
    assert len(ne) == 1
    assert ne[0].reason_code == "MEXP_METRICS_ABSENT"
    assert ne[0].metrics == {}


@pytest.mark.asyncio
async def test_dispatch_exception_marks_trial_failed():
    script = [RuntimeError("network down")] + [None] * 5
    rec = DispatchRecorder(script=script)
    runner = ExperimentRunner(dispatch=rec, persist=_persist_noop)
    plan, state = _plan_and_state()
    state = await runner.run("sess", plan, state)
    failed = [t for t in state.trials.values() if t.state == "failed"]
    assert failed[0].reason_code == "MEXP_DISPATCH_EXCEPTION"


@pytest.mark.asyncio
async def test_identical_variants_reuse_content_fingerprint():
    """两个候选若内容完全一致（同工具/参数/协议）→ 第二个复用不 dispatch。"""
    plan = compile_experiment_plan(
        family_id="interpolation", input_ref="ref:geojson-x",
        value_field="v", candidate_ids=["idw"], max_trials=6)
    # 人为克隆 idw 的一个变体（同参数组）制造同内容试验
    from app.services.gis_harness.method_experiment.plan import Trial
    plan = plan.model_copy(update={"trials": (
        plan.trials[0],
        Trial(trial_id="t02", candidate_id="idw", variant_index=1,
              params=dict(plan.trials[0].params), seed=0,
              eval_tool=plan.trials[0].eval_tool),
    )})
    state = new_state_from_plan(plan)
    rec = DispatchRecorder()
    runner = ExperimentRunner(dispatch=rec, persist=_persist_noop)
    state = await runner.run("sess", plan, state)

    assert len(rec.calls) == 1          # 只 dispatch 一次
    states = {t.trial_id: t.state for t in state.trials.values()}
    assert states["t01"] == "completed"
    assert states["t02"] == "reused"
    reused = state.trials["t02"]
    assert reused.metrics == state.trials["t01"].metrics


@pytest.mark.asyncio
async def test_window_budget_preserves_pending():
    rec = DispatchRecorder()
    runner = ExperimentRunner(dispatch=rec, persist=_persist_noop)
    plan, state = _plan_and_state()
    state = await runner.run("sess", plan, state, max_trials_per_call=2)
    assert len(rec.calls) == 2
    counts = state.counts()
    assert counts.get("pending", 0) == 2
    assert state.status == "partial"
    # 续跑剩余
    state = await runner.run("sess", plan, state, max_trials_per_call=6)
    assert len(rec.calls) == 4
    assert state.status == "completed"


@pytest.mark.asyncio
async def test_cancellation_keeps_completed_prefix():
    rec = DispatchRecorder()
    runner = ExperimentRunner(dispatch=rec, persist=_persist_noop)
    plan, state = _plan_and_state()
    calls = {"n": 0}

    def cancel_after_two():
        calls["n"] += 1
        return calls["n"] > 2          # 第 3 个试验前取消

    state = await runner.run("sess", plan, state, cancel_check=cancel_after_two)
    counts = state.counts()
    assert counts.get("completed", 0) == 2
    assert counts.get("cancelled", 0) == 1     # 边界试验取消终态
    assert counts.get("pending", 0) == 1       # 未开始的保持 pending（可续跑）
    assert state.status == "cancelled"
    assert len(rec.calls) == 2                 # 取消后零新 dispatch


def test_fingerprint_changes_with_input_and_params():
    plan1 = compile_experiment_plan(
        family_id="interpolation", input_ref="ref:a", value_field="v",
        candidate_ids=["idw"])
    plan2 = compile_experiment_plan(
        family_id="interpolation", input_ref="ref:b", value_field="v",
        candidate_ids=["idw"])
    t1 = trial_content_fingerprint(plan1, {"method": "idw", "power": 2.0}, 0,
                                   "interpolation_block_cv")
    t2 = trial_content_fingerprint(plan2, {"method": "idw", "power": 2.0}, 0,
                                   "interpolation_block_cv")
    t3 = trial_content_fingerprint(plan1, {"method": "idw", "power": 3.0}, 0,
                                   "interpolation_block_cv")
    assert t1 != t2                     # 输入变了 = 不同内容
    assert t1 != t3                     # 参数变了 = 不同内容
    assert trial_content_fingerprint(
        plan1, {"method": "idw", "power": 2.0}, 0,
        "interpolation_block_cv") == t1  # 确定性
