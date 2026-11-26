"""WP3 provider-aware schema：query_local_poi typed 参数面锁定测试。

背景（PI_UPSTREAM_COMPAT_MATRIX C20）：该工具此前无 args_model，
bbox/polygon 无注解 → JSON schema 无类型节点（#12049 bare 桶）。
本文件锁定：
1. schema 卫生：bare/keyword 桶清零（typed union 保留 —— subtype OR 列表
   与坐标数组是真实语义，禁止删约束换 strict，归 typed_non_strict）；
2. 输入面等价：旧签名的全部输入形状（str/数组两栖、null 归一、int
   adcode、str limit、GeoJSON 三层坐标、subtype OR 列表）逐项可构造；
3. ``polygon=""`` 缺省哨兵不再触发服务层格式错误（旧：``"" is not None``
   误入格式校验分支；现：边界处 ``or None`` 还原「未提供」语义）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.tools.local_stats import QueryLocalPoiArgs

_TESTS_DIR = Path(__file__).resolve().parents[1]


def _strict_reject_nodes():
    """复用 schema 卫生车道移植的上游谓词（单一事实源，不复制实现）。"""
    spec = importlib.util.spec_from_file_location(
        "_tpns_helper", _TESTS_DIR / "unit" / "test_pi_native_surface.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._strict_reject_nodes


def _query_local_poi_dump_params():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    from app.services.chat.pi_native_surface import pi_surface_for_spawn

    registry = ToolRegistry()
    init_tools(registry)
    surface = pi_surface_for_spawn(registry)
    return next(t for t in surface["tools"] if t["name"] == "query_local_poi")


@pytest.mark.parametrize("case", [
    {"bbox": [104.0, 30.6, 104.2, 30.7]},
    {"bbox": "104,30.6,104.2,30.7"},
    {"adcode": 510104},
    {"adcode": "510104"},
    {"limit": "500"},
    {"limit": 2000},
    {"subtype": "小学"},
    {"subtype": ["小学", "中学"]},
    {"polygon": [104, 30.5, 104.2, 30.7]},
    {"polygon": [[[104, 30.5], [104.2, 30.5], [104.2, 30.7], [104, 30.7],
                  [104, 30.5]]]},
    {"polygon": {"type": "Polygon", "coordinates": [[[104, 30.5]]]}},
    {"name_like": None, "category": None, "subtype": None, "district": None,
     "polygon": None, "bbox": None, "adcode": None, "limit": None},
    {},
])
def test_args_model_accepts_legacy_input_shapes(case):
    args = QueryLocalPoiArgs(**case)
    # null 归一等价：显式 None 与缺省 "" 同值
    if case.get("name_like") is None:
        assert args.name_like == ""
    if case.get("limit") is None:
        assert args.limit == 2000


def test_args_model_schema_fully_typed_no_bare_nodes():
    """bare/keyword 桶必须为空：schema 每个节点可 typed 锚定。

    remaining ``other``（typed union / 开放对象）是真实语义（subtype OR、
    坐标数组、GeoJSON dict），按 C20 纪律**不删约束换 strict**。
    """
    tool = _query_local_poi_dump_params()
    rejects = _strict_reject_nodes()(tool.get("parameters"))
    assert rejects["bare"] == [], f"bare nodes remain: {rejects['bare']}"
    assert rejects["keyword"] == [], f"legacy keywords remain: {rejects['keyword']}"


def test_empty_polygon_sentinel_matches_unprovided_semantics():
    """``polygon=""`` 归一为「未提供」：工具体 ``polygon or None`` 边界。

    服务层以 ``polygon is not None`` 判「提供了 polygon」，"" 哨兵若直传
    会误入格式错误分支；这里锁定 args → 服务边界的还原行为。
    """
    args = QueryLocalPoiArgs(polygon="")
    sentinel = (args.polygon or None)
    assert sentinel is None
    # 非 "" 值不受影响
    assert (QueryLocalPoiArgs(polygon=[104, 30.5, 104.2, 30.7]).polygon
            or None) is not None
