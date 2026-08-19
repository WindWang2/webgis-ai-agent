"""is_suspicious_result 单测。

unified-tool-dispatch 票据 04 contract：原 chat/dispatcher.py 已删除，
is_suspicious_result 收敛到 tool_dispatch_service.py 作为唯一定义。
本测试改为从该处导入。
"""
import pytest

from app.services.tool_dispatch_service import is_suspicious_result


class TestSuspicious:
    @pytest.mark.parametrize(
        "result, expected",
        [
            (None, True),
            ("", True),
            ([], True),
            ({}, True),
            ({"success": False, "code": "X"}, True),
            ({"type": "FeatureCollection", "features": []}, True),
            ({"data": []}, True),
            ({"poi_count": 0}, True),
            ({"type": "FeatureCollection", "features": [{"id": 1}]}, False),
            ({"data": ["x"]}, False),
            ({"poi_count": 5}, False),
            ({"success": True, "ref": "ref:x"}, False),
            (["x"], False),
            # 制图封装后要素体被收成 result_ref，不能再当空结果。
            (
                {
                    "type": "FeatureCollection",
                    "feature_count": 128,
                    "result_ref": "ref:geojson-schools",
                    "success": True,
                },
                False,
            ),
            (
                {
                    "type": "FeatureCollection",
                    "count": 20,
                    "result_ref": "ref:geojson-admin",
                },
                False,
            ),
        ],
    )
    def test_each_shape(self, result, expected):
        assert is_suspicious_result(result) is expected
