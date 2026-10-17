"""ADR-0219 C3：cartography 工具 grammar face pin 合并（sync 工具消费面）。"""
from __future__ import annotations

from app.tools.cartography import _requirement_grammar_pins
from app.services.gis_harness.representation_binding import requirement_to_mapspec as r2m
from app.lib.runtime.context import bind_runtime_context


class _FakeDoc:
    """grammar_face_pins 的 duck-typed 文档视图（避免整文档构造）。"""

    def __init__(self):
        from types import SimpleNamespace

        provenance = SimpleNamespace(origin="user")
        self.representation = SimpleNamespace(
            palette="蓝色", geometry_kind="热力图", pinned_channels={})
        self.intent = SimpleNamespace(
            representation=self.representation,
            field_provenance={
                "representation.palette": provenance,
                "representation.geometry_kind": provenance,
            })
        self.lifecycle = "draft"


class TestPinSnapshot:
    def test_snapshot_roundtrip(self):
        # _user_owns 走 field_provenance/section provenance；None override
        # 回落 section —— 此处构造最小可信文档面（真实文档路径由
        # test_requirement_to_mapspec 覆盖），这里测快照读写契约本身。
        r2m._PIN_SNAPSHOT.clear()
        r2m._PIN_SNAPSHOT["s-pin"] = {"pinned_palette": "blue"}
        assert r2m.pin_snapshot("s-pin") == {"pinned_palette": "blue"}
        assert r2m.pin_snapshot("s-absent") == {}

    def test_publish_bounded(self):
        r2m._PIN_SNAPSHOT.clear()
        for i in range(r2m._PIN_SNAPSHOT_MAX + 8):
            r2m.publish_pin_snapshot(f"s-{i}", _FakeDoc())
        assert len(r2m._PIN_SNAPSHOT) <= r2m._PIN_SNAPSHOT_MAX


class TestSyncHelper:
    def test_reads_snapshot_via_runtime_context(self):
        r2m._PIN_SNAPSHOT.clear()
        r2m._PIN_SNAPSHOT["s-ctx"] = {"pinned_palette": "blue",
                                      "pinned_representation": "visual_heatmap"}
        with bind_runtime_context(session_id="s-ctx"):
            assert _requirement_grammar_pins() == {
                "pinned_palette": "blue",
                "pinned_representation": "visual_heatmap"}

    def test_no_context_returns_empty(self):
        r2m._PIN_SNAPSHOT.clear()
        assert _requirement_grammar_pins() == {}

    def test_kill_switch(self, monkeypatch):
        r2m._PIN_SNAPSHOT.clear()
        r2m._PIN_SNAPSHOT["s-off"] = {"pinned_palette": "blue"}
        monkeypatch.setenv("GIS_REQUIREMENT_BINDING", "0")
        with bind_runtime_context(session_id="s-off"):
            assert _requirement_grammar_pins() == {}
