"""Requirement IR 时态控制扩展 + temporal_view 工具面测试（GOAL 03 M3）。

覆盖：
- normalize_temporal_dynamics：对照期对/播放/保留图层显式表达；
- TimeSpec additive 字段落位 + digest 只收显式值（存量话语指纹不变）；
- 工具注册面（5 工具经 ToolRegistry）+ 缺 session 上下文的错误契约。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.requirement_ir.digest import (
    canonical_core,
)
from app.services.gis_harness.requirement_ir.normalize import (
    normalize_temporal_dynamics,
)


class TestNormalizeTemporalDynamics:
    def test_compare_two_years(self):
        cs, ce, pb, hold = normalize_temporal_dynamics("对比 2023 与 2026 年各区数量")
        assert (cs, ce) == ("2023", "2026")
        assert pb is False and hold is False

    def test_compare_vs(self):
        cs, ce, _, _ = normalize_temporal_dynamics("2023 vs 2026 固定颜色分级")
        assert (cs, ce) == ("2023", "2026")

    def test_playback(self):
        _, _, pb, _ = normalize_temporal_dynamics("显示成都小学近5年变化并逐年播放")
        assert pb is True

    def test_hold_layers(self):
        _, _, _, hold = normalize_temporal_dynamics("保留图层，只换年份到 2025")
        assert hold is True

    def test_no_expression(self):
        assert normalize_temporal_dynamics("显示学校分布") == ("", "", False, False)

    def test_compare_needs_two_years(self):
        # 只有一个年份 + 对照词 → 不构成对照期对（不虚构）。
        cs, ce, _, _ = normalize_temporal_dynamics("和 2023 年对比")
        assert (cs, ce) == ("", "")

    def test_single_year_not_compare(self):
        cs, ce, pb, _ = normalize_temporal_dynamics("2024 年的情况，请播放")
        assert (cs, ce) == ("", "")
        assert pb is True


class TestTimeSpecExtension:
    def _doc(self, query: str):
        from app.services.gis_harness.intent import (
            resolve_map_request_intent,
        )
        from app.services.gis_harness.requirement_ir import build_document

        return build_document(query, resolve_map_request_intent(query), turn=1)

    def test_compare_fields_populated(self):
        spec = self._doc("对比 2023 与 2026 年各区小学数量")
        assert spec.intent.time.compare_start == "2023"
        assert spec.intent.time.compare_end == "2026"

    def test_playback_and_series(self):
        spec = self._doc("显示成都小学近5年变化并逐年播放")
        assert spec.intent.time.playback_requested is True
        assert spec.intent.time.series is True

    def test_digest_stable_for_legacy_utterance(self):
        # additive 纪律：无时态控制表达的话语 digest 与扩展前一致
        # （显式才入语义核 —— 用规范值直接断言）。
        core_dict = canonical_core(self._doc("显示成都各区人口密度"))
        time_core = core_dict["time"]
        assert set(time_core.keys()) == {
            "range_start", "range_end", "granularity", "series"}

    def test_digest_changes_when_compare_expressed(self):
        core_dict = canonical_core(self._doc("对比 2023 与 2026 年各区小学数量"))
        assert core_dict["time"]["compare_start"] == "2023"
        assert core_dict["time"]["compare_end"] == "2026"


class TestToolSurface:
    def test_five_tools_registered(self):
        from app.tools.registry import ToolRegistry
        from app.tools.temporal_view_tools import register_temporal_view_tools

        reg = ToolRegistry()
        register_temporal_view_tools(reg)
        assert {
            "temporal_view_bind", "temporal_view_control",
            "temporal_view_compare", "temporal_view_unbind",
            "temporal_view_status",
        } <= set(reg._tools.keys())

    @pytest.mark.asyncio
    async def test_tools_require_session_context(self):
        from app.tools.registry import ToolRegistry
        from app.tools.temporal_view_tools import register_temporal_view_tools

        reg = ToolRegistry()
        register_temporal_view_tools(reg)
        entry = reg._tools["temporal_view_bind"]
        fn = entry if callable(entry) else getattr(entry, "func", None)
        out = await fn(ref_id="ref:x", session_id="")
        assert out == {"error": "Missing session_id context"}
