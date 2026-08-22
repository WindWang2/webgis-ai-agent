"""ChatEngine 规划阶段集成测试。"""
import pytest

from app.tools.registry import ToolRegistry
from app.services.tool_catalog import ToolCatalog
from app.services.chat_engine import ChatEngine
from app.services.chat import planner as planner_mod


@pytest.fixture
def engine(monkeypatch):
    reg = ToolRegistry()
    reg.register("buffer_analysis", "buffer", func=lambda **_: {})
    eng = ChatEngine(reg, tool_catalog=ToolCatalog(reg))
    async def fake_get_or_create_session(session_id, user_id=None):
        return [{"role": "system", "content": eng._build_system_prompt()}]
    async def fake_save_msg_async(*a, **k):
        pass
    async def fake_generate_title(*a, **k):
        pass
    monkeypatch.setattr(eng, "_get_or_create_session", fake_get_or_create_session)
    monkeypatch.setattr(eng, "_save_msg_async", fake_save_msg_async)
    monkeypatch.setattr(eng, "_generate_title", fake_generate_title)
    return eng


def test_planner_llm_config_uses_planner_model(engine, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "LLM_PLANNER_MODEL", "cheap-model")
    cfg = engine._planner_llm_config()
    assert cfg.model == "cheap-model"


def test_planner_llm_config_falls_back_to_main_model(engine, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "LLM_PLANNER_MODEL", "")
    cfg = engine._planner_llm_config()
    assert cfg.model == engine.model


@pytest.mark.asyncio
async def test_maybe_plan_runs_for_complex_request(engine, monkeypatch):
    captured = {}
    async def fake_make_plan(cfg, session_id, message, env):
        captured["called"] = True
        plan = planner_mod.Plan(intent="x", domains=["core"], steps=[])
        planner_mod.set_plan(session_id, plan)
        return plan
    monkeypatch.setattr(planner_mod, "make_plan", fake_make_plan)
    await engine._maybe_plan("sess-P1", "分析成都市三甲医院的空间分布并做热点检测", [])
    assert captured.get("called") is True
    planner_mod.clear_plan("sess-P1")


@pytest.mark.asyncio
async def test_maybe_plan_skips_short_followup(engine, monkeypatch):
    planner_mod.set_plan("sess-P2", planner_mod.Plan(intent="x", domains=["core"], steps=[]))
    captured = {}
    async def fake_make_plan(*a, **k):
        captured["called"] = True
    monkeypatch.setattr(planner_mod, "make_plan", fake_make_plan)
    await engine._maybe_plan("sess-P2", "换个颜色", [])
    assert "called" not in captured   # 短追问 + 有计划 → 跳过规划
    planner_mod.clear_plan("sess-P2")


def test_log_tool_decision_accepts_step_n_parameter(engine, tmp_path, monkeypatch):
    """_log_tool_decision 必须接收 step_n 参数而不是内部计算。"""
    from app.services.chat import decision_log
    from app.services.tool_dispatch_service import ToolDispatchResult
    captured: list = []
    monkeypatch.setattr(decision_log, "log_tool_decision",
                        lambda rec, **_kw: captured.append(rec))
    # 直接调用 _log_tool_decision，传入 step_n=2
    engine._log_tool_decision(
        session_id="sess-T1",
        round_index=0,
        message="test",
        tool_name="buffer_analysis",
        tool_args={},
        outcome=ToolDispatchResult(
            status="ok",
            llm_payload="ok",
            slim_event={"ok": True},
            geojson_ref=None,
            raw_result={"ok": True},
            error_msg=None,
        ),
        subset_size=5,
        step_n=2,
    )
    assert len(captured) == 1
    assert captured[0].plan_step_matched == 2


@pytest.mark.asyncio
async def test_maybe_plan_returns_plan_on_success(engine, monkeypatch):
    """_maybe_plan 成功生成计划时必须返回 Plan 对象。"""
    expected_plan = planner_mod.Plan(intent="test", domains=["core"], steps=[])
    async def fake_make_plan(cfg, session_id, message, env):
        planner_mod.set_plan(session_id, expected_plan)
        return expected_plan
    monkeypatch.setattr(planner_mod, "make_plan", fake_make_plan)
    result = await engine._maybe_plan("sess-R1", "复杂请求需要规划的内容", [])
    assert result is expected_plan
    planner_mod.clear_plan("sess-R1")


@pytest.mark.asyncio
async def test_maybe_plan_returns_none_when_skipped(engine, monkeypatch):
    """should_plan 返回 False 时，_maybe_plan 返回 None。"""
    planner_mod.set_plan("sess-R2", planner_mod.Plan(intent="x", domains=["core"], steps=[]))
    result = await engine._maybe_plan("sess-R2", "换颜色", [])  # 短追问，应该跳过
    assert result is None
    planner_mod.clear_plan("sess-R2")


@pytest.mark.asyncio
async def test_maybe_plan_returns_none_on_llm_failure(engine, monkeypatch):
    """make_plan 抛异常时，_maybe_plan 返回 None（不传播异常）。"""
    async def fake_make_plan(*a, **k):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(planner_mod, "make_plan", fake_make_plan)
    result = await engine._maybe_plan("sess-R3", "复杂请求需要规划的内容", [])
    assert result is None


@pytest.mark.asyncio
async def test_chat_stream_emits_plan_ready_when_plan_created(engine, monkeypatch):
    """chat_stream 在 _maybe_plan 成功后必须发 plan_ready SSE 事件。"""
    test_plan = planner_mod.Plan(
        intent="测试意图",
        domains=["core", "chinese"],
        steps=[
            planner_mod.PlanStep(n=1, goal="获取边界", tool_family="chinese"),
            planner_mod.PlanStep(n=2, goal="出热力图", tool_family="core", done=True),
        ],
    )
    async def fake_maybe_plan(self, session_id, message, messages):
        planner_mod.set_plan(session_id, test_plan)
        return test_plan
    monkeypatch.setattr(engine, "_maybe_plan",
                        fake_maybe_plan.__get__(engine, type(engine)))
    # 让主 LLM 调用立即结束（不进工具循环），简化测试
    async def fake_llm_stream(*a, **k):
        if False:
            yield
        # 直接 yield 一个 done event
        yield ("done", {"message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop"})
    monkeypatch.setattr(engine, "_call_llm_stream", fake_llm_stream)

    captured = []
    async for ev in engine.chat_stream("复杂请求需要规划的内容", session_id="sess-EV1"):
        captured.append(ev)

    joined = "".join(captured)
    assert "event: plan_ready" in joined
    assert "测试意图" in joined
    # 验证 steps 数组与字段
    import json
    plan_ready_chunks = [c for c in captured if c.startswith("event: plan_ready")]
    assert len(plan_ready_chunks) == 1
    data_line = [line for line in plan_ready_chunks[0].splitlines() if line.startswith("data:")][0]
    data = json.loads(data_line[len("data:"):].strip())
    assert data["intent"] == "测试意图"
    assert data["domains"] == ["core", "chinese"]
    assert len(data["steps"]) == 2
    # P3-2/P2-6：done 取投影真实状态（step2 已完成 → True），不硬编码 False
    assert data["steps"][0] == {"n": 1, "goal": "获取边界", "tool_family": "chinese", "done": False}
    assert data["steps"][1] == {"n": 2, "goal": "出热力图", "tool_family": "core", "done": True}
    planner_mod.clear_plan("sess-EV1")


@pytest.mark.asyncio
async def test_chat_stream_emits_plan_step_done_after_tool(engine, monkeypatch):
    """工具执行命中计划步骤后必须发 plan_step_done。"""
    # 预置一个 Plan，标 buffer_analysis 工具 domain 是 core，匹配 step 1
    test_plan = planner_mod.Plan(
        intent="x", domains=["core"],
        steps=[planner_mod.PlanStep(n=1, goal="缓冲", tool_family="core")],
    )
    planner_mod.set_plan("sess-EV2", test_plan)
    # 让 _maybe_plan 不再二次规划
    async def fake_maybe_plan(self, *a, **k): return None
    monkeypatch.setattr(engine, "_maybe_plan",
                        fake_maybe_plan.__get__(engine, type(engine)))
    # 让 registry.metadata 返回 domains=["core"]，使 mark_step_done 能匹配
    monkeypatch.setattr(engine.registry, "metadata",
                        lambda name: {"domains": ["core"]})

    # 让主 LLM 第一轮返回一个 tool_call，第二轮立刻 done
    call_count = {"n": 0}
    async def fake_llm_stream(*a, **k):
        call_count["n"] += 1
        if call_count["n"] == 1:
            yield ("done", {"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "tc1", "type": "function",
                                "function": {"name": "buffer_analysis",
                                             "arguments": "{}"}}],
            }, "finish_reason": "tool_calls"})
        else:
            yield ("done", {"message": {"role": "assistant", "content": "done"},
                            "finish_reason": "stop"})
    monkeypatch.setattr(engine, "_call_llm_stream", fake_llm_stream)
    # 让 dispatch 不实际跑工具
    from app.services.tool_dispatch_service import ToolDispatchResult
    async def fake_dispatch(self, *a, **k):
        return ToolDispatchResult(
            status="ok",
            llm_payload="{}",
            slim_event={"ok": True},
            geojson_ref=None,
            raw_result={"ok": True},
            error_msg=None,
        )
    monkeypatch.setattr(engine, "_dispatch_tool",
                        fake_dispatch.__get__(engine, type(engine)))

    captured = []
    async for ev in engine.chat_stream("缓冲分析", session_id="sess-EV2"):
        captured.append(ev)

    joined = "".join(captured)
    assert "event: plan_step_done" in joined
    import json
    chunks = [c for c in captured if c.startswith("event: plan_step_done")]
    assert len(chunks) == 1
    data = json.loads([line for line in chunks[0].splitlines() if line.startswith("data:")][0][len("data:"):])
    assert data["step_n"] == 1
    planner_mod.clear_plan("sess-EV2")


@pytest.mark.asyncio
async def test_chat_stream_emits_plan_finalized_with_skipped(engine, monkeypatch):
    """task_complete 前必须发 plan_finalized，未打勾步骤进 skipped。"""
    test_plan = planner_mod.Plan(
        intent="x", domains=["core"],
        steps=[
            planner_mod.PlanStep(n=1, goal="a", tool_family="core", done=True),
            planner_mod.PlanStep(n=2, goal="b", tool_family="core", done=True),
            planner_mod.PlanStep(n=3, goal="c", tool_family="core", done=False),
        ],
    )
    planner_mod.set_plan("sess-EV3", test_plan)
    async def fake_maybe_plan(self, *a, **k): return None
    monkeypatch.setattr(engine, "_maybe_plan",
                        fake_maybe_plan.__get__(engine, type(engine)))
    async def fake_llm_stream(*a, **k):
        yield ("done", {"message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop"})
    monkeypatch.setattr(engine, "_call_llm_stream", fake_llm_stream)

    captured = []
    async for ev in engine.chat_stream("anything", session_id="sess-EV3"):
        captured.append(ev)

    joined = "".join(captured)
    assert "event: plan_finalized" in joined
    # plan_finalized 必须出现在 task_complete 之前
    pf_idx = joined.find("event: plan_finalized")
    tc_idx = joined.find("event: task_complete")
    assert pf_idx >= 0 and tc_idx >= 0 and pf_idx < tc_idx
    import json
    chunks = [c for c in captured if c.startswith("event: plan_finalized")]
    data = json.loads([line for line in chunks[0].splitlines() if line.startswith("data:")][0][len("data:"):])
    assert data["skipped"] == [3]
    planner_mod.clear_plan("sess-EV3")


@pytest.mark.asyncio
async def test_chat_stream_no_plan_events_when_plan_skipped(engine, monkeypatch):
    """_maybe_plan 返回 None 且无已有 plan 时，SSE 流不能出现任何 plan_* 事件。"""
    planner_mod.clear_plan("sess-EV4")  # 确保无残留
    async def fake_maybe_plan(self, *a, **k): return None
    monkeypatch.setattr(engine, "_maybe_plan",
                        fake_maybe_plan.__get__(engine, type(engine)))
    async def fake_llm_stream(*a, **k):
        yield ("done", {"message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop"})
    monkeypatch.setattr(engine, "_call_llm_stream", fake_llm_stream)

    captured = []
    async for ev in engine.chat_stream("x", session_id="sess-EV4"):
        captured.append(ev)
    joined = "".join(captured)
    assert "event: plan_ready" not in joined
    assert "event: plan_step_done" not in joined
    assert "event: plan_finalized" not in joined


@pytest.mark.asyncio
async def test_chat_stream_failed_tool_does_not_advance_plan(engine, monkeypatch):
    """R2（design-v3 §2）：工具失败（status=error）绝不打勾计划步骤，也不发
    plan_step_done；step_error 附带 failure_class/recovery_action（additive）。"""
    test_plan = planner_mod.Plan(
        intent="x", domains=["statistics"],
        steps=[planner_mod.PlanStep(n=1, goal="热点", tool_family="statistics")],
    )
    planner_mod.set_plan("sess-EV5", test_plan)
    async def fake_maybe_plan(self, *a, **k): return None
    monkeypatch.setattr(engine, "_maybe_plan",
                        fake_maybe_plan.__get__(engine, type(engine)))
    monkeypatch.setattr(engine.registry, "metadata",
                        lambda name: {"domains": ["statistics"]})

    call_count = {"n": 0}
    async def fake_llm_stream(*a, **k):
        call_count["n"] += 1
        if call_count["n"] == 1:
            yield ("done", {"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "tc1", "type": "function",
                                "function": {"name": "hotspot_analysis",
                                             "arguments": "{}"}}],
            }, "finish_reason": "tool_calls"})
        else:
            yield ("done", {"message": {"role": "assistant", "content": "done"},
                            "finish_reason": "stop"})
    monkeypatch.setattr(engine, "_call_llm_stream", fake_llm_stream)

    from app.services.tool_dispatch_service import ToolDispatchResult
    async def fake_dispatch(self, *a, **k):
        return ToolDispatchResult(
            status="error",
            llm_payload="boom",
            slim_event={"error": "boom"},
            geojson_ref=None,
            raw_result={"success": False, "code": "TOOL_ERROR", "message": "boom"},
            error_msg="boom",
        )
    monkeypatch.setattr(engine, "_dispatch_tool",
                        fake_dispatch.__get__(engine, type(engine)))

    captured = []
    async for ev in engine.chat_stream("热点分析", session_id="sess-EV5"):
        captured.append(ev)

    joined = "".join(captured)
    assert "event: plan_step_done" not in joined
    assert test_plan.steps[0].done is False  # 失败不打勾
    assert "event: step_error" in joined
    assert "failure_class" in joined  # additive 分类字段
    planner_mod.clear_plan("sess-EV5")


@pytest.mark.asyncio
async def test_maybe_plan_new_goal_supersedes_old(engine, monkeypatch):
    """design-v3 §2/§4：新目标（"换成查路线"）→ 旧 canonical 计划 superseded，
    新计划接管（修复场景 8 短消息换目标被门控吞掉）。"""
    from app.services.planning import PlanStatus
    from app.services.planning.store import plan_store

    old = planner_mod.Plan(
        intent="旧", domains=["raster"],
        steps=[planner_mod.PlanStep(n=1, goal="NDVI", tool_family="raster")],
    )
    planner_mod.set_plan("sess-S1", old)
    old_canon = plan_store.peek("sess-S1")
    old_id = old_canon.plan_id

    async def fake_call_llm(_cfg, _messages, _tools=None):
        return {"choices": [{"message": {"content":
            '{"intent":"新","domains":["network"],"steps":[{"n":1,"goal":"路线","tool_family":"network"}]}'}}]}

    monkeypatch.setattr(planner_mod, "call_llm", fake_call_llm)

    plan = await engine._maybe_plan("sess-S1", "换成查路线", [])
    assert plan is not None and plan.intent == "新"
    hist = await plan_store.get_by_id("sess-S1", old_id)
    assert hist is not None and hist.status == PlanStatus.superseded
    planner_mod.clear_plan("sess-S1")
    await plan_store.clear("sess-S1")


# ─────────────────────────────────────────────────────────────────────────
# P3 延后项 #4：mid-turn tick flush——mark_step_done 打勾后立即 best-effort
# 落盘，不依赖回合末 flush（崩溃/断连不丢已打勾步骤）
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mid_turn_tick_flush_persists_step(engine, monkeypatch):
    """工具打勾计划步骤后立即落盘：即使回合末 flush 崩溃（模拟），plan_store
    里已能看到该步骤 completed——tick 持久化独立于回合末 flush。"""
    from app.services.planning.models import PlanStatus, StepStatus
    from app.services.planning.store import plan_store

    # 两步计划：本轮只完成 step1 → 计划仍非终态，回合末 flush 会真正执行
    # （若单步计划打勾后即 completed，回合末 flush 会被 get_plan 终态过滤
    # 正确跳过，无法验证"崩溃后 tick 已落盘"）。
    test_plan = planner_mod.Plan(
        intent="x", domains=["core"],
        steps=[
            planner_mod.PlanStep(n=1, goal="缓冲", tool_family="core"),
            planner_mod.PlanStep(n=2, goal="更多", tool_family="core"),
        ],
    )
    planner_mod.set_plan("sess-EV6", test_plan)
    async def fake_maybe_plan(self, *a, **k): return None
    monkeypatch.setattr(engine, "_maybe_plan",
                        fake_maybe_plan.__get__(engine, type(engine)))
    monkeypatch.setattr(engine.registry, "metadata",
                        lambda name: {"domains": ["core"]})

    call_count = {"n": 0}
    async def fake_call_llm(*a, **k):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "tc1", "type": "function",
                                "function": {"name": "buffer_analysis",
                                             "arguments": "{}"}}],
            }}]}
        return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}
    monkeypatch.setattr(engine, "_call_llm", fake_call_llm)

    from app.services.tool_dispatch_service import ToolDispatchResult
    async def fake_dispatch(self, *a, **k):
        return ToolDispatchResult(
            status="ok", llm_payload="{}", slim_event={"ok": True},
            geojson_ref=None, raw_result={"ok": True}, error_msg=None,
        )
    monkeypatch.setattr(engine, "_dispatch_tool",
                        fake_dispatch.__get__(engine, type(engine)))

    # 模拟"回合末 flush 崩溃"：第 2 次 flush（turn-end，finally）抛异常被吞；
    # 第 1 次（tick flush——工具打勾后立即触发）正常落盘。
    from app.services.chat.plan_orchestrator import plan_orchestrator
    real_flush = plan_orchestrator.flush
    flush_calls = {"n": 0}
    async def flaky_flush(session_id):
        flush_calls["n"] += 1
        if flush_calls["n"] >= 2:
            raise RuntimeError("simulated crash before turn-end flush")
        await real_flush(session_id)
    monkeypatch.setattr(plan_orchestrator, "flush", flaky_flush)

    result = await engine.chat("缓冲分析", session_id="sess-EV6")
    assert result["content"] == "done"
    # 回合正常完成（best-effort：turn-end flush 崩溃只记 warning，不破坏回合）
    assert flush_calls["n"] == 2

    # 模拟重启：清空进程缓存后从 store 读回 → 步骤 completed（来自 tick flush）
    plan_store.clear_cache()
    persisted = await plan_store.load_current("sess-EV6")
    assert persisted is not None
    assert persisted.steps[0].status == StepStatus.completed
    assert persisted.steps[1].status == StepStatus.pending
    assert persisted.status == PlanStatus.proposed  # 仍有 pending → 非终态
    planner_mod.clear_plan("sess-EV6")
    await plan_store.clear("sess-EV6")
