"""is_suspicious_result 单测。

unified-tool-dispatch 票据 03：原 dispatch_tool 的行为测试已由
test_tool_dispatch_service.py 在服务接口层覆盖（判别式结果 + geojson_ref 回归锁），
此处按 DEEPENING「replace, don't layer」原则删除被取代形态的浅测试。
仅保留 is_suspicious_result 纯函数枚举——该函数存活于 chat/dispatcher.py。
"""
import pytest

from app.services.chat.dispatcher import is_suspicious_result


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
        ],
    )
    def test_each_shape(self, result, expected):
        assert is_suspicious_result(result) is expected
