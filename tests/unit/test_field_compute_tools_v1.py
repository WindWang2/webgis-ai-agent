"""G19 Pi 工具面回归（WP10）—— 经真实 ``ToolRegistry.dispatch`` 生产路径。

覆盖：工具可发现且名称不冲突、有界上下文（不回传数据本体）、preview→apply
→result 走同一 service、下一轮「用刚才的新密度字段做分级图」解析**真实**
成果（真实字段名 + 既有 CartographyService 分类可用）、失效/缺失单位如实披露。

说明：真实 Pi 进程 + stub LLM 的全链路需要 Node extension/runtime，本环境
不具备；该面在 TEST_MATRIX 中标记 blocked，不以 monkeypatch 冒充端到端通过。
"""
from __future__ import annotations

import pytest

from app.services.cartography_service import CartographyService
from app.services.data_preparation.field_compute import service
from app.services.dataset_semantics import reset_dataset_semantic_store
from app.services.session_data import session_data_manager as sdm
from app.tools.field_compute_tools import register_field_compute_tools
from app.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path)
    reset_dataset_semantic_store()
    yield
    reset_dataset_semantic_store()


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_field_compute_tools(r)
    return r


def _fc():
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.0]},
         "properties": {"人口": 1000 + i * 100, "面积 平方千米": 10 + i,
                        "名称": f"区{i}"}}
        for i in range(5)]}


async def _seed(sid):
    return await sdm.store(sid, {"geojson": _fc(), "crs": "EPSG:4326",
                                 "crs_source": "declared"}, prefix="test")


class TestToolRegistry:
    def test_four_tools_registered_without_collision(self, registry):
        names = {t["name"] if isinstance(t, dict) else t
                 for t in registry.list_tools()}
        assert {"field_compute_catalog", "field_compute_preview",
                "field_compute_apply", "field_compute_result"} <= names

    async def test_catalog_is_bounded_and_has_no_data(self, registry):
        out = await registry.dispatch("field_compute_catalog", {},
                                      session_id="sess-fc-tools")
        assert out["success"] is True
        assert out["function_set_version"] == "g19-fc-1.0"
        assert 0 < len(out["functions"]) <= 40
        assert all("sample" not in f and "value" not in f
                   for f in out["functions"])


class TestToolJourney:
    async def test_dispatch_preview_apply_result_and_next_turn_field(self, registry):
        sid = "sess-fc-tools-journey"
        ref = await _seed(sid)
        columns = [
            {"name": "人口密度", "expression": "{人口} / {面积 平方千米}"},
            {"name": "密度分级", "expression": "if({人口密度} > 100, '高', '低')"},
        ]
        pv = await registry.dispatch(
            "field_compute_preview",
            {"source_ref": ref, "columns": columns, "session_id": sid,
             "field_units": {"人口": "persons",
                             "面积 平方千米": "square_kilometers"}},
            session_id=sid)
        assert pv["success"] is True and pv["status"] == "previewed"
        assert pv["kind"] == "sample" and pv["total_rows"] == 5
        # 模型只看到有界信息，绝无完整要素集
        assert len(pv["before_after"]) <= 5
        assert "features" not in pv and "geojson" not in pv
        fp = pv["confirmation"]["params_fingerprint"]

        applied = await registry.dispatch(
            "field_compute_apply",
            {"plan_id": pv["plan_id"], "expected_fingerprint": fp,
             "session_id": sid},
            session_id=sid)
        assert applied["success"] is True and applied["status"] == "ready"
        assert applied["output_ref"].startswith("ref:fc-")
        assert applied["row_count"] == 5
        assert {f["name"] for f in applied["derived_fields"]} == {
            "人口密度", "密度分级"}
        density = next(f for f in applied["derived_fields"]
                       if f["name"] == "人口密度")
        assert density["unit_status"] == "derived"
        assert density["measurement_kind"] == "density"
        assert applied["persistence"] == "session_ref"   # 不冒充持久成功
        assert applied["precision_policy"] in ("", "bigint_as_string",
                                               "not_evaluated", None)
        assert "output_ref" in applied["guidance"]
        assert "人口密度" in applied["guidance"]

        got = await registry.dispatch(
            "field_compute_result",
            {"output_ref": applied["output_ref"], "session_id": sid},
            session_id=sid)
        assert got["success"] is True and got["readable"] is True
        assert "人口密度" in got["fields"] and got["row_count"] == 5

        # 下一轮「用刚才的新密度字段做分级图」：解析真实成果，不重新猜字段。
        # 用既有分类引擎验证该数值字段确实可用于分级（数据面证据）。
        values = [float(r["人口密度"]) for r in got["sample_rows"]
                  if r.get("人口密度") is not None]
        assert values and min(values) > 0
        breaks = CartographyService.classify(values, method="equal_interval", k=3)
        assert len(breaks) >= 2 and breaks == sorted(breaks)
        # 与 UI 用同一公式：service 层再跑一次等价表达式，结果相同
        created = await service.create_plan({
            "session_id": sid, "source_ref": ref, "columns": columns,
            "field_units": {"人口": "persons",
                            "面积 平方千米": "square_kilometers"},
        })
        pv2 = await service.preview(created["plan"].plan_id, sid)
        assert pv2["columns"][0]["formula"] == pv["columns"][0]["formula"]

    async def test_missing_unit_is_disclosed_not_invented(self, registry):
        sid = "sess-fc-tools-units"
        ref = await _seed(sid)
        pv = await registry.dispatch(
            "field_compute_preview",
            {"source_ref": ref,
             "columns": [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
             "session_id": sid},
            session_id=sid)
        assert pv["success"] is True
        col = pv["columns"][0]
        assert col["unit_status"] == "undeclared" and col["unit"] == ""
        applied = await registry.dispatch(
            "field_compute_apply",
            {"plan_id": pv["plan_id"],
             "expected_fingerprint": pv["confirmation"]["params_fingerprint"],
             "session_id": sid},
            session_id=sid)
        assert applied["success"] is True
        assert applied["unit_summary"]["密度"]["declared"] is False
        assert applied["unit_summary"]["密度"]["unit_status"] == "undeclared"

    async def test_invalid_formula_returns_typed_code_no_host_side_effect(self, registry):
        sid = "sess-fc-tools-safety"
        ref = await _seed(sid)
        out = await registry.dispatch(
            "field_compute_preview",
            {"source_ref": ref,
             "columns": [{"name": "坏", "expression": "__import__('os').system('echo x')"}],
             "session_id": sid},
            session_id=sid)
        assert out["success"] is False
        assert out["code"] in ("UNKNOWN_NODE", "UNKNOWN_FUNCTION",
                               "UNKNOWN_FIELD", "UNKNOWN_OPERATOR")
        assert "hint" in out
        # 主进程无副作用：没有任何派生成果被登记
        assert (await sdm.get(sid, ref))["geojson"]["features"][0]["properties"]["人口"] == 1000

    async def test_stale_fingerprint_rejected(self, registry):
        sid = "sess-fc-tools-stale"
        ref = await _seed(sid)
        pv = await registry.dispatch(
            "field_compute_preview",
            {"source_ref": ref,
             "columns": [{"name": "密度", "expression": "{人口} / {面积 平方千米}"}],
             "session_id": sid},
            session_id=sid)
        out = await registry.dispatch(
            "field_compute_apply",
            {"plan_id": pv["plan_id"], "expected_fingerprint": "outdated",
             "session_id": sid},
            session_id=sid)
        assert out["success"] is False and out["code"] == "STALE_PLAN"
