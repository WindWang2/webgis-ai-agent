"""出版冻结导出旅程（验收旅程 5 缩小版）—— 冻结工件被真实导出消费。

链路：播种行政区（2 大 1 小，inline→ref 载体）→ apply 纸面综合（commit，
冻结 paper 工件）→ 同一 MapSpec 在不同 zoom 导出 SVG：
- 纸面等效 zoom 跨度内：走冻结工件（小县被消除 —— 真实图面证据）；
- 跨度外（street zoom）：原始数据（小县在场）；
- 原始 ref 数据全程不变。
"""
from __future__ import annotations

import copy
import uuid

import pytest

from app.services.gis_harness.paper_generalization import (
    apply_paper_scale_generalization,
)
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import mapspec_store_instance
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed
from app.services.session_data import session_data_manager


def _districts_fc() -> dict:
    def sq(x0, y0, x1, y1, fid):
        ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
        return {"type": "Feature", "id": fid,
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {"name": fid}}

    return {"type": "FeatureCollection", "features": [
        sq(115.0, 38.0, 116.0, 39.0, "district-a"),
        sq(116.0, 38.0, 117.0, 39.0, "district-b"),
        sq(116.0, 39.0, 116.004, 39.004, "tiny-county"),
    ]}


@pytest.fixture()
async def session():
    sid = f"pgenexp-{uuid.uuid4().hex[:10]}"
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(sid, UpsertLayerIntent(
        layer={"id": "districts", "type": "fill", "source": "src-districts",
               "paint": {"fill-color": "#4a90d9"}},
        source_data={"type": "geojson", "inlineData": _districts_fc()},
    ))
    assert not res.is_error, res.error_msg
    yield sid
    session_data_manager._store.pop(sid, None)
    session_data_manager._map_state.pop(sid, None)
    await mapspec_store_instance.discard_mapspec(sid)


async def _read_ref_fc(session_id: str, ref: str):
    """ref → FeatureCollection（直读；别名解析为回退 —— M9 同款）。"""
    res = await session_data_manager.get_ref_data_shared(session_id, ref)
    if not getattr(res, "success", False):
        try:
            resolved = await session_data_manager.resolve_alias(session_id, ref)
        except Exception:  # noqa: BLE001
            resolved = None
        if resolved and resolved != ref:
            res = await session_data_manager.get_ref_data_shared(
                session_id, resolved)
    assert getattr(res, "success", False), getattr(res, "error", "")
    data = res.data
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    return data if isinstance(data, dict) and \
        data.get("type") == "FeatureCollection" else None


async def _hydrated_spec(spec: dict, session_id: str) -> dict:
    """导出前内联化全部 ref 载荷（主源 + 各窗口工件源；M9 同款）。"""
    hydrated = copy.deepcopy(spec)
    for layer in hydrated["layers"]:
        sources = hydrated.get("sources") or {}
        stack = [layer.get("source")] + [
            w.get("source") for w in
            (layer.get("representation") or {}).get("windows") or []
            if isinstance(w, dict) and w.get("source")]
        for sid_ in stack:
            if not sid_ or sid_ not in sources:
                continue
            entry = sources[sid_]
            ref = entry.get("ref_id") or entry.get("ref")
            if not isinstance(ref, str):
                continue
            fc = await _read_ref_fc(session_id, ref)
            if fc is not None:
                entry["inlineData"] = fc
    # 嵌套 inlineData 包装解包（M9 同款：{type:geojson, inlineData:FC} → FC）
    for entry in hydrated.get("sources", {}).values():
        inline = entry.get("inlineData") if isinstance(entry, dict) else None
        if isinstance(inline, dict) and inline.get("type") == "geojson" \
                and isinstance(inline.get("inlineData"), dict):
            entry["inlineData"] = inline["inlineData"]
    return hydrated


def _canonical(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


class TestFrozenExportJourney:
    async def test_paper_window_uses_frozen_artifact(self, session):
        receipt = await apply_paper_scale_generalization(
            session, "districts", mode="commit", denominator=500_000,
            page_text="A4横版", purpose="overview",
            operation_id="op-exp-1")
        assert receipt["status"] == "committed"
        window = receipt["export_window"]
        paper_zoom = (float(window["min_zoom"]) +
                      float(window["max_zoom"])) / 2.0

        # 工件 ref 真实存了消除后的派生数据（2 要素，小县消失）
        art_fc = await _read_ref_fc(session, receipt["artifact_ref"])
        assert art_fc is not None
        art_ids = [f["id"] for f in art_fc["features"]]
        assert "tiny-county" not in art_ids
        assert len(art_ids) == 2

        spec = await mapspec_store_instance.get_mapspec(session)
        hydrated = await _hydrated_spec(spec, session)

        # 纸面 zoom：走冻结工件（诊断 + 图面无小县）
        hydrated["view"] = {"zoom": paper_zoom, "center": [116.0, 38.5]}
        out_paper = compile_mapspec_to_svg_detailed(
            hydrated, include_chrome=True)
        codes = [d.get("code") for d in out_paper.diagnostics]
        assert "layer_generalized_artifact" in codes
        assert out_paper.feature_count == 2, \
            "纸面 zoom 必须画冻结工件（小县被消除）"

        # 紧邻窗外 zoom（视口仍覆盖全区）：原始数据（小县在场、无工件诊断）
        hydrated["view"] = {"zoom": 9.5, "center": [116.0, 38.5]}
        out_raw = compile_mapspec_to_svg_detailed(
            hydrated, include_chrome=True)
        codes_raw = [d.get("code") for d in out_raw.diagnostics]
        assert "layer_generalized_artifact" not in codes_raw
        assert out_raw.feature_count == 3

    async def test_original_source_untouched_after_freeze(self, session):
        spec0 = await mapspec_store_instance.get_mapspec(session)
        src0 = spec0["sources"]["src-districts"]
        # 播种载体 = inline（UpsertLayer source_data 不自动转 ref）
        # store_data 约定：inlineData = {type: geojson, inlineData: FC}
        fc0 = (src0.get("inlineData") or {}).get("inlineData")
        if fc0 is None:
            fc0 = src0.get("inlineData")
        assert [f["id"] for f in fc0["features"]] == [
            "district-a", "district-b", "tiny-county"]
        src0_snapshot = _canonical(src0)

        await apply_paper_scale_generalization(
            session, "districts", mode="commit", denominator=500_000,
            operation_id="op-exp-2")

        spec1 = await mapspec_store_instance.get_mapspec(session)
        src1 = spec1["sources"]["src-districts"]
        assert _canonical(src1) == src0_snapshot, \
            "原始 source 条目（含 inlineData）必须逐字节不变"
