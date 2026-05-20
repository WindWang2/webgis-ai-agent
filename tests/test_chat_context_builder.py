"""context_builder 计划注入测试。"""
from app.services.chat.context_builder import build_plan_block, compose_request_messages
from app.services.chat.planner import Plan, PlanStep, set_plan, clear_plan


def test_build_plan_block_shows_checkboxes():
    plan = Plan(intent="分析医院分布", domains=["chinese"], steps=[
        PlanStep(n=1, goal="锁定边界", tool_family="chinese", done=True),
        PlanStep(n=2, goal="搜索 POI", tool_family="chinese", done=False),
    ])
    block = build_plan_block(plan)
    assert "[执行计划]" in block
    assert "分析医院分布" in block
    assert "✅" in block and "⬜" in block
    assert "锁定边界" in block and "搜索 POI" in block


def test_build_plan_block_warns_on_incomplete():
    plan = Plan(intent="x", domains=["core"], steps=[
        PlanStep(n=1, goal="a", tool_family="core", done=False),
    ])
    assert "未完成" in build_plan_block(plan)


def test_build_plan_block_all_done_no_warning():
    plan = Plan(intent="x", domains=["core"], steps=[
        PlanStep(n=1, goal="a", tool_family="core", done=True),
    ])
    assert "未完成" not in build_plan_block(plan)


def test_compose_request_messages_injects_plan_when_present():
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "你好"},
    ]
    set_plan("ctx-sess", Plan(intent="测试意图", domains=["core"], steps=[]))
    try:
        out = compose_request_messages("ctx-sess", msgs)
        joined = " ".join(m["content"] for m in out if m.get("role") == "system")
        assert "测试意图" in joined
    finally:
        clear_plan("ctx-sess")


def test_compose_request_messages_no_plan_no_block():
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "你好"},
    ]
    clear_plan("ctx-sess-2")
    out = compose_request_messages("ctx-sess-2", msgs)
    joined = " ".join(m["content"] for m in out if m.get("role") == "system")
    assert "[执行计划]" not in joined
