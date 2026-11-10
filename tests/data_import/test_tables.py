"""G17 表格解释矩阵：CSV 变体 / 前导零与长整型 / WKT / 纯表 / XLSX 工作表。

对应任务书场景 5、6、7、8、9、10、12。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_csv_bytes  # noqa: E402

from app.core.config import settings
from app.services.data_import.service import DataImportError, DataImportService
from app.services.session_data import session_data_manager

pytestmark = pytest.mark.heavy

SESSION = "sess-g17-tables-0001"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    return DataImportService()


def _key(state, display: str) -> str:
    return next(m.member_key for m in state.members if m.display_name == display)


async def test_csv_easting_northing_declared_crs(env, tmp_path):
    """场景 5：自定义列名 Easting/Northing + 声明投影 CRS → 已知位置，不按经纬度猜。"""
    csv = make_csv_bytes(
        [[500000, 4400000, "站A"], [500500, 4400500, "站B"]],
        ["Easting", "Northing", "名称"],
    )
    state = await env.create_batch(SESSION, "user:t", [("survey.csv", csv)])
    m = state.members[0]
    # 未声明 CRS：预览为表（不落图、不假称确认 —— 场景 11 联动）
    await env.save_plan(
        state.batch_id, SESSION,
        {m.member_key: {"mode": "points", "x_column": "Easting", "y_column": "Northing"}},
        selected=[m.member_key],
    )
    pv = await env.preview_members(state.batch_id, SESSION, [m.member_key], row_limit=10)
    p = pv["previews"][0]
    assert p["requires_crs_confirmation"] is True
    assert p["location_preview"] is None
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    assert out["receipts"][m.member_key]["error_code"] == "CRS_UNCONFIRMED"

    # 声明 EPSG:32650 → 落到已知位置
    await env.save_plan(
        state.batch_id, SESSION,
        {m.member_key: {
            "mode": "points", "x_column": "Easting", "y_column": "Northing",
            "source_crs_declared": "EPSG:32650",
        }},
    )
    out2 = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out2["receipts"][m.member_key]
    assert r["status"] == "completed", r
    assert r["crs_evidence"]["crs_source"] == "declared"
    d = await session_data_manager.get(SESSION, r["output_ref"])
    coords = d["geojson"]["features"][0]["geometry"]["coordinates"]
    assert 112.0 < coords[0] < 118.0 and 38.0 < coords[1] < 42.0  # 32650→4326 合理范围


async def test_stale_plan_version_rejected_after_change(env, tmp_path):
    """场景 12：改 X/Y 后用旧计划版本提交 → 明确拒绝。"""
    csv = make_csv_bytes([[116.3, 39.8]], ["x", "y"])
    state = await env.create_batch(SESSION, "user:t", [("pts.csv", csv)])
    m = state.members[0]
    await env.save_plan(
        state.batch_id, SESSION,
        {m.member_key: {"mode": "points", "x_column": "x", "y_column": "y",
                        "source_crs_declared": "EPSG:4326"}},
        selected=[m.member_key],
    )
    v1 = (await env.get_batch(state.batch_id, SESSION)).plan_version
    await env.preview_members(state.batch_id, SESSION, [m.member_key], row_limit=10)
    # 变更解释（对调 X/Y）
    await env.save_plan(
        state.batch_id, SESSION,
        {m.member_key: {"mode": "points", "x_column": "y", "y_column": "x",
                        "source_crs_declared": "EPSG:4326"}},
    )
    with pytest.raises(DataImportError) as ei:
        await env.start_commit(state.batch_id, SESSION, expected_plan_version=v1, background=False)
    assert ei.value.code == "PLAN_VERSION_CONFLICT"
    # 旧预览失效：成员不再处于 previewed 绑定
    fresh = await env.get_batch(state.batch_id, SESSION)
    assert m.member_key not in fresh.previewed_hashes


async def test_csv_semicolon_chinese_quoted(env, tmp_path):
    """场景 6：分号分隔 + gb18030 + 带引号字段；显式编码读取、回退披露。"""
    raw = "名称;备注;纬度;经度\r\n\"北京,首都\";\"含;分号\";39.9;116.4\r\n\"上海\";\"引号\"\"内嵌\";31.2;121.5\r\n"
    csv = raw.encode("gb18030")
    state = await env.create_batch(SESSION, "user:t", [("cn.csv", csv)])
    m = state.members[0]
    assert m.hints.get("delimiter") in (";",)
    await env.save_plan(
        state.batch_id, SESSION,
        {m.member_key: {
            "mode": "points", "x_column": "经度", "y_column": "纬度",
            "encoding": "gb18030", "delimiter": ";",
            "source_crs_declared": "EPSG:4326",
        }},
        selected=[m.member_key],
    )
    pv = await env.preview_members(state.batch_id, SESSION, [m.member_key], row_limit=10)
    p = pv["previews"][0]
    assert p["encoding_used"] == "gb18030"
    assert p["delimiter_used"] == ";"
    assert p["sample_rows"][0]["名称"] == "北京,首都"
    assert p["sample_rows"][0]["备注"] == "含;分号"
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out["receipts"][m.member_key]
    assert r["status"] == "completed" and r["output_rows"] == 2


async def test_leading_zeros_and_long_int(env, tmp_path):
    """场景 7：行政代码前导零与长整型 ID 保真（默认 text 透传；显式 number 也不截断）。"""
    big_id = 9007199254740993  # 2^53+1，JS Number 会丢 1
    csv = (
        "adm_code,big_id,name\n"
        f"00123,{big_id},甲\n"
        f"00456,{big_id + 1},乙\n"
    ).encode("utf-8")
    state = await env.create_batch(SESSION, "user:t", [("codes.csv", csv)])
    m = state.members[0]
    # 纯表导入
    await env.save_plan(state.batch_id, SESSION, {m.member_key: {"mode": "table"}}, selected=[m.member_key])
    pv = await env.preview_members(state.batch_id, SESSION, [m.member_key], row_limit=10)
    rows = pv["previews"][0]["sample_rows"]
    assert rows[0]["adm_code"] == "00123"
    assert rows[0]["big_id"] == str(big_id)
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out["receipts"][m.member_key]
    assert r["status"] == "completed"
    d = await session_data_manager.get(SESSION, r["output_ref"])
    tbl = d["table"]
    code_col = next(c for c in tbl["columns"] if c["name"] == "adm_code")
    assert code_col["type"] == "text"
    assert tbl["rows"][0][tbl["columns"].index(code_col)] == "00123"
    # 显式 number：>2^53 整数保真为字符串（不经 JS Number 截断）
    await env.save_plan(
        state.batch_id, SESSION,
        {m.member_key: {"mode": "table", "field_types": {"adm_code": "text", "big_id": "number"}}},
    )
    out2 = await env.start_commit(state.batch_id, SESSION, background=False)
    r2 = out2["receipts"][m.member_key]
    assert r2["status"] == "completed"
    d2 = await session_data_manager.get(SESSION, r2["output_ref"])
    tbl2 = d2["table"]
    big_col = next(c for c in tbl2["columns"] if c["name"] == "big_id")
    val = tbl2["rows"][0][tbl2["columns"].index(big_col)]
    assert val == str(big_id)   # 字符串形态保真


async def test_wkt_column_mixed_and_invalid(env, tmp_path):
    """场景 8：WKT 列含点、线、一个非法值 → 族披露、非法行可定位、不静默丢。"""
    csv = (
        "id,wkt\n"
        '1,"POINT (116.3 39.8)"\n'
        '2,"LINESTRING (116.3 39.8, 116.4 39.9)"\n'
        '3,"NOT_A_WKT"\n'
        '4,"POINT (116.5 39.95)"\n'
    ).encode("utf-8")
    state = await env.create_batch(SESSION, "user:t", [("wkt.csv", csv)])
    m = state.members[0]
    await env.save_plan(
        state.batch_id, SESSION,
        {m.member_key: {"mode": "wkt", "wkt_column": "wkt", "source_crs_declared": "EPSG:4326"}},
        selected=[m.member_key],
    )
    pv = await env.preview_members(state.batch_id, SESSION, [m.member_key], row_limit=10)
    p = pv["previews"][0]
    assert p["effective_mode"] == "wkt"
    assert p["geometry"]["family"] == "Mixed"
    assert p["rejected_rows"] == 1
    assert p["rejects_inline"][0]["row"] == 3
    assert p["rejects_inline"][0]["reason"] == "invalid_wkt"
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out["receipts"][m.member_key]
    assert r["status"] == "completed"
    assert r["output_rows"] == 3 and r["rejected_rows"] == 1
    assert r["geometry_type"] == "Mixed"


async def test_plain_stats_table_no_geometry(env, tmp_path):
    """场景 9：普通统计表 → 标准表格产物，不伪造 geometry/bbox。"""
    csv = make_csv_bytes(
        [["华东", 120, 3.5], ["华南", 98, 2.1]],
        ["区域", "销量", "增速"],
    )
    state = await env.create_batch(SESSION, "user:t", [("stats.csv", csv)])
    m = state.members[0]
    await env.save_plan(state.batch_id, SESSION, {m.member_key: {}}, selected=[m.member_key])
    pv = await env.preview_members(state.batch_id, SESSION, [m.member_key], row_limit=10)
    p = pv["previews"][0]
    assert p["effective_mode"] == "table"
    assert p["crs"]["confirmed"] is False and p["crs"]["crs_source"] == "unknown"
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out["receipts"][m.member_key]
    assert r["status"] == "completed"
    assert r["output_kind"] == "table"
    assert r["output_ref"].startswith("ref:table-")
    assert r["geometry_type"] is None
    d = await session_data_manager.get(SESSION, r["output_ref"])
    assert d["table"]["columns"][0]["name"] == "区域"
    assert len(d["table"]["rows"]) == 2


async def test_xlsx_sheets_empty_and_formula(env, tmp_path):
    """场景 10：多 sheet / 空 sheet / 公式单元格（无缓存值按空处理并披露）。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws1 = wb.active
    ws1.title = "数据"
    ws1.append(["名称", "数值", "公式列"])
    ws1.append(["甲", 10, "=B2*2"])       # openpyxl 不计算 → 无缓存值
    ws1.append(["乙", 20, "=B3*2"])
    ws2 = wb.create_sheet("空表")
    ws3 = wb.create_sheet("汇总")
    ws3.append(["k", "v"])
    ws3.append(["x", 1])
    xlsx_path = tmp_path / "book.xlsx"
    wb.save(xlsx_path)

    state = await env.create_batch(SESSION, "user:t", [("book.xlsx", xlsx_path.read_bytes())])
    members = {m.display_name: m for m in state.members}
    assert set(members) == {"book.xlsx :: 数据", "book.xlsx :: 空表", "book.xlsx :: 汇总"}
    assert members["book.xlsx :: 空表"].unsupported_reason == "empty_sheet"
    assert any("公式单元格" in n and "无缓存值" in n for n in state.notes)

    # 只选「数据」sheet（不选空表/汇总）
    key = members["book.xlsx :: 数据"].member_key
    await env.save_plan(state.batch_id, SESSION, {key: {"mode": "table"}}, selected=[key])
    pv = await env.preview_members(state.batch_id, SESSION, [key], row_limit=10)
    p = pv["previews"][0]
    assert p["columns"] == ["名称", "数值", "公式列"]
    assert p["sample_rows"][0]["名称"] == "甲"
    assert p["sample_rows"][0]["公式列"] == ""     # 无缓存值 → 空且已披露
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out["receipts"][key]
    assert r["status"] == "completed" and r["output_rows"] == 2
    assert set(out["receipts"]) == {key}           # 其他 sheet 无产物
