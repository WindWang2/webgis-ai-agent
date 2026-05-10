"""工具层共享工具函数测试"""
import pytest
from app.tools._utils import parse_bbox, validate_data_path, std_error_response, asset_href


class TestParseBBox:
    def test_valid_bracket_string(self):
        result = parse_bbox("[116.2, 39.7, 116.6, 40.1]")
        assert result == [116.2, 39.7, 116.6, 40.1]

    def test_valid_parenthesis_string(self):
        result = parse_bbox("(116.2, 39.7, 116.6, 40.1)")
        assert result == [116.2, 39.7, 116.6, 40.1]

    def test_valid_plain_string(self):
        result = parse_bbox("116.2, 39.7, 116.6, 40.1")
        assert result == [116.2, 39.7, 116.6, 40.1]

    def test_too_few_values_raises(self):
        with pytest.raises(ValueError, match="需要 4 个值"):
            parse_bbox("116.2, 39.7")

    def test_too_many_values_raises(self):
        with pytest.raises(ValueError, match="需要 4 个值"):
            parse_bbox("1, 2, 3, 4, 5")

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="格式错误"):
            parse_bbox("not-a-number")

    def test_west_gte_east_raises(self):
        with pytest.raises(ValueError, match="经度范围无效"):
            parse_bbox("120.0, 30.0, 110.0, 40.0")

    def test_south_gte_north_raises(self):
        with pytest.raises(ValueError, match="纬度范围无效"):
            parse_bbox("110.0, 40.0, 120.0, 30.0")

    def test_longitude_out_of_range_raises(self):
        with pytest.raises(ValueError, match="经度超出有效范围"):
            parse_bbox("-200.0, 30.0, 120.0, 40.0")

    def test_latitude_out_of_range_raises(self):
        with pytest.raises(ValueError, match="纬度超出有效范围"):
            parse_bbox("110.0, -100.0, 120.0, 40.0")


class TestValidateDataPath:
    def test_valid_relative_path(self):
        result = validate_data_path("uploads/test.geojson", data_dir="./data")
        assert result.endswith("uploads/test.geojson")

    def test_traversal_with_dotdot_raises(self):
        with pytest.raises(ValueError, match="非法路径"):
            validate_data_path("../../etc/passwd", data_dir="./data")

    def test_absolute_path_outside_raises(self):
        with pytest.raises(ValueError, match="非法路径"):
            validate_data_path("/etc/passwd", data_dir="./data")

    def test_path_within_data_dir_ok(self):
        import os
        result = validate_data_path("reports/output.html", data_dir="./data")
        data_dir_abs = os.path.abspath("./data")
        assert result.startswith(data_dir_abs)


class TestStdErrorResponse:
    def test_basic_error(self):
        resp = std_error_response("something broke")
        assert resp["success"] is False
        assert resp["code"] == "TOOL_ERROR"
        assert resp["message"] == "something broke"
        assert resp["data"] is None

    def test_custom_code(self):
        resp = std_error_response("not found", code="NOT_FOUND")
        assert resp["code"] == "NOT_FOUND"

    def test_with_error_type(self):
        resp = std_error_response("bad", error_type="ValueError")
        assert resp["error_type"] == "ValueError"


class TestAssetHref:
    def test_dict_asset(self):
        assets = {"red": {"href": "http://example.com/red.tif"}}
        assert asset_href(assets, "red") == "http://example.com/red.tif"

    def test_missing_asset(self):
        assert asset_href({}, "nir") == ""

    def test_alias_fallback_b04(self):
        assets = {"B04": {"href": "http://example.com/b04.tif"}}
        assert asset_href(assets, "red") == "http://example.com/b04.tif"

    def test_alias_fallback_b08(self):
        assets = {"B08": {"href": "http://example.com/b08.tif"}}
        assert asset_href(assets, "nir") == "http://example.com/b08.tif"

    def test_pystac_asset_object(self):
        class FakeAsset:
            href = "http://example.com/fake.tif"
        assets = {"visual": FakeAsset()}
        assert asset_href(assets, "visual") == "http://example.com/fake.tif"
