"""H10：dispatch 投影闸集成测试（GIS_ACTION_IR_BIND / strict / fail-open）。

生产契约：默认模式生产行为逐位不变（blocking findings 只降级为证据 +
telemetry）；strict 模式 blocking → typed 拒绝；投影故障 fail-open。
"""
from __future__ import annotations


from app.services.gis_action.legacy_adapter import (
    reset_usage_counters,
    usage_snapshot,
)
from app.services.gis_action.service import bind_action_ir


def _noop_registry(**meta):
    class _Reg:
        def metadata(self, name):
            if name == "noop":
                return {"side_effect": "pure", "deterministic": True,
                        **meta}
            if name == "legacy_probe":
                return {"side_effect": "unclassified", "status": "deprecated",
                        "fallback_tool": "modern_probe", **meta}
            return None
    return _Reg()


class TestBindDefaults:
    def setup_method(self):
        reset_usage_counters()

    def test_pure_tool_compiles_and_routes(self):
        bind = bind_action_ir("noop", {"x": 1}, registry=_noop_registry())
        assert bind is not None
        assert bind.status == "compiled"
        assert bind.kind == "inspect"
        assert not bind.denied
        assert bind.evidence["policy_version"] == "gis_action_ir_bind.v1"
        assert bind.evidence["finding_codes"] == []

    def test_mutation_tool_kind_from_side_effect(self):
        bind = bind_action_ir(
            "noop", {}, registry=_noop_registry(side_effect="state_mutation"))
        assert bind.kind == "mutate_presentation"

    def test_deprecated_tool_blocks_but_not_denied_by_default(self):
        bind = bind_action_ir("legacy_probe", {}, registry=_noop_registry())
        assert bind.status == "blocked"
        assert bind.evidence["finding_codes"] == ["TOOL_DEPRECATED"]
        assert bind.denied is False  # 默认模式：降级为证据，不阻断

    def test_args_non_dict_tolerated(self):
        bind = bind_action_ir("noop", "not-a-dict", registry=_noop_registry())
        assert bind is not None and bind.status == "compiled"

    def test_metadata_missing_fails_open_to_none(self):
        class _Broken:
            def metadata(self, name):
                raise RuntimeError("registry down")
        assert bind_action_ir("noop", {}, registry=_Broken()) is None

    def test_unregistered_tool_blocks_not_crashes(self):
        bind = bind_action_ir("ghost_tool", {}, registry=_noop_registry())
        assert bind.status == "blocked"
        assert "TOOL_UNRESOLVED" in bind.evidence["finding_codes"]

    def test_usage_counters_move(self):
        bind_action_ir("noop", {}, registry=_noop_registry())
        snap = usage_snapshot()
        assert snap["direct_routed"]["inspect"] == 1


class TestKillSwitches:
    def test_bind_off_returns_none(self, monkeypatch):
        monkeypatch.setenv("GIS_ACTION_IR_BIND", "0")
        assert bind_action_ir("noop", {}, registry=_noop_registry()) is None

    def test_strict_mode_denies_blocking(self, monkeypatch):
        monkeypatch.setenv("GIS_ACTION_IR_STRICT", "1")
        bind = bind_action_ir("legacy_probe", {}, registry=_noop_registry())
        assert bind.denied is True
        assert "TOOL_DEPRECATED" in bind.denial_text
        assert "modern_probe" in bind.denial_text

    def test_strict_mode_passes_clean_tool(self, monkeypatch):
        monkeypatch.setenv("GIS_ACTION_IR_STRICT", "1")
        bind = bind_action_ir("noop", {}, registry=_noop_registry())
        assert bind.denied is False


class TestDispatchIntegration:
    """真实 ToolDispatchService 路径：evidence 附加 + 行为不变。"""

    def _make_service(self):
        from app.services.tool_dispatch_service import ToolDispatchService
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()

        def probe(x: int = 0):
            return {"success": True, "summary": {"x": x}}

        reg.register("probe_tool", "probe tool", probe,
                     parameters={"type": "object",
                                 "properties": {"x": {"type": "integer"}},
                                 "required": []})
        return ToolDispatchService(registry=reg), reg

    async def test_success_path_carries_action_evidence(self):
        svc, _ = self._make_service()
        tc = {"id": "c1", "function": {"name": "probe_tool",
                                       "arguments": {"x": 1}}}
        result = await svc.dispatch(tc, "s-ir", set())
        assert result.status == "ok"
        assert result.action_evidence is not None
        assert result.action_evidence["status"] == "compiled"
        assert result.action_evidence["finding_codes"] == []

    async def test_bind_off_keeps_evidence_none(self, monkeypatch):
        monkeypatch.setenv("GIS_ACTION_IR_BIND", "0")
        svc, _ = self._make_service()
        tc = {"id": "c2", "function": {"name": "probe_tool",
                                       "arguments": {"x": 2}}}
        result = await svc.dispatch(tc, "s-ir-off", set())
        assert result.status == "ok"
        assert result.action_evidence is None

    async def test_dedup_semantics_unchanged(self):
        """去重语义不被投影闸改变（同参第二次 → repeated）。"""
        svc, _ = self._make_service()
        tc = {"id": "c3", "function": {"name": "probe_tool",
                                       "arguments": {"x": 3}}}
        executed: set = set()
        first = await svc.dispatch(tc, "s-dedup", executed)
        second = await svc.dispatch(tc, "s-dedup", executed)
        assert first.status == "ok"
        assert second.status == "repeated"

    async def test_result_dataclass_accepts_missing_evidence(self):
        """additive 字段缺省 = 既有构造点零改动兼容。"""
        from app.services.tool_dispatch_service import ToolDispatchResult
        r = ToolDispatchResult(status="ok", llm_payload="x", slim_event={},
                               geojson_ref=None, raw_result={}, error_msg=None)
        assert r.action_evidence is None
