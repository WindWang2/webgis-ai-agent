"""Dispatch 血缘捕获 V3 —— 参数级 inputs 接线测试（§十 缺口 #1 闭合）。

三层验证：
1. capture_arg_lineage_refs + _resolve_references：ref/别名 → 规范 ref 捕获；
2. register_tool_artifact(inputs=...)：透传写台账边（有界、去自引用）；
3. 闭环：源 ref → 消费 → 产物记录 inputs 非空（dispatch 接缝血缘不再为空）。
"""

import pytest

from app.services.artifact_registry import (
    get_artifact,
    register_tool_artifact,
)
from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry, capture_arg_lineage_refs


def _fc(n: int = 3) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"geometry": {"type": "Point", "coordinates": [116.0 + i, 39.0]},
             "properties": {"v": i}}
            for i in range(n)
        ],
    }


class TestLineageCapture:
    async def test_ref_arg_captured(self):
        sid = "lin-cap-ref"
        ref = await session_data_manager.store(sid, _fc(), prefix="geojson")
        registry = ToolRegistry()
        with capture_arg_lineage_refs() as sink:
            await registry._resolve_references(sid, {"data": ref})
        assert sink == {ref}

    async def test_alias_normalized_to_canonical_ref(self):
        sid = "lin-cap-alias"
        ref = await session_data_manager.store(sid, _fc(), prefix="geojson")
        await session_data_manager.set_alias(sid, ref, "我的数据")
        registry = ToolRegistry()
        with capture_arg_lineage_refs() as sink:
            await registry._resolve_references(sid, {"data": "我的数据"})
        assert sink == {ref}

    async def test_no_capture_without_sink(self):
        """无捕获者时零行为变化（增值旁路契约）。"""
        sid = "lin-cap-off"
        ref = await session_data_manager.store(sid, _fc(), prefix="geojson")
        registry = ToolRegistry()
        resolved = await registry._resolve_references(sid, {"data": ref})
        assert isinstance(resolved, dict) and "data" in resolved

    async def test_plain_string_not_captured(self):
        sid = "lin-cap-plain"
        ref = await session_data_manager.store(sid, _fc(), prefix="geojson")
        registry = ToolRegistry()
        with capture_arg_lineage_refs() as sink:
            await registry._resolve_references(
                sid, {"data": ref, "radius": 300, "note": "hello"}
            )
        assert sink == {ref}

    async def test_failed_deref_not_captured(self):
        """解引用失败（抛错）不留下血缘证据——只有真实消费才计边。"""
        sid = "lin-cap-fail"
        registry = ToolRegistry()
        with pytest.raises(ValueError):
            with capture_arg_lineage_refs() as sink:
                await registry._resolve_references(sid, {"data": "ref:geojson-ghost"})
        assert sink == set()


class TestRegisterToolArtifactInputs:
    async def test_inputs_written_to_ledger_edge(self):
        sid = "lin-reg-inputs"
        parent = await session_data_manager.store(sid, _fc(), prefix="geojson")
        out = await session_data_manager.store(
            sid, _fc(1), prefix="geojson"
        )
        rec = await register_tool_artifact(
            sid, out, tool="buffer_analysis", inputs=[parent]
        )
        assert rec is not None and rec.inputs == [parent]
        back = await get_artifact(sid, out)
        assert back is not None and back.inputs == [parent]

    async def test_self_reference_excluded(self):
        sid = "lin-reg-self"
        out = await session_data_manager.store(sid, _fc(), prefix="geojson")
        rec = await register_tool_artifact(sid, out, tool="t", inputs=[out, "ref:geojson-a"])
        assert rec is not None
        assert out not in rec.inputs
        assert rec.inputs == ["ref:geojson-a"]

    async def test_inputs_bounded_to_16(self):
        sid = "lin-reg-bounded"
        out = await session_data_manager.store(sid, _fc(), prefix="geojson")
        many = [f"ref:geojson-{i:04d}" for i in range(20)]
        rec = await register_tool_artifact(sid, out, tool="t", inputs=many)
        assert rec is not None and len(rec.inputs) <= 16

    async def test_legacy_call_without_inputs_still_works(self):
        """既有调用方（不传 inputs）行为不变。"""
        sid = "lin-reg-legacy"
        out = await session_data_manager.store(sid, _fc(), prefix="geojson")
        rec = await register_tool_artifact(sid, out, tool="t")
        assert rec is not None and rec.inputs == []


class TestChainedLineage:
    async def test_second_hop_records_first_as_parent(self):
        """闭环：A 产出 ref1 →（模拟 dispatch 参数解引用捕获）→ B 产出
        ref2 时 inputs 含 ref1 —— dispatch 接缝血缘边可查询。"""
        sid = "lin-chain"
        ref1 = await session_data_manager.store(sid, _fc(), prefix="geojson")
        registry = ToolRegistry()
        with capture_arg_lineage_refs() as sink:
            resolved = await registry._resolve_references(sid, {"geojson": ref1})
        assert resolved["geojson"] is not None
        ref2 = await session_data_manager.store(sid, _fc(2), prefix="geojson")
        rec = await register_tool_artifact(
            sid, ref2, tool="overlay_analysis", inputs=sorted(sink)[:16]
        )
        assert rec is not None and rec.inputs == [ref1]
        # 血缘查询面能读到这条边（lineage_query 的父查询语义）
        view_sink = await get_artifact(sid, ref2)
        assert view_sink is not None and ref1 in view_sink.inputs
