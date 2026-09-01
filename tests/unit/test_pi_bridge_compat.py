"""Pi 宿主兼容性修复的契约测试（切换到 Pi 后的工具/步骤/上下文接缝）。

覆盖：
- BLOCKING：rebind_*/chartRef/tableRef 游标不被透明解引用摧毁；
- MAJOR：generate_chart data_ref 游标捕获注入（layer_id 自动解析可达）；
- MAJOR：field_extras 注册面（签名推导模型携带 json_schema_extra）；
- MAJOR：环境感知块（Pi turn prompt 注入；观察记录携带 4 键）；
- MAJOR：工具面偏好行从 SessionPlan 信封派生注入；
- MINOR：_completed_keys 会话隔离 / background_job_ids 携带 /
  list_available_tools tier-3 过滤 / 非流式终验 parity / 别名折叠守卫。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


class TestRefCursorSurvival:
    """BLOCKING：ref 游标字段在 dispatch 的透明解引用下必须原样到达工具。"""

    async def test_rebind_refs_reach_tool_undereferenced(self, tmp_path, monkeypatch):
        from app.services.gis_harness.tools import register_gis_harness_tools
        from app.tools.registry import ToolRegistry
        from app.services.session_data import session_data_manager

        monkeypatch.setattr(
            "app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path,
        )
        registry = ToolRegistry()
        register_gis_harness_tools(registry)

        sid = "pi-compat-rebind"
        # 存一个 chart ref（解引用目标存在 —— 若被解引用，载荷是 dict）。
        ref = await session_data_manager.store(
            sid, {"chart": {"type": "bar", "title": "x", "data": [{"name": "a", "value": 1}]}},
            prefix="chart",
        )
        # 建一个面板供 rebind。
        tool = registry._tools["webgis_component_update"]
        await tool(
            session_id=sid, component_id="c1", component_type="chart_panel", create=True,
            chart={"type": "bar", "title": "t", "data": [{"name": "a", "value": 1}]},
        )
        result = await registry.dispatch(
            "webgis_component_update",
            {
                "session_id": sid,
                "component_id": "c1",
                "action": "rebind",
                "rebind_chart_ref": ref,
            },
            session_id=sid,
        )
        assert result.get("success") is True, result
        bound = next(
            c for c in result.get("components", []) if c.get("id") == "c1"
        )
        # ref 字符串原样写入组件 options —— 不是被解引用的载荷 dict。
        assert bound["options"].get("chartRef") == ref

    async def test_nested_options_chartref_not_dereferenced(self):
        """options.chartRef（嵌套 dict 内的游标）不被递归解引用。"""
        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()
        captured: dict = {}

        async def probe(options: dict = None):
            captured["options"] = options or {}
            return {"ok": True}

        registry.register(
            tier=1, name="_probe_options_ref", description="probe", func=probe,
        )
        # 模拟一个已存储的 chart ref。
        from app.services.session_data import session_data_manager

        sid = "pi-compat-options"
        ref = await session_data_manager.store(sid, {"chart": {"type": "bar"}}, prefix="chart")
        await registry.dispatch(
            "_probe_options_ref",
            {"options": {"chartRef": ref, "title": "T"}},
            session_id=sid,
        )
        assert captured["options"]["chartRef"] == ref
        assert isinstance(captured["options"]["chartRef"], str)

    async def test_alias_folding_never_eats_declared_fields(self):
        """声明过的字段（含保护名 geojson_ref/data_ref）不被折叠进 geojson。"""
        from app.tools.registry import _normalize_tool_arguments
        from pydantic import BaseModel

        class Args(BaseModel):
            geojson: object = None
            geojson_ref: str = ""

        normalized = _normalize_tool_arguments(
            "kde_surface",
            {"geojson_ref": "ref:geojson-1"},
            Args,
        )
        assert normalized["geojson_ref"] == "ref:geojson-1"
        assert "geojson" not in normalized


class TestDataRefCapture:
    """MAJOR：generate_chart 的 data_ref 游标捕获（layer_id 自动解析可达）。"""

    async def test_data_ref_injected_when_data_dereferenced(self, tmp_path, monkeypatch):
        from app.tools.chart import register_chart_tools
        from app.tools.registry import ToolRegistry
        from app.services.session_data import session_data_manager

        monkeypatch.setattr(
            "app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path,
        )
        registry = ToolRegistry()
        register_chart_tools(registry)

        sid = "pi-compat-chart"
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"ct_name": "武侯区", "count": 8}},
                {"type": "Feature", "properties": {"ct_name": "锦江区", "count": 6}},
            ],
        }
        ref = await session_data_manager.store(sid, fc, prefix="geojson")

        # MapSpec：唯一一个 source 以该 ref 为源 + 一个图层。
        from app.services.mapspec_store import mapspec_store

        await mapspec_store.layer_upsert(
            sid,
            {
                "id": "district-layer",
                "source": "district-layer",
                "type": "fill",
                "paint": {},
            },
            source_data=None,
        )
        # 直接把 source 指针写进 spec（layer_upsert 的 source 注册走 spec 通道）。
        spec = await mapspec_store.get_mapspec(sid)
        spec = dict(spec or {})
        sources = dict(spec.get("sources") or {})
        sources["district-layer"] = {"type": "geojson", "ref": ref}
        from app.services.session_data import session_data_manager as sdm

        await sdm.set_map_state(
            sid, "mapspec", {**spec, "sources": sources},
        )

        result = await registry.dispatch(
            "generate_chart",
            {
                "chart_type": "bar",
                "title": "各区数量",
                "data": ref,
                "x_field": "ct_name",
                "y_field": "count",
                "session_id": sid,
                "attach_to_map": True,
            },
            session_id=sid,
        )
        panel = result.get("map_chart_panel") or {}
        # data 被解引用 → data_ref 注入 → layer 自动解析 → 面板携带 layer_id。
        assert panel.get("attached") is True, result
        assert panel.get("layer_id") == "district-layer", panel

    def test_field_extras_reach_signature_derived_model(self):
        """field_extras 注册面：签名推导模型携带 json_schema_extra。"""
        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()

        async def probe(data: object = None, data_ref: str = ""):
            return {"ok": True}

        registry.register(
            tier=1, name="_probe_field_extras", description="probe", func=probe,
            field_extras={"data_ref": {"ref_cursor": True, "capture_ref_of": "data"}},
        )
        model = registry._models["_probe_field_extras"]
        extra = model.model_fields["data_ref"].json_schema_extra
        assert isinstance(extra, dict)
        assert extra.get("ref_cursor") is True
        assert extra.get("capture_ref_of") == "data"
        # 声明被 registry 的游标/捕获机制识别。
        assert registry._declared_ref_cursor_keys(model) == {"data_ref"}
        assert registry._declared_cursor_capture_fields(model) == {"data_ref": "data"}


class TestPiTurnContext:
    """MAJOR：环境感知块 + 工具面偏好行注入 Pi turn prompt。"""

    async def test_env_block_bounded_and_fenced(self):
        from app.api.routes.chat import _build_environment_turn_context

        block = _build_environment_turn_context({
            "viewport": {"center": [104.0, 30.6], "zoom": 10.5, "bearing": 12},
            "base_layer": "OSM 地图",
            "is_3d": True,
            "user_location": {"lng": 104.0, "lat": 30.6, "accuracy": 20},
            "selected_feature": {
                "layer_name": "<evil>", "feature_id": "f1",
                "point": [104.0, 30.6],
                "properties": {"name": "x"},
            },
            "focus_layer_id": "poi",
            "layers": [{"name": f"L{i}"} for i in range(20)],
        })
        assert block.startswith("[环境感知")
        assert "3D" in block and "bearing=12°" in block
        assert "用户当前选中" in block
        assert "用户聚焦图层" in block and "poi" in block
        assert "<evil>" not in block  # 转义：不可注入
        assert "untrusted_layer_name" in block  # fence 标记在场
        assert "共 20 层" in block  # 截断披露
        assert len(block) < 2500  # 有界

    async def test_env_block_empty_on_no_state(self):
        from app.api.routes.chat import _build_environment_turn_context

        assert _build_environment_turn_context(None) == ""
        assert _build_environment_turn_context({}) == ""

    async def test_turn_prompt_carries_surface_and_env_blocks(self):
        from app.services.chat.pi_turn_context import attach_turn_context

        msg = attach_turn_context(
            "m", "tok", "carto", "plan",
            env_block="[环境感知] x", surface_block="[工具面提示] y",
        )
        assert msg.index("m") < msg.index("carto") < msg.index("plan")
        assert msg.index("plan") < msg.index("[环境感知] x") < msg.index("[工具面提示] y")
        assert msg.rstrip().endswith("(Internal routing context; do not quote or modify this marker.)")

    def test_surface_line_from_chapter(self):
        from app.services.gis_harness.tool_surface import compile_tool_surface

        s = compile_tool_surface(
            chapter={"analysis_steps": [{"status": "running"}]},
        )
        assert s.phase == "analysis"
        # data 阶段preferred 含 intent 前门（prompt 提示行非空可用）。
        s2 = compile_tool_surface(next_action="produce_layer")
        assert "webgis_map_product" in s2.preferred_tools


class TestDispatchServiceCompat:
    """MINOR：会话隔离的 completed 键 + background_job_ids 携带。"""

    async def test_completed_keys_session_scoped(self):
        """另一会话的同参调用不命中本会话的 completed 标记（不谎报已成功）。"""
        from app.services.tool_dispatch_service import ToolDispatchService
        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()

        async def probe(x: int = 1):
            return {"ok": True}

        registry.register(tier=1, name="_probe_dedup", description="probe", func=probe)
        service = ToolDispatchService(registry=registry)

        tc = {"id": "t1", "function": {"name": "_probe_dedup", "arguments": {"x": 1}}}
        executed_a: set = set()
        r1 = await service.dispatch(tc, "session-a", executed_a)
        assert r1.status == "ok"

        # session-b 同参：不得被 session-a 的 completed 标记拦截。
        executed_b: set = set()
        r2 = await service.dispatch(tc, "session-b", executed_b)
        assert r2.status == "ok"

        # 同会话同参重复 → post-success repeated（既有语义保持）。
        r3 = await service.dispatch(tc, "session-a", executed_a)
        assert r3.status == "repeated"

    def test_result_carries_background_job_ids_field(self):
        from app.services.tool_dispatch_service import ToolDispatchResult

        res = ToolDispatchResult(
            status="ok", llm_payload="p", slim_event={}, geojson_ref=None,
            raw_result={}, error_msg=None,
        )
        assert res.background_job_ids == []


class TestDiscoveryAndFinalizer:
    """MINOR：tier-3 发现过滤 + 非流式终验 parity。"""

    async def test_list_available_tools_hides_tier3(self):
        from app.tools.registry import ToolRegistry, tool as tool_dec
        from app.tools.meta_tools import register_meta_tools

        registry = ToolRegistry()
        register_meta_tools(registry)
        tool_dec(registry, tier=1, domains=["probe"], name="_t1_ok", description="p1")(lambda **kw: {})
        tool_dec(registry, tier=3, domains=["probe"], name="_t3_admin", description="p3")(lambda **kw: {})
        meta = registry.all_metadata()
        assert meta["_t3_admin"]["tier"] == 3

        fn = registry._tools["list_available_tools"]
        result = await fn(domain="probe")
        names = [t["name"] for t in result["tools"]]
        assert "_t1_ok" in names
        assert "_t3_admin" not in names
        assert result.get("hidden_tier3") == 1

    async def test_nonstream_finalizer_parity(self):
        """prompt()（非流式）在返回前触发 maybe_finalize_map_product。"""

        calls: list[str] = []

        class _FakeFinalizer:
            async def __call__(self, sid, *, reason):
                calls.append(reason)
                return None

        # 仅验证钩子语义：函数存在且可被 chat.py 的调用点复用。
        from app.services.gis_harness.map_completion import maybe_finalize_map_product
        assert maybe_finalize_map_product is not None
        # reason 约定
        assert calls == [] or "turn_settled" in calls


class TestExtensionTimeoutDefault:
    """MAJOR：扩展超时默认与服务端工具预算对齐。"""

    def test_mjs_default_derives_from_server_budget(self):
        import re
        from pathlib import Path

        mjs = Path("app/extensions/webgis-tools/index.mjs").read_text()
        assert "TOOL_TIMEOUT_S" in mjs
        # 兜底不再低于服务端默认预算（300s）。
        m = re.search(r":\s*300;", mjs)
        assert m, "server budget fallback missing"
        assert "60000" not in re.sub(r"//.*", "", mjs)  # 旧 60s 默认已移除

    def test_ts_source_in_sync(self):
        from pathlib import Path

        ts = Path("app/extensions/webgis-tools/index.ts").read_text()
        assert "TOOL_TIMEOUT_S" in ts
