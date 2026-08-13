"""chat/context_builder 单测（M1 深水区拆分）：

build_map_state_summary / format_layer_lines / build_last_analysis_context /
compose_request_messages 全是纯函数（仅依赖 session_data_manager 单例），
直接调即可。
"""
import pytest

from app.services.chat.context_builder import (
    build_last_analysis_context,
    build_map_state_summary,
    build_plan_block,
    compose_request_messages,
    format_layer_lines,
)
from app.services.chat.context_assembler import ChatContextAssembler
from app.services.chat.plan_orchestrator import render_plan_block
from app.services.session_data import session_data_manager


# ─── format_layer_lines (async) ───────────────────────


class TestFormatLayerLines:
    async def test_empty_returns_empty(self):
        assert await format_layer_lines({}, []) == []

    async def test_inventory_priority(self):
        out = await format_layer_lines(
            inventory={"ref:abc": "POI 学校"},
            active_layers=[{"id": "ref:abc", "visible": True, "type": "vector", "featureCount": 12}],
        )
        assert len(out) == 1
        line = out[0]
        assert "ref:abc" in line
        assert "别名=<untrusted_layer_alias>POI 学校</untrusted_layer_alias>" in line
        assert "类型=<untrusted_layer_type>vector</untrusted_layer_type>" in line
        assert "要素=12" in line
        assert "可见" in line

    async def test_fallback_to_active_when_no_inventory(self):
        out = await format_layer_lines(
            inventory={},
            active_layers=[
                {"id": "layer-1", "name": "热力图", "type": "heatmap", "visible": False, "opacity": 0.5},
            ],
        )
        assert "<untrusted_layer_name>热力图</untrusted_layer_name>" in out[0]
        assert "id=<untrusted_layer_name>layer-1</untrusted_layer_name>" in out[0]
        assert "类型=<untrusted_layer_type>heatmap</untrusted_layer_type>" in out[0]
        assert "隐藏" in out[0]
        assert "不透明度=50%" in out[0]


# ─── build_last_analysis_context (纯) ─────────────────────────


class TestLastAnalysisContext:
    def test_empty_history_returns_empty(self):
        assert build_last_analysis_context([]) == ""
        assert build_last_analysis_context([{"role": "system", "content": "..."}]) == ""

    def test_picks_most_recent_user_and_assistant(self):
        # design-v3 §4 去重（行为变更）：历史窗口保证保留最近 2 轮，
        # [最近对话上下文] 只覆盖窗口之前的轮次（这里是第一轮"你好"交换）。
        msgs = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好"},
            {"role": "user", "content": "查海淀医院"},
            {"role": "assistant", "content": "已查到 312 家医院"},
            {"role": "user", "content": "画热力图"},
        ]
        ctx = build_last_analysis_context(msgs)
        assert "你好" in ctx  # 窗口之前的轮次被提炼
        assert "画热力图" not in ctx  # 最新一轮在历史窗口内逐字可见，不再重复
        assert "查海淀医院" not in ctx

    def test_collects_unique_refs(self):
        # 3 轮：refs 放在窗口之前的首轮，验证去重采集仍生效。
        msgs = [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "result at ref:data-aaa"},
            {"role": "tool", "content": "{ref: ref:data-bbb, ...}"},
            {"role": "assistant", "content": "ref:data-aaa is reused"},  # 与上面重复
            {"role": "user", "content": "继续"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "换个样式"},
        ]
        ctx = build_last_analysis_context(msgs)
        assert "ref:data-aaa" in ctx
        assert "ref:data-bbb" in ctx

    def test_truncates_long_messages(self):
        # 3 轮：长消息在窗口之前的首轮，验证 200/300 截断仍生效。
        long_user = "X" * 500
        long_asst = "Y" * 500
        msgs = [
            {"role": "user", "content": long_user},
            {"role": "assistant", "content": long_asst},
            {"role": "user", "content": "继续"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "换个样式"},
        ]
        ctx = build_last_analysis_context(msgs)
        # 用户 200 截、助手 300 截
        assert ctx.count("X") <= 200
        assert ctx.count("Y") <= 300
        assert ctx.count("X") > 0  # 确实提炼了窗口之前的长消息（非空断言）
        assert ctx.count("Y") > 0


# ─── build_map_state_summary（接 session_data_manager） ──────────


@pytest.fixture
async def clean_session():
    sid = "test-context-builder-session"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


class TestMapStateSummary:
    async def test_empty_session_includes_defaults(self, clean_session):
        out = await build_map_state_summary(clean_session)
        assert "[环境感知" in out
        assert "未授权" in out  # 用户位置默认
        assert "未知" in out  # 视口默认
        assert "活跃图层: 无" in out

    async def test_viewport_renders(self, clean_session):
        await session_data_manager.set_map_state(clean_session, "viewport", {
            "center": [116.4074, 39.9042], "zoom": 12, "bearing": 30, "pitch": 60,
        })
        out = await build_map_state_summary(clean_session)
        assert "lng=116.4074" in out
        assert "lat=39.9042" in out
        assert "zoom=12" in out
        assert "bearing=30" in out
        assert "pitch=60" in out

    async def test_bounds_render(self, clean_session):
        await session_data_manager.set_map_state(clean_session, "viewport", {"bounds": [1.1, 2.2, 3.3, 4.4]})
        out = await build_map_state_summary(clean_session)
        assert "可视范围" in out
        assert "1.100" in out and "4.400" in out

    async def test_inventory_layers(self, clean_session):
        await session_data_manager.store(clean_session, {"type": "FeatureCollection", "features": []}, prefix="data")
        out = await build_map_state_summary(clean_session)
        # store 会创建一个 ref，summary 里应当包含
        assert "活跃图层:" in out
        assert "ref:data-" in out

    async def test_user_location_renders(self, clean_session):
        await session_data_manager.set_map_state(clean_session, "user_location", {"lng": 116.5, "lat": 39.8, "accuracy": 10})
        out = await build_map_state_summary(clean_session)
        assert "116.500000" in out
        assert "39.800000" in out
        assert "±10m" in out

    async def test_event_log_renders(self, clean_session):
        await session_data_manager.append_event(clean_session, "tool_executed", {"tool": "geocode_cn", "ref": "ref:data-x"})
        out = await build_map_state_summary(clean_session)
        # Round 2 split: 工具调用 vs 用户操作 各有独立段
        assert "近期工具调用:" in out
        assert "geocode_cn" in out


# ─── compose_request_messages ─────────────────────────────────


class TestComposeRequestMessages:
    async def test_injects_env_into_system_prompt(self, clean_session):
        msgs = [
            {"role": "system", "content": "BASE_PROMPT"},
            {"role": "user", "content": "hi"},
        ]
        out = await compose_request_messages(clean_session, msgs)
        # 系统提示被合并扩展
        assert out[0]["role"] == "system"
        assert out[0]["content"].startswith("BASE_PROMPT")
        assert "[环境感知" in out[0]["content"]
        # user 消息保留
        assert any(m["role"] == "user" and m["content"] == "hi" for m in out)

    async def test_appends_last_ctx_when_history_nonempty(self, clean_session):
        # design-v3 §4 去重（行为变更）：仅 2 轮对话全部落在历史窗口内
        # （min 2 轮保证），[最近对话上下文] 不再重复注入同一批消息。
        msgs = [
            {"role": "system", "content": "BASE"},
            {"role": "user", "content": "查海淀医院"},
            {"role": "assistant", "content": "找到 50 家"},
            {"role": "user", "content": "画热力图"},
        ]
        out = await compose_request_messages(clean_session, msgs)
        joined = " ".join(m["content"] for m in out if m.get("role") == "system")
        assert "[最近对话上下文]" not in joined
        # 用户消息全部还原顺序（历史窗口逐字保留）
        user_msgs = [m["content"] for m in out if m["role"] == "user"]
        assert user_msgs == ["查海淀医院", "画热力图"]

    async def test_empty_messages_returns_empty(self, clean_session):
        assert await compose_request_messages(clean_session, []) == []


# ─── design-v3 §4：计划块单一渲染 + tools payload 软计入 ─────────


def test_plan_block_single_render_source():
    """[执行计划] 单一渲染来源：build_plan_block 与 render_plan_block 等价。"""
    from app.services.chat.planner import Plan, PlanStep
    plan = Plan(intent="x", domains=["core"], steps=[
        PlanStep(n=1, goal="a", tool_family="core", done=True),
        PlanStep(n=2, goal="b", tool_family="core", done=False),
    ])
    assert build_plan_block(plan) == render_plan_block(plan)
    assert "[执行计划]" in render_plan_block(plan)


class TestEstimatedTokens:
    async def test_tools_payload_counted_in_estimate(self, clean_session):
        msgs = [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "hi"},
        ]
        base = await ChatContextAssembler().assemble(clean_session, msgs)
        with_tools = await ChatContextAssembler().assemble(
            clean_session, msgs, tools_payload_chars=8000
        )
        # 8000 ASCII chars ≈ 2000 tokens（软计入，只影响估算）
        assert with_tools.estimated_tokens > base.estimated_tokens
        assert with_tools.estimated_tokens - base.estimated_tokens >= 2000

    async def test_cjk_tools_payload_weighted_higher_than_ascii(self, clean_session):
        """P3 #3：等字符数的 CJK-heavy tools payload 估算 ≥ 纯 ASCII payload——
        _estimate_tokens 权重（CJK 1 char ≈ 1.5 tokens，ASCII 4 char ≈ 1 token）。"""
        msgs = [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "hi"},
        ]
        ascii_payload = "tool text " * 160         # 1600 chars，全 ASCII
        cjk_payload = "空间分析工具描述示例" * 160   # 同字符数（10×160），CJK-heavy
        assert len(ascii_payload) == len(cjk_payload) == 1600

        base = (await ChatContextAssembler().assemble(clean_session, msgs)).estimated_tokens
        ascii_delta = (
            await ChatContextAssembler().assemble(clean_session, msgs, tools_payload=ascii_payload)
        ).estimated_tokens - base
        cjk_delta = (
            await ChatContextAssembler().assemble(clean_session, msgs, tools_payload=cjk_payload)
        ).estimated_tokens - base
        # 同字符数：CJK 权重（1.5/char）显著高于 ASCII（0.25/char）
        assert cjk_delta >= ascii_delta
        assert cjk_delta > ascii_delta
        # CJK 估算必须高于旧的 chars/4 近似（1600/4 = 400）
        assert cjk_delta > 400
