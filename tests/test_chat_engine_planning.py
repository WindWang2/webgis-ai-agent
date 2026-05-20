"""ChatEngine 规划阶段集成测试。"""
import pytest

from app.tools.registry import ToolRegistry
from app.services.tool_catalog import ToolCatalog
from app.services.chat_engine import ChatEngine
from app.services.chat import planner as planner_mod


@pytest.fixture
def engine():
    reg = ToolRegistry()
    reg.register("buffer_analysis", "buffer", func=lambda **_: {})
    eng = ChatEngine(reg, tool_catalog=ToolCatalog(reg))
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
    captured: list = []
    monkeypatch.setattr(decision_log, "log_tool_decision",
                        lambda rec: captured.append(rec))
    # 直接调用 _log_tool_decision，传入 step_n=2
    engine._log_tool_decision(
        session_id="sess-T1",
        round_index=0,
        message="test",
        tool_name="buffer_analysis",
        tool_args={},
        outcome={"is_error": False, "result": {"ok": True}},
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
