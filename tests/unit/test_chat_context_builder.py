"""chat/context_builder 单测（M1 深水区拆分）：

build_map_state_summary / format_layer_lines / build_last_analysis_context /
compose_request_messages 全是纯函数（仅依赖 session_data_manager 单例），
直接调即可。
"""
import pytest

from app.services.chat.context_builder import (
    build_last_analysis_context,
    build_map_state_summary,
    compose_request_messages,
    format_layer_lines,
)
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
        assert "别名=POI 学校" in line
        assert "类型=vector" in line
        assert "要素=12" in line
        assert "可见" in line

    async def test_fallback_to_active_when_no_inventory(self):
        out = await format_layer_lines(
            inventory={},
            active_layers=[
                {"id": "layer-1", "name": "热力图", "type": "heatmap", "visible": False, "opacity": 0.5},
            ],
        )
        assert "热力图" in out[0]
        assert "id=layer-1" in out[0]
        assert "类型=heatmap" in out[0]
        assert "隐藏" in out[0]
        assert "不透明度=50%" in out[0]


# ─── build_last_analysis_context (纯) ─────────────────────────


class TestLastAnalysisContext:
    def test_empty_history_returns_empty(self):
        assert build_last_analysis_context([]) == ""
        assert build_last_analysis_context([{"role": "system", "content": "..."}]) == ""

    def test_picks_most_recent_user_and_assistant(self):
        msgs = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好"},
            {"role": "user", "content": "查海淀医院"},
            {"role": "assistant", "content": "已查到 312 家医院"},
            {"role": "user", "content": "画热力图"},
        ]
        ctx = build_last_analysis_context(msgs)
        assert "画热力图" in ctx
        assert "已查到 312 家医院" in ctx
        assert "查海淀医院" not in ctx  # 不是最新

    def test_collects_unique_refs(self):
        msgs = [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "result at ref:data-aaa"},
            {"role": "tool", "content": "{ref: ref:data-bbb, ...}"},
            {"role": "assistant", "content": "ref:data-aaa is reused"},  # 与上面重复
        ]
        ctx = build_last_analysis_context(msgs)
        assert "ref:data-aaa" in ctx
        assert "ref:data-bbb" in ctx

    def test_truncates_long_messages(self):
        long_user = "X" * 500
        long_asst = "Y" * 500
        msgs = [
            {"role": "user", "content": long_user},
            {"role": "assistant", "content": long_asst},
        ]
        ctx = build_last_analysis_context(msgs)
        # 用户 200 截、助手 300 截
        assert ctx.count("X") <= 200
        assert ctx.count("Y") <= 300


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
        msgs = [
            {"role": "system", "content": "BASE"},
            {"role": "user", "content": "查海淀医院"},
            {"role": "assistant", "content": "找到 50 家"},
            {"role": "user", "content": "画热力图"},
        ]
        out = await compose_request_messages(clean_session, msgs)
        # 第二条应该是"最近对话上下文"系统消息
        assert out[1]["role"] == "system"
        assert "[最近对话上下文]" in out[1]["content"]
        # 用户消息全部还原顺序
        user_msgs = [m["content"] for m in out if m["role"] == "user"]
        assert user_msgs == ["查海淀医院", "画热力图"]

    async def test_empty_messages_returns_empty(self, clean_session):
        assert await compose_request_messages(clean_session, []) == []
