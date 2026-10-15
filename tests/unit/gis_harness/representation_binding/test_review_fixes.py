"""ADR-0219 review 修复的回归测试（P1#1 CAS 链 / P1#3 写入门 / P1#4 pin）。

对应独立 review（2026-10-16 subagent C）发现清单：
- P1#1：apply_pending_bindings 批内 CAS 链——单轮多绑定全部落地；
- P1#3：requirement service 进程内写入门——并发写者不再静默丢更新；
- P1#4：显式 method 路径下 requirement palette pin 仍生效（合并已在
  分支外）。
二轮复核补充（N1-N4）：error_code 矩阵映射、真并发 store、截断披露、
skip reason、drift 族归一、batch 聚合投影。
"""
from __future__ import annotations

import asyncio


from app.services.gis_harness.requirement_ir import service as req_service
from app.services.gis_harness.representation_binding import requirement_to_mapspec as r2m
from app.lib.runtime.context import bind_runtime_context


class MemStore:
    """Yielding 内存 store（每个 async 方法显式让步——gather 的并发
    任务真正交错，写入门的测试才是真并发而非伪串行；review N2）。"""

    def __init__(self):
        self.data: dict = {}

    async def get_map_state(self, session_id):
        await asyncio.sleep(0)
        return self.data.get(session_id, {})

    async def set_map_state(self, session_id, key, value, seq=None):
        await asyncio.sleep(0)
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


class TestSecondPassFixes:
    """二轮复核 N1-N4 的回归钉子。"""

    def test_n1_user_wins_code_maps_to_matrix_vocab(self):
        """N1：引擎码 user_wins_presentation 必须映射为冲突矩阵登记码。"""
        from app.services.gis_harness.representation_binding.conflicts import (
            REASON_CODES,
            resolve_conflict,
            resolve_from_result,
        )

        code = resolve_from_result(
            is_error=True, superseded=False, duplicate=False,
            error_code="user_wins_presentation")
        assert code == "binding_user_wins_presentation"
        assert code in REASON_CODES, "binding 终态码必须在冲突矩阵登记"
        assert resolve_conflict(code).winner == "user"

    def test_n4_truncation_disclosure(self):
        """N4：多来源叠加超限时产出 binding_pass_truncated 披露条目。"""
        from app.services.gis_harness.requirement_ir.patch import apply_patch
        from app.services.gis_harness.requirement_ir.contracts import (
            PatchRecord, RequirementDocument)

        store = MemStore()
        run(req_service.ensure_document(store, "s-trunc", "成都小学分布图", turn=1))
        doc = RequirementDocument.model_validate(
            store.data["s-trunc"][req_service.REQUIREMENT_STATE_KEY]["document"])
        layers = [f"layer{i:02d}" for i in range(15)]
        for path, value, op_id in (
            ("representation.hidden_layer_ids", layers, "op-t1"),
            ("representation.locked_layer_ids", ["lk1"], "op-t2"),
            ("components.legend", False, "op-t3"),
            ("representation.palette", "蓝色", "op-t4"),
        ):
            doc, applied = apply_patch(doc, PatchRecord(
                op_id=op_id, turn=2, actor="user", op="set",
                path=path, value=value))
            assert applied
        mapspec = {"layers": [{"id": f"layer{i:02d}", "type": "circle"} for i in range(15)],
                   "layout": {"components": [{"id": "legend", "type": "legend"}]}}
        proposals = r2m.build_binding_proposals(doc, mapspec, expected_revision=1)
        skipped = [p for p in proposals
                   if p.error_code == "binding_pass_truncated"]
        assert skipped, "超限必须有截断披露条目"
        assert "binding_pass_truncated" in (skipped[0].error_code or "")
        assert skipped[0].disclosures

    def test_n4_skip_reasons(self):
        """N4：projection_skip_reason 的专属码（style→not_mirrored；
        repair→system_repair；opacity→ephemeral）。"""
        from app.services.gis_harness.representation_binding import (
            semantic_projection as sp)
        from app.services.mapspec.intents import PatchLayerStyleIntent
        from app.contracts.mapspec_intents import PatchLayerPresentationIntent

        style = PatchLayerStyleIntent(layer_id="x", paint={"color": "#fff"})
        assert sp.projection_skip_reason(
            style, origin="user", actor=None, producer_class=None) == \
            "projection_not_mirrored"
        assert sp.projection_skip_reason(
            PatchLayerPresentationIntent(layer_id="x", opacity=0.4),
            origin="user", actor=None, producer_class=None) == "projection_ephemeral"
        assert sp.projection_skip_reason(
            PatchLayerPresentationIntent(layer_id="x", visible=True),
            origin="agent", actor="runtime_repair", producer_class=None) == \
            "projection_system_repair"

    def test_n4_drift_family_normalization(self):
        """N4：drift 信号族归一（requirement 存 roads，spec 是 roads-1）。"""
        from app.services.gis_harness.requirement_ir.patch import apply_patch
        from app.services.gis_harness.requirement_ir.contracts import (
            PatchRecord, RequirementDocument)
        from app.services.gis_harness.representation_binding.agent_context import (
            representation_binding_summary)

        store = MemStore()
        run(req_service.ensure_document(store, "s-drift", "成都小学分布图", turn=1))
        doc = RequirementDocument.model_validate(
            store.data["s-drift"][req_service.REQUIREMENT_STATE_KEY]["document"])
        doc, applied = apply_patch(doc, PatchRecord(
            op_id="op-d1", turn=2, actor="user", op="set",
            path="representation.hidden_layer_ids", value=["roads"]))
        assert applied
        mapspec = {"layers": [
            {"id": "roads-1", "layout": {"visibility": "visible"}}]}
        summary = representation_binding_summary(doc, mapspec)
        assert summary["drift"] == [
            {"layer_id": "roads", "signal": "user_hidden_but_visible"}]
        # 物理层已隐藏 → 无 drift
        hidden_spec = {"layers": [
            {"id": "roads-1", "layout": {"visibility": "none"}}]}
        assert "drift" not in representation_binding_summary(doc, hidden_spec)

    def test_n4_batch_projection_aggregates_single_write(self):
        """N4：batch 投影聚合为一次 load→apply→save（单 op_id 收敛）。"""
        from app.services.gis_harness.representation_binding import (
            semantic_projection as sp)
        from app.contracts.mapspec_intents import PatchLayerPresentationIntent

        store = MemStore()
        run(req_service.ensure_document(store, "s-batch", "成都小学分布图", turn=1))
        spec = {"layers": [
            {"id": "a", "layout": {"visibility": "none"},
             "cartographic_intent": {"presentation_owner": "user",
                                     "expected_visible": False}},
            {"id": "b", "layout": {"visibility": "none"},
             "cartographic_intent": {"presentation_owner": "user",
                                     "expected_visible": False}},
        ]}

        class _Outcome:
            def __init__(self, layer_id):
                self.layer_id = layer_id
                self.status = "applied"
                self.visible = False

        class _Result:
            outcomes = [_Outcome("a"), _Outcome("b")]
            mapspec = spec
            mutation_id = "c:batch1"
            duplicate = False

        run(sp.project_user_batch_safe(
            "s-batch",
            [PatchLayerPresentationIntent(layer_id="a", visible=False),
             PatchLayerPresentationIntent(layer_id="b", visible=False)],
            _Result(), origin="user", actor="mapspec_route",
            mutation_id="c:batch1", store=store))
        doc = run(req_service.load_state(store, "s-batch")).document
        from app.services.gis_harness.requirement_ir.contracts import RequirementDocument
        parsed = RequirementDocument.model_validate(doc)
        # 聚合：单 op_id（绝对值集一次收敛两条 intent 效果）
        hidden_ops = [p for p in parsed.patches
                      if p.path == "representation.hidden_layer_ids"]
        assert len(hidden_ops) == 1
        assert list(parsed.intent.representation.hidden_layer_ids) == ["a", "b"]
