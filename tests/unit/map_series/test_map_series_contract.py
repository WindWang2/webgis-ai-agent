"""map_series.v1 契约测试 —— 负例（预算/重复/未知词表/泄漏/extra）+ 正例。"""
from __future__ import annotations

from app.services.map_series.contract import (
    MAP_SERIES_VERSION,
    validate_map_series_payload,
)
from tests.unit.map_series.helpers import series_payload


class TestContractPositive:
    def test_valid_payload_roundtrip(self):
        report = validate_map_series_payload(series_payload())
        assert report["ok"], report["violations"]
        assert report["series"]["series_version"] == MAP_SERIES_VERSION
        assert len(report["series"]["members"]) == 2
        assert not report["leak_findings"]

    def test_member_optional_fields_default(self):
        payload = series_payload()
        payload["members"][0]["data_bindings"] = {"population": "pop_total"}
        payload["members"][0]["locked_fields"] = ["palettes"]
        payload["members"][0]["extent_override"] = [103.0, 30.0, 104.5, 31.0]
        report = validate_map_series_payload(payload)
        assert report["ok"], report["violations"]
        member = report["series"]["members"][0]
        assert member["enabled"] is True
        assert member["extent_override"] == [103.0, 30.0, 104.5, 31.0]


class TestContractNegative:
    def test_members_over_budget(self):
        members = [
            {"member_id": f"m{i}", "title": f"图{i}", "session_id": f"s{i}"}
            for i in range(51)
        ]
        report = validate_map_series_payload(series_payload(members=members))
        assert not report["ok"]
        assert any("over budget" in v["reason"] for v in report["violations"])

    def test_duplicate_member_id(self):
        payload = series_payload()
        payload["members"].append(dict(payload["members"][0]))
        report = validate_map_series_payload(payload)
        assert not report["ok"]
        assert any("duplicate member_id" in v["reason"] for v in report["violations"])

    def test_unknown_role_rejected(self):
        payload = series_payload()
        payload["members"][0]["role"] = "hero"
        report = validate_map_series_payload(payload)
        assert not report["ok"]
        assert any("unknown member role" in v["reason"] for v in report["violations"])

    def test_unknown_locked_field_rejected(self):
        payload = series_payload()
        payload["members"][0]["locked_fields"] = ["everything"]
        report = validate_map_series_payload(payload)
        assert not report["ok"]
        assert any("unknown locked fields" in v["reason"] for v in report["violations"])

    def test_schema_extra_forbid(self):
        payload = series_payload(mystery_key=1)
        report = validate_map_series_payload(payload)
        assert not report["ok"]

    def test_member_extra_forbid(self):
        payload = series_payload()
        payload["members"][0]["rendered_png"] = "base64..."
        report = validate_map_series_payload(payload)
        assert not report["ok"]

    def test_ref_cursor_in_payload_rejected(self):
        payload = series_payload()
        payload["members"][0]["session_id"] = "ref:dataset/42"
        report = validate_map_series_payload(payload)
        assert not report["ok"]
        assert any(
            v["reason"] == "REF_CURSOR_FORBIDDEN" for v in report["violations"]
        )

    def test_inline_geojson_rejected(self):
        payload = series_payload()
        payload["members"][0]["geojson"] = {"type": "FeatureCollection"}
        report = validate_map_series_payload(payload)
        assert not report["ok"]
        assert any(
            v["reason"] == "INLINE_DATA_FORBIDDEN" for v in report["violations"]
        )

    def test_credential_field_rejected(self):
        payload = series_payload()
        payload["members"][0]["api_key"] = "sk-xxx"
        report = validate_map_series_payload(payload)
        assert not report["ok"]
        assert any(
            v["reason"] == "CREDENTIAL_FIELD_FORBIDDEN" for v in report["violations"]
        )

    def test_data_breakpoints_rejected(self):
        payload = series_payload()
        payload["members"][0]["classification_breaks"] = [0, 10, 20, 30]
        report = validate_map_series_payload(payload)
        assert not report["ok"]
        assert any(
            v["reason"] == "DATA_VALUES_FORBIDDEN" for v in report["violations"]
        )

    def test_data_binding_non_field_name_rejected(self):
        payload = series_payload()
        payload["members"][0]["data_bindings"] = {"population": "ref:cursor/9"}
        report = validate_map_series_payload(payload)
        assert not report["ok"]
        reasons = {v["reason"] for v in report["violations"]}
        assert reasons & {"DATA_BINDING_FIELD_INVALID", "REF_CURSOR_FORBIDDEN"}

    def test_bad_extent_override_rejected(self):
        payload = series_payload()
        payload["members"][0]["extent_override"] = [104.5, 31.0, 103.0, 30.0]  # w>e
        report = validate_map_series_payload(payload)
        assert not report["ok"]

    def test_unknown_dimension_axis_rejected(self):
        report = validate_map_series_payload(
            series_payload(dimension={"axis": "scale", "labels": []})
        )
        assert not report["ok"]

    def test_unknown_output_target_rejected(self):
        report = validate_map_series_payload(
            series_payload(output={"targets": ["poster"], "dpi": 300})
        )
        assert not report["ok"]

    def test_dpi_out_of_range_rejected(self):
        report = validate_map_series_payload(
            series_payload(output={"targets": ["png"], "dpi": 1200})
        )
        assert not report["ok"]

    def test_design_spec_id_required(self):
        payload = series_payload()
        payload.pop("design_spec_id")
        report = validate_map_series_payload(payload)
        assert not report["ok"]
