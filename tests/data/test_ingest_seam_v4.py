"""Wave 3 Ingest V4 —— 生产缝测试（审计 03 §7 items 1/3/4/5/7）。

覆盖：
- 管线 materialize 步失败 → 标准错误形状 + ref 补偿回滚（R7）；
- 编码回退 utf-8 → gb18030（R3）+ 残余失败 → ParseError；
- CRS 诚实：GeoJSON RFC 7946 缺省披露 / CSV 声明与未声明（R4）；
- ingest_dataset 工具：登记 ref + 有界 profile/quality + repairable 计数（R1）；
- 修复提案映射：诊断码 → REMEDIATION_OPS 词表（R5，plan-only）。
"""
import json

import pytest

from app.services.data_ingest.pipeline import (
    get_ingest_pipeline,
    reset_ingest_pipeline,
)
from app.services.data_parser import ParseError, parse_vector
from app.services.session_data import session_data_manager


@pytest.fixture(autouse=True)
def _reset():
    reset_ingest_pipeline()
    yield
    reset_ingest_pipeline()


def _fc(n=3, value="v", crs=None):
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.1, 39.0]},
             "properties": {"v": f"{value}{i}"}}
            for i in range(n)
        ],
    }
    if crs:
        fc = {"type": "FeatureCollection", "crs": crs, "features": fc["features"]}
    return fc


def _write_geojson(path, fc):
    path.write_text(json.dumps(fc), encoding="utf-8")


_GBK_CSV = "name,lng,lat\n北京,116.40,39.90\n上海,121.47,31.23\n".encode("gb18030")
_UTF8_CSV = "name,lng,lat\nBeijing,116.40,39.90\nShanghai,121.47,31.23\n".encode("utf-8")


# ── Item 1：materialize 步失败 → 错误形状 + 补偿回滚（R7）────────────────

class TestMaterializeFailure:
    async def test_materialize_failure_rolls_back_ref(self, monkeypatch):
        sid = "ingest-v4-matfail"
        from app.services import artifact_registry as ar

        async def failing_update(*a, **kw):
            raise RuntimeError("metadata write failed")

        monkeypatch.setattr(ar, "update_record_metadata", failing_update)
        result = await get_ingest_pipeline().ingest(sid, _fc(), materialize=True)
        assert not result.ok
        assert result.error_code == "MATERIALIZE_FAILED_ROLLED_BACK"
        assert "materialize" in result.error
        # steps_completed 诚实：materialize 没有完成
        assert "materialize" not in result.steps_completed
        assert "register" in result.steps_completed
        # ref 已补偿删除（与 register 失败同一纪律）
        exists = await session_data_manager.ref_exists(sid, result.ref_id)
        assert not exists

    async def test_materialize_success_still_ok(self):
        sid = "ingest-v4-matok"
        result = await get_ingest_pipeline().ingest(sid, _fc(), materialize=True)
        assert result.ok
        assert result.steps_completed[-1] == "materialize"


# ── Item 3：编码回退（R3）───────────────────────────────────────────────

class TestEncodingFallback:
    def test_gbk_csv_falls_back_with_disclosure(self, tmp_path):
        p = tmp_path / "points.csv"
        p.write_bytes(_GBK_CSV)
        meta = parse_vector(p, tmp_path, "enc-1")
        assert meta["feature_count"] == 2
        assert meta["encoding"] == "gb18030"
        assert meta["encoding_fallback"] is True

    def test_utf8_csv_byte_identical_behavior(self, tmp_path):
        p = tmp_path / "points.csv"
        p.write_bytes(_UTF8_CSV)
        meta = parse_vector(p, tmp_path, "enc-2")
        assert meta["feature_count"] == 2
        # utf-8 文件行为不变：无编码披露（没有回退发生）
        assert "encoding" not in meta
        assert "encoding_fallback" not in meta

    def test_undecodable_csv_raises_parse_error(self, tmp_path):
        p = tmp_path / "points.csv"
        # 0x80 不是合法的 utf-8 续字节，也不是合法的 gb18030 起始字节
        p.write_bytes(b"\x80\x80\x80\x80,lng,lat\n" + b"\x80" * 8)
        with pytest.raises(ParseError) as exc_info:
            parse_vector(p, tmp_path, "enc-3")
        # 修复建议文本来自 quality.py ENCODING_ISSUES（单一事实源）
        assert "gb18030" in str(exc_info.value) or "编码" in str(exc_info.value)


# ── Item 4：CRS 诚实（R4）───────────────────────────────────────────────

class TestCrsHonesty:
    def test_geojson_without_crs_discloses_rfc7946_default(self, tmp_path):
        p = tmp_path / "no-crs.geojson"
        _write_geojson(p, _fc(1))
        meta = parse_vector(p, tmp_path, "crs-1")
        # 行为不变：仍是 4326；新增披露
        assert meta["crs"] == "EPSG:4326"
        assert meta["crs_source"] == "rfc7946_default"
        assert meta["crs_assumed"] == "EPSG:4326"

    def test_vector_with_crs_marks_source_confirmed(self, tmp_path):
        # GPKG 真实携带 CRS 元数据 → crs_source=source（确认，非缺省）。
        # 注：GDAL 写 GeoJSON 按 RFC 7946 不落 crs 成员 —— 4326 GeoJSON 与
        # 无声明 GeoJSON 在文件层面不可区分，按规范缺省披露是正确语义。
        import geopandas as gpd
        from shapely.geometry import Point

        gdf = gpd.GeoDataFrame({"v": [1]}, geometry=[Point(116.0, 39.0)], crs="EPSG:4326")
        p = tmp_path / "with-crs.gpkg"
        gdf.to_file(p, driver="GPKG")
        meta = parse_vector(p, tmp_path, "crs-2")
        assert meta["crs"] == "EPSG:4326"
        assert meta["crs_source"] == "source"
        assert meta.get("crs_assumed") is None

    def test_csv_declared_crs_respected_and_converted(self, tmp_path):
        # 声明 EPSG:3857（米制）→ 转 4326，original_crs 保留
        p = tmp_path / "proj.csv"
        p.write_text(
            "name,lng,lat\nA,12957712.31,4852323.85\nB,12958000.00,4852800.00\n",
            encoding="utf-8",
        )
        meta = parse_vector(p, tmp_path, "crs-3", crs="EPSG:3857")
        assert meta["crs_source"] == "declared"
        assert meta["crs"] == "EPSG:4326"
        assert meta["original_crs"] == "EPSG:3857"
        assert meta.get("crs_assumed") is None
        # 坐标真的被转换了（出界米制值 → 合法经纬度）
        assert -180 <= meta["bbox"][0] <= 180

    def test_csv_without_crs_stays_unknown_honest(self, tmp_path):
        p = tmp_path / "plain.csv"
        p.write_bytes(_UTF8_CSV)
        meta = parse_vector(p, tmp_path, "crs-4")
        # 不再谎称 confirmed 4326
        assert meta["crs"] is None
        assert meta["crs_source"] == "assumed"
        assert meta["crs_assumed"] == "EPSG:4326"
        assert any("CRS_MISSING" in w for w in meta.get("warnings", []))


# ── Item 5b：ingest_dataset 工具（R1）───────────────────────────────────

def _make_registry():
    from app.tools.ingest_tools import register_ingest_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_ingest_tools(registry)
    return registry._tools["ingest_dataset"]


class TestIngestDatasetTool:
    async def test_happy_path_registers_ref_with_bounded_output(self):
        ingest = _make_registry()
        sid = "tool-ingest-ok"
        out = await ingest(data=_fc(4), declared_crs="EPSG:4326", name="工具摄入", session_id=sid)
        assert out["success"] is True
        assert out["ref_id"].startswith("ref:ingest-")
        assert out["duplicate"] is False
        assert out["profile"]["row_count"] == 4
        assert out["quality"]["quality_status"] in ("valid", "warning")
        assert isinstance(out["repairable_issue_count"], int)
        rec = await session_data_manager.get_ref_descriptor(sid, out["ref_id"])
        assert rec is not None

    async def test_dedup_reuses_ref(self):
        ingest = _make_registry()
        sid = "tool-ingest-dedup"
        first = await ingest(data=_fc(2), session_id=sid)
        second = await ingest(data=_fc(2), session_id=sid)
        assert first["success"] and second["success"]
        assert second["duplicate"] is True
        assert second["ref_id"] == first["ref_id"]

    async def test_no_session_honestly_fails(self):
        ingest = _make_registry()
        out = await ingest(data=_fc(), session_id=None)
        assert out["success"] is False
        assert out["code"] == "NO_SESSION"

    async def test_non_fc_payload_rejected(self):
        ingest = _make_registry()
        out = await ingest(data={"type": "chart_spec", "series": [1]}, session_id="tool-x")
        assert out["success"] is False
        assert out["code"] == "UNSUPPORTED_SHAPE"

    async def test_missing_crs_surfaces_repairable_count(self):
        ingest = _make_registry()
        out = await ingest(data=_fc(2), session_id="tool-crs")  # 未声明 CRS
        assert out["success"] is True
        codes = [i["code"] for i in out["quality"]["issues"]]
        assert "crs_missing" in codes
        assert out["repairable_issue_count"] >= 1

    async def test_propose_repairs_flag_returns_plan_only(self):
        ingest = _make_registry()
        out = await ingest(data=_fc(2), propose_repairs=True, session_id="tool-plan")
        proposals = out.get("repair_proposals")
        assert isinstance(proposals, list)
        by_code = {p["reason_code"]: p for p in proposals}
        assert "crs_missing" in by_code
        p = by_code["crs_missing"]
        assert p["operation"] == "reproject"
        # caveat：CRS 未知 → 重投影不可自动应用（需要先声明源 CRS）
        assert p["auto_applicable"] is False
        assert p["params"].get("requires_declared_crs") is True


# ── Item 7：修复提案映射（R5，plan-only）────────────────────────────────

class TestRepairProposalMapping:
    def _codes(self, proposals):
        return {p.reason_code for p in proposals}

    def test_crs_missing_maps_to_reproject_with_declared_crs_caveat(self):
        from app.services.data_ingest.repair_planning import propose_repairs_for_issue_codes

        [p] = propose_repairs_for_issue_codes(["crs_missing"])
        assert p.operation == "reproject"
        assert p.auto_applicable is False
        assert p.params.get("requires_declared_crs") is True
        assert p.backing  # 引用 REMEDIATION_OP_BACKING 实现背书

    def test_duplicate_maps_to_deduplicate(self):
        from app.services.data_ingest.repair_planning import propose_repairs_for_issue_codes

        for code in ("duplicate_geometries", "duplicate_rows"):
            [p] = propose_repairs_for_issue_codes([code])
            assert p.operation == "repair_geometry"
            assert p.params.get("mode") == "deduplicate"
            assert any("deduplicate" in b for b in p.backing)

    def test_geometry_and_raster_codes_map(self):
        from app.services.data_ingest.repair_planning import propose_repairs_for_issue_codes

        mapping = {
            "invalid_geometry": "repair_geometry",
            "self_intersection": "repair_geometry",
            "null_heavy_field": "filter_null",
            "nodata_saturation": "filter_nodata",
            "resolution_mismatch": "resample",
            "encoding_issues": "normalize",
        }
        for code, op in mapping.items():
            [p] = propose_repairs_for_issue_codes([code])
            assert p.operation == op, code

    def test_field_targeting(self):
        from app.services.data_ingest.repair_planning import propose_repairs_for_issue_codes

        [p] = propose_repairs_for_issue_codes(
            ["null_heavy_field"], fields={"null_heavy_field": "population"}
        )
        assert p.target == "population"
        assert p.params.get("target_field") == "population"

    def test_unmapped_codes_propose_nothing(self):
        from app.services.data_ingest.repair_planning import propose_repairs_for_issue_codes

        out = propose_repairs_for_issue_codes(
            ["empty_payload", "extent_mismatch", "temporal_gaps", "schema_truncated"]
        )
        assert out == []

    def test_all_operations_within_vocabulary(self):
        # 单一词表源：所有映射到的 operation ⊆ REMEDIATION_OPS
        from app.services.data_ingest.repair_planning import (
            REPAIRABLE_ISSUE_CODES,
            propose_repairs_for_issue_codes,
        )
        from app.services.gis_harness.data_qualification import REMEDIATION_OPS

        proposals = propose_repairs_for_issue_codes(sorted(REPAIRABLE_ISSUE_CODES))
        assert proposals
        assert all(p.operation in REMEDIATION_OPS for p in proposals)

    def test_quality_report_object_path(self):
        from app.lib.data.profile import DatasetProfileV3, ProfileQuality, VectorProfileData
        from app.lib.data.quality import run_quality_checks
        from app.services.data_ingest.repair_planning import propose_repairs

        vp = VectorProfileData(row_count=2, scanned_rows=2)
        profile = DatasetProfileV3(
            target_ref="ref:x", category="vector", crs="", vector=vp,
            profile_quality=ProfileQuality.COMPLETE,
        )
        report = run_quality_checks(profile)
        proposals = propose_repairs(report)
        assert isinstance(proposals, list)
        # CRS 缺失 → 可提案 reproject（需声明 CRS 的 caveat）
        assert "crs_missing" in self._codes(proposals)
        assert "empty_payload" not in self._codes(proposals)
        # 有界输出 + plan-only 结构
        for p in proposals:
            d = p.to_bounded_dict()
            assert set(d) >= {"operation", "target", "params", "reason_code",
                              "auto_applicable", "confidence"}
