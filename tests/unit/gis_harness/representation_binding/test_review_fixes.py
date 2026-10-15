"""ADR-0219 review 修复的回归测试（P1#1 CAS 链 / P1#3 写入门 / P1#4 pin）。

对应独立 review（2026-10-16 subagent C）发现清单：
- P1#1：apply_pending_bindings 批内 CAS 链——单轮多绑定全部落地；
- P1#3：requirement service 进程内写入门——并发写者不再静默丢更新；
- P1#4：显式 method 路径下 requirement palette pin 仍生效（合并已在
  分支外）。
"""
from __future__ import annotations

import asyncio


from app.services.gis_harness.requirement_ir import service as req_service
from app.services.gis_harness.representation_binding import requirement_to_mapspec as r2m
from app.lib.runtime.context import bind_runtime_context


class MemStore:
    def __init__(self):
        self.data: dict = {}

    async def get_map_state(self, session_id):
        return self.data.get(session_id, {})

    async def set_map_state(self, session_id, key, value, seq=None):
        self.data.setdefault(session_id, {})[key] = value
        return True


def run(coro):
    return asyncio.run(coro)


_GJ = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
         "properties": {"pop": 10.0}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 0]},
         "properties": {"pop": 100.0}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [2, 0]},
         "properties": {"pop": 500.0}},
    ],
}


class TestConcurrentWriterGate:
    def test_concurrent_patches_no_lost_update(self):
        """P1#3：两个并发 apply_user_patches（turn 路径 + 投影路径的
        最小复现）必须都落账——进程内写进门串行化 load→modify→save。"""
        store = MemStore()
        run(req_service.ensure_document(store, "s1", "成都小学分布图", turn=1))

        async def scenario():
            await asyncio.gather(
                req_service.apply_user_patches(store, "s1", [
                    req_service.TurnPatch(
                        op="set", path="representation.palette", value="蓝色",
                        actor="user", op_id="op-a", turn=2, reason="turn path"),
                ]),
                req_service.apply_user_patches(store, "s1", [
                    req_service.TurnPatch(
                        op="set", path="representation.hidden_layer_ids",
                        value=["roads"], actor="user", op_id="op-b",
                        turn=2, reason="projection path"),
                ]),
            )

        run(scenario())
        state = store.data["s1"][req_service.REQUIREMENT_STATE_KEY]
        doc = state["document"]
        op_ids = {p["op_id"] for p in doc["patches"]}
        assert {"op-a", "op-b"} <= op_ids, "并发写者不得静默丢更新"
        assert doc["revision"] == 3, "两条 patch 各推一次 revision"
        intent = doc["intent"]
        assert intent["representation"]["palette"] == "blue"
        assert list(intent["representation"]["hidden_layer_ids"]) == ["roads"]

    def test_gate_reentrancy_across_loops(self):
        """asyncio.run 每次新 loop——门必须按 loop 重建（不炸不串）。"""
        store = MemStore()
        run(req_service.ensure_document(store, "s2", "成都小学分布图", turn=1))
        run(req_service.apply_user_patches(store, "s2", [
            req_service.TurnPatch(op="set", path="representation.palette",
                                  value="红色", actor="user", op_id="op-c", turn=2)]))
        run(req_service.apply_user_patches(store, "s2", [
            req_service.TurnPatch(op="set", path="representation.palette",
                                  value="绿色", actor="user", op_id="op-d", turn=3)]))
        state = store.data["s2"][req_service.REQUIREMENT_STATE_KEY]
        assert state["document"]["intent"]["representation"]["palette"] == "green"


class TestPinMergeExplicitMethod:
    def test_explicit_method_still_applies_pin(self):
        """P1#4：agent 显式传 method 时用户 palette pin 仍优先（合并提升
        到分支外）——结果 legend/样式使用 pin 色，且有披露 check。"""
        from app.tools.cartography import register_cartography_tools
        from app.tools.registry import ToolRegistry

        r2m._PIN_SNAPSHOT.clear()
        r2m._PIN_SNAPSHOT["s-pin"] = {"pinned_palette": "viridis"}
        registry = ToolRegistry()
        register_cartography_tools(registry)
        with bind_runtime_context(session_id="s-pin"):
            out = run(registry.dispatch("create_thematic_map", {
                "geojson": _GJ, "field": "pop",
                "method": "equal_interval", "k": 3,
                "palette": "Reds",
            }))
        assert "error" not in out, out.get("error")
        legend = out.get("legend_spec") or {}
        # viridis pin 生效（不是工具参数 Reds）
        assert legend.get("palette") == "viridis"
        assert legend.get("palette_colors"), "pin 色带必须产出实际颜色"
        checks = [
            c for c in (out.get("layer_meta") or {}).get("measurement_checks", [])
            if c.get("code") == "requirement_palette_pin_applied"
        ]
        assert checks, "显式 method 路径也必须有 pin 覆盖披露"
        r2m._PIN_SNAPSHOT.clear()

    def test_no_pin_explicit_method_unchanged(self):
        """无 pin 时显式 method + 工具 palette 行为不变（回归面）。"""
        from app.tools.cartography import register_cartography_tools
        from app.tools.registry import ToolRegistry

        r2m._PIN_SNAPSHOT.clear()
        registry = ToolRegistry()
        register_cartography_tools(registry)
        with bind_runtime_context(session_id="s-nopin"):
            out = run(registry.dispatch("create_thematic_map", {
                "geojson": _GJ, "field": "pop",
                "method": "equal_interval", "k": 3,
                "palette": "Reds",
            }))
        assert "error" not in out, out.get("error")
        checks = [
            c for c in (out.get("layer_meta") or {}).get("measurement_checks", [])
            if c.get("code") == "requirement_palette_pin_applied"
        ]
        assert not checks
