"""prepare_dataset 工具 V1 —— M6 单测（ToolDispatchService 真实分发链 + 四态判别式）。

纪律：不直接调用工具函数 —— 全部经 ToolDispatchService.dispatch（生产入口
同款），会话绑定走 bind_runtime_context（_resolve_session_id 的可信来源）。
"""
from __future__ import annotations

import json

import pytest

from app.core.runtime_context import bind_runtime_context
from app.services.tool_dispatch_service import ToolDispatchService
from app.tools import init_tools
from app.tools.registry import ToolRegistry


@pytest.fixture(scope="module")
def registry():
    reg = ToolRegistry()
    init_tools(reg)
    return reg


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path)
    from app.services.dataset_semantics import reset_dataset_semantic_store
    reset_dataset_semantic_store()
    yield
    reset_dataset_semantic_store()


def _tc(args: dict) -> dict:
    return {"id": "call_1", "function": {"name": "prepare_dataset",
                                         "arguments": args}}


async def _dispatch(registry, args: dict, session_id: str):
    service = ToolDispatchService(registry=registry)
    executed: set = set()
    with bind_runtime_context(session_id=session_id):
        result = await service.dispatch(_tc(args), session_id, executed)
    assert result.status == "ok", f"dispatch failed: {result.error_msg}"
    return result.raw_result


def _school_payload(n=20, bad_every=0, swap=False, geom_only_bad=False):
    """geom_only_bad：GeoJSON 形态 —— 坏坐标只在几何里、属性列不含坐标
    （列值域不被污染，质量旗标仍触发自动过滤 —— auto prepared 的真实形态；
    CSV 上传形态则坏值同时污染列，诚实路径是先过滤再映射，见 E2E）。"""
    features = []
    for i in range(n):
        lon, lat = 104.0 + (i % 10) * 0.01, 30.0 + (i % 5) * 0.01
        if bad_every and i % bad_every == 0:
            lon, lat = 999.0, 999.0
        coords = [lat, lon] if swap else [lon, lat]
        props = {"学校编号": f"S{i:03d}", "学校名称": f"第{i}小学"}
        if not geom_only_bad:
            props["经度"] = lat if swap else lon
            props["纬度"] = lon if swap else lat
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": coords},
            "properties": props,
        })
    return {"geojson": {"type": "FeatureCollection", "features": features},
            "crs": "EPSG:4326"}


async def _seed_ref(session_id, payload):
    from app.services.session_data import session_data_manager
    return await session_data_manager.store(session_id, payload, prefix="test")


class TestDispatchRealChain:
    async def test_no_session_typed_error(self, registry):
        service = ToolDispatchService(registry=registry)
        executed: set = set()
        result = await service.dispatch(
            _tc({"source_ref": "ref:test-1"}), "", executed)
        # dispatch 层拒绝（无会话）或工具层 typed NO_SESSION —— 都不假成功
        assert result.status in ("ok", "error")
        if result.status == "ok":
            assert result.raw_result["success"] is False
            assert result.raw_result["code"] == "NO_SESSION"

    async def test_invalid_ref_typed_error(self, registry):
        """非法 ref：工具 typed INVALID_REF（dispatch 归为 error 态；
        raw_result 携带稳定码）。"""
        service = ToolDispatchService(registry=registry)
        executed: set = set()
        with bind_runtime_context(session_id="sess-tool-1"):
            result = await service.dispatch(
                _tc({"source_ref": "not-a-ref", "session_id": "sess-tool-1"}),
                "sess-tool-1", executed)
        assert result.status == "error"
        assert result.raw_result["code"] == "INVALID_REF"

    async def test_missing_ref_typed_error(self, registry):
        """缺 ref：dispatch 层解引用失败即 typed 拒绝（早于工具体 —— 同样
        不假成功；错误信息携带缺失 ref 标识）。"""
        service = ToolDispatchService(registry=registry)
        executed: set = set()
        with bind_runtime_context(session_id="sess-tool-2"):
            result = await service.dispatch(
                _tc({"source_ref": "ref:missing-1", "session_id": "sess-tool-2"}),
                "sess-tool-2", executed)
        assert result.status == "error"
        assert "ref:missing-1" in str(result.error_msg)

    async def test_llm_payload_has_no_row_data(self, registry):
        """有界红线：llm_payload 绝不携带行数据（长度可枚举断言）。"""
        sid = "sess-tool-3"
        ref = await _seed_ref(sid, _school_payload(30))
        service = ToolDispatchService(registry=registry)
        executed: set = set()
        with bind_runtime_context(session_id=sid):
            result = await service.dispatch(
                _tc({"source_ref": ref}), sid, executed)
        assert result.status == "ok"
        payload_blob = json.dumps(result.llm_payload, ensure_ascii=False)
        # 30 行数据本体绝不进 payload：即便全量序列化也远小于数据本体
        assert len(payload_blob) < 6000
        # 两条独立红线（R1 #11：原弱析取拆分）—— 无行样本值 + 无样本键
        assert "第29小学" not in payload_blob
        assert "sample" not in result.llm_payload
        assert result.raw_result["status"] in ("ready", "requires_input")


class TestFourStateSemantics:
    async def test_ready_state_inspect_only(self, registry):
        sid = "sess-tool-4"
        ref = await _seed_ref(sid, _school_payload(20))
        out = await _dispatch(registry, {"source_ref": ref}, sid)
        assert out["status"] == "ready"
        assert out["receipt_summary"]["blocked"] is False
        # 提案：学校名称 → name
        assert out["proposal"].get("学校名称") == "name"

    async def test_blocked_state_on_axis_swap(self, registry):
        sid = "sess-tool-5"
        ref = await _seed_ref(sid, _school_payload(20, swap=True))
        out = await _dispatch(registry, {"source_ref": ref}, sid)
        assert out["status"] == "blocked"
        codes = [f["code"] for f in out["blocked_findings"]]
        assert "axis_order_suspect" in codes

    async def test_requires_input_state(self, registry):
        # 全字符串无信号字段 → 无候选 → 澄清
        payload = {"geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0, 30.0]},
             "properties": {"神秘列": "a", "备注": "b"}} for _ in range(5)]},
            "crs": "EPSG:4326"}
        sid = "sess-tool-6"
        ref = await _seed_ref(sid, payload)
        out = await _dispatch(registry, {"source_ref": ref}, sid)
        assert out["status"] == "requires_input"
        fields = [c["field"] for c in out["clarifications"]]
        assert "神秘列" in fields

    async def test_role_hints_resolve_clarification(self, registry):
        payload = {"geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0, 30.0]},
             "properties": {"神秘列": "a"}} for _ in range(5)]},
            "crs": "EPSG:4326"}
        sid = "sess-tool-7"
        ref = await _seed_ref(sid, payload)
        out = await _dispatch(
            registry, {"source_ref": ref, "role_hints": {"神秘列": "category"}},
            sid)
        # 提示被采纳（字符串角色 + dtype 一致）→ 无澄清 → ready
        assert out["status"] == "ready"
        assert out["proposal"].get("神秘列") == "category"

    async def test_prepared_state_full_auto(self, registry):
        # GeoJSON 形态：坏几何行 + 干净属性列 → 映射高置信 + 质量旗标
        # 触发自动过滤 → auto_execute 一步到位 prepared
        sid = "sess-tool-8"
        ref = await _seed_ref(sid, _school_payload(20, bad_every=10,
                                                   geom_only_bad=True))
        out = await _dispatch(
            registry, {"source_ref": ref, "auto_execute": True}, sid)
        assert out["status"] == "prepared"
        assert out["output_ref"].startswith("ref:prep-")
        assert out["receipt_summary"]["row_before"] == 20
        assert out["receipt_summary"]["row_after"] == 18
        # 输出 ref 可直接被再次检查（链式准备）
        out2 = await _dispatch(registry, {"source_ref": out["output_ref"]}, sid)
        assert out2["status"] == "ready"
        assert out2["receipt_summary"]["blocked"] is False

    async def test_auto_execute_respects_clarifications(self, registry):
        """R1 #2 清偿回归：CSV 形态脏列 → 澄清项存在 → auto 也不执行。"""
        sid = "sess-tool-10"
        ref = await _seed_ref(sid, _school_payload(20, bad_every=10))
        out = await _dispatch(
            registry, {"source_ref": ref, "auto_execute": True}, sid)
        assert out["status"] == "requires_input"
        assert out["clarifications"]
        assert "output_ref" not in out

    async def test_blocked_never_executes_even_with_auto(self, registry):
        sid = "sess-tool-9"
        ref = await _seed_ref(sid, _school_payload(20, swap=True))
        out = await _dispatch(
            registry, {"source_ref": ref, "auto_execute": True}, sid)
        assert out["status"] == "blocked"
        assert "output_ref" not in out
