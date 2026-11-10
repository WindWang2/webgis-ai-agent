"""G17 容器矩阵真实验收：GPKG 多层选择 / ZIP 双套归组 / KML / 部分成功重试。

对应任务书场景 1、3、15（+ 场景 4 的声明 CRS 路径）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import (  # noqa: E402
    MINIMAL_KML,
    make_csv_bytes,
    make_geojson_bytes,
    make_three_layer_gpkg,
    make_zip_no_prj,
    make_zip_two_sets,
)

from app.core.config import settings
from app.services.data_import.service import DataImportError, DataImportService
from app.services.session_data import session_data_manager

pytestmark = pytest.mark.heavy

SESSION = "sess-g17-containers-0001"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    return DataImportService()


def _key(state, display: str) -> str:
    return next(m.member_key for m in state.members if m.display_name == display)


async def test_gpkg_three_layers_select_two(env, tmp_path):
    gpkg = tmp_path / "layers.gpkg"
    make_three_layer_gpkg(gpkg)
    state = await env.create_batch(SESSION, "user:t", [("layers.gpkg", gpkg.read_bytes())])

    layers = {m.display_name: m for m in state.members}
    assert set(layers) == {"pt_layer", "line_layer", "poly_layer"}
    assert layers["pt_layer"].feature_count == 2
    assert layers["pt_layer"].geometry_type == "Point"
    assert layers["line_layer"].crs  # EPSG:32650 源元数据
    assert layers["poly_layer"].crs is None
    assert layers["poly_layer"].crs_source == "unknown"

    # 只选两层（场景 1）
    sel = ["pt_layer", "line_layer"]
    saved = await env.save_plan(
        state.batch_id, SESSION,
        member_configs={layers[n].member_key: {} for n in sel},
        selected=[layers[n].member_key for n in sel],
    )
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    receipts = out["receipts"]
    assert out["status"] == "completed"
    assert set(receipts) == {layers[n].member_key for n in sel}   # 未选层无产物

    # 各自数量 / schema / CRS（线层真实转换到 4326）
    d1 = await session_data_manager.get(SESSION, receipts[layers["pt_layer"].member_key]["output_ref"])
    d2 = await session_data_manager.get(SESSION, receipts[layers["line_layer"].member_key]["output_ref"])
    assert len(d1["geojson"]["features"]) == 2
    assert set(d1["geojson"]["features"][0]["properties"]) == {"station", "code"}
    assert d1["geojson"]["features"][0]["properties"]["code"] == "00123"
    assert len(d2["geojson"]["features"]) == 2
    line = d2["geojson"]["features"][0]["geometry"]["coordinates"][0]
    assert 115.0 < line[0] < 118.0 and 38.0 < line[1] < 42.0   # 已从 32650 转换
    assert receipts[layers["line_layer"].member_key]["crs_evidence"]["transform_target"] == "EPSG:4326"


async def test_zip_two_sets_grouped_correctly(env, tmp_path):
    zp = tmp_path / "two.zip"
    make_zip_two_sets(zp, tmp_path)
    state = await env.create_batch(SESSION, "user:t", [("two.zip", zp.read_bytes())])
    members = {m.display_name: m for m in state.members}
    # 两套按 basename 归组；孤儿 .shp 不可导入且披露
    assert set(members) == {"setA (data)", "setB (data)"}
    assert members["setA (data)"].feature_count == 2
    assert members["setB (data)"].feature_count == 3
    assert any("orphan" in n or "缺少组件" in n for n in state.notes)

    keys = [members["setA (data)"].member_key, members["setB (data)"].member_key]
    await env.save_plan(state.batch_id, SESSION, member_configs={k: {} for k in keys}, selected=keys)
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    receipts = out["receipts"]
    assert out["status"] == "completed"

    da = await session_data_manager.get(SESSION, receipts[keys[0]]["output_ref"])
    db = await session_data_manager.get(SESSION, receipts[keys[1]]["output_ref"])
    # 不串用：A 只有 a_name，B 只有 b_value（场景 3）
    assert set(da["geojson"]["features"][0]["properties"]) == {"a_name"}
    assert set(db["geojson"]["features"][0]["properties"]) == {"b_value"}
    assert len(db["geojson"]["features"]) == 3


async def test_kml_member_discovered_and_committed(env, tmp_path):
    kml = tmp_path / "doc.kml"
    kml.write_bytes(MINIMAL_KML)
    state = await env.create_batch(SESSION, "user:t", [("doc.kml", MINIMAL_KML)])
    assert len(state.members) == 1
    m = state.members[0]
    assert m.kind.value == "vector"
    await env.save_plan(state.batch_id, SESSION, {m.member_key: {}}, selected=[m.member_key])
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out["receipts"][m.member_key]
    assert r["status"] == "completed", r
    assert r["output_rows"] == 2


async def test_shapefile_missing_prj_declared_crs(env, tmp_path):
    """场景 4：缺 .prj + 用户声明 EPSG:32650 → 计划转换正确，输出落在 4326 合理范围。"""
    zp = tmp_path / "noprj.zip"
    make_zip_no_prj(zp, tmp_path)
    state = await env.create_batch(SESSION, "user:t", [("noprj.zip", zp.read_bytes())])
    m = state.members[0]
    assert m.crs is None and m.crs_source == "unknown"

    # 不声明 → 提交被拒（CRS_UNCONFIRMED），不静默按经纬度
    await env.save_plan(state.batch_id, SESSION, {m.member_key: {}}, selected=[m.member_key])
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out["receipts"][m.member_key]
    assert r["status"] == "failed" and r["error_code"] == "CRS_UNCONFIRMED"

    # 声明后成功（修正失败成员重试，场景 15 机制）
    await env.save_plan(state.batch_id, SESSION, {m.member_key: {"source_crs_declared": "EPSG:32650"}})
    out2 = await env.start_commit(state.batch_id, SESSION, background=False)
    r2 = out2["receipts"][m.member_key]
    assert r2["status"] == "completed", r2
    assert r2["crs_evidence"]["crs_source"] == "declared"
    assert r2["crs_evidence"]["transform_target"] == "EPSG:4326"
    d = await session_data_manager.get(SESSION, r2["output_ref"])
    c = d["geojson"]["features"][0]["geometry"]["coordinates"]
    assert 115.0 < c[0] < 118.0 and 38.0 < c[1] < 42.0


async def test_partial_success_retry_keeps_completed(env, tmp_path):
    """场景 15：一项成功、一项失败 → 收据 partial；修正后成功项不重写。"""
    good = make_geojson_bytes(
        [{"type": "Feature", "properties": {"n": 1},
          "geometry": {"type": "Point", "coordinates": [116.3, 39.8]}}]
    )
    # gb18030 编码 CSV，但计划声明 utf-8 → 解释失败（诚实拒绝，不静默回退）
    gbk_csv = make_csv_bytes(
        [["116.3", "39.8", "北京"]], ["lng", "lat", "城市"], encoding="gb18030"
    )
    state = await env.create_batch(
        SESSION, "user:t",
        [("good.geojson", good), ("table.csv", gbk_csv)],
    )
    keys = {m.display_name: m.member_key for m in state.members}
    await env.save_plan(
        state.batch_id, SESSION,
        member_configs={keys["table.csv"]: {"encoding": "utf-8"}},
        selected=list(keys.values()),
    )
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    assert out["status"] == "partial"
    r_good = out["receipts"][keys["good.geojson"]]
    r_bad = out["receipts"][keys["table.csv"]]
    assert r_good["status"] == "completed"
    assert r_bad["status"] == "failed" and r_bad["error_code"] == "ENCODING_UNDECODABLE"

    # 修正：声明 CRS（lng/lat 命名的点列允许按声明 4326 落图；编码探测回退 gb18030 被披露）
    await env.save_plan(
        state.batch_id, SESSION,
        {keys["table.csv"]: {"source_crs_declared": "EPSG:4326"}},
    )
    out2 = await env.start_commit(state.batch_id, SESSION, background=False)
    assert out2["status"] == "completed"
    r_good2 = out2["receipts"][keys["good.geojson"]]
    r_bad2 = out2["receipts"][keys["table.csv"]]
    assert r_bad2["status"] == "completed"
    # 成功成员未被重写（复用同一 ref）
    assert r_good2["output_ref"] == r_good["output_ref"]
    assert r_good2["duplicate"] is True
