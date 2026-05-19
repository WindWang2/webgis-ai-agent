"""A5 PostGISAdapter：SQL 注入回归测试。

不打真实 PostGIS — 只验证标识符白名单 + 参数绑定的安全契约。
方法：让 PostGISAdapter.query 在尝试访问 DB 前就因校验失败抛 ValueError；
合法输入则通过校验、走到 db_session 步骤被我们 mock 掉，断言 SQL 文本
不含未参数化的危险字符。
"""
import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# data_fetcher 包 __init__ 会拉 OSSAdapter（需要 oss2 可选依赖），测试环境不一定有。
# 直接按文件路径加载 postgis_adapter，绕开包导入链。
_HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_pg_path = os.path.join(
    _HERE, "app", "services", "data_fetcher", "adapters", "postgis_adapter.py"
)
# 先加载 base.py（postgis_adapter 引用它）
_base_path = os.path.join(_HERE, "app", "services", "data_fetcher", "adapters", "base.py")
_base_spec = importlib.util.spec_from_file_location("_pg_base_for_test", _base_path)
_base_mod = importlib.util.module_from_spec(_base_spec)
sys.modules["_pg_base_for_test"] = _base_mod
_base_spec.loader.exec_module(_base_mod)

# 用绝对路径加载，相对导入会失败 — patch 一下 postgis_adapter 源码里的 from .base 为我们刚加载的 base
_pg_src = open(_pg_path, encoding="utf-8").read().replace("from .base import", "from _pg_base_for_test import")
_pg_mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("postgis_adapter_test", loader=None))
exec(compile(_pg_src, _pg_path, "exec"), _pg_mod.__dict__)
PostGISAdapter = _pg_mod.PostGISAdapter
_validate_ident = _pg_mod._validate_ident
_validate_properties = _pg_mod._validate_properties
_validate_table = _pg_mod._validate_table


class TestIdentValidation:
    def test_accepts_plain_identifier(self):
        assert _validate_ident("road_segments") == "road_segments"

    @pytest.mark.parametrize("bad", [
        "",                       # 空
        "1bad",                  # 数字开头
        "tab; DROP TABLE x",     # 含分号
        "tab le",                # 含空格
        "tab--",                 # 含注释
        "\"escaped\"",          # 含引号
        None,                    # 非字符串
        "tab'",
    ])
    def test_rejects_injection_attempts(self, bad):
        with pytest.raises(ValueError):
            _validate_ident(bad)  # type: ignore[arg-type]


class TestTableValidation:
    def test_accepts_schema_qualified(self):
        assert _validate_table("public.parks") == "public.parks"

    def test_rejects_three_part(self):
        with pytest.raises(ValueError):
            _validate_table("a.b.c")

    def test_rejects_injection(self):
        with pytest.raises(ValueError):
            _validate_table("public.parks; DROP TABLE users")


class TestPropertiesValidation:
    def test_star_returns_none(self):
        assert _validate_properties("*") is None

    def test_empty_returns_none(self):
        assert _validate_properties("") is None

    def test_splits_clean_list(self):
        assert _validate_properties("name, area") == ["name", "area"]

    def test_rejects_injection_in_list(self):
        with pytest.raises(ValueError):
            _validate_properties("name, area; DROP TABLE users")


class TestQuerySafety:
    def test_query_rejects_bad_table(self):
        a = PostGISAdapter()
        with pytest.raises(ValueError, match="table"):
            a.query({"table": "users; DROP TABLE x"})

    def test_query_rejects_bad_geometry_column(self):
        a = PostGISAdapter()
        with pytest.raises(ValueError, match="geometry_column"):
            a.query({"table": "parks", "geometry_column": "geom; --"})

    def test_query_rejects_bad_filter_eq_column(self):
        a = PostGISAdapter()
        with pytest.raises(ValueError, match="filter column"):
            a.query({"table": "parks", "filter_eq": {"name; DROP": "x"}})

    def test_query_binds_bbox_as_parameters(self):
        """合法输入路径：拦截 db_session，断言 bbox 走绑定参数不进 SQL 文本。"""
        a = PostGISAdapter()
        captured = {}

        class FakeResult:
            def scalar_one_or_none(self):
                return None

        class FakeDb:
            def execute(self, sql, params=None):
                captured["sql"] = str(sql)
                captured["params"] = params
                return FakeResult()

        class FakeCtx:
            def __enter__(self_inner):
                return FakeDb()

            def __exit__(self_inner, *a):
                return False

        # 直接在 _pg_mod 模块上替换 db_session（patch 在 oss2 缺失时无法解析包路径）
        original = _pg_mod.db_session
        _pg_mod.db_session = lambda: FakeCtx()
        try:
            a.query({
                "table": "parks",
                "bbox": [116.0, 39.0, 117.0, 40.0],
                "filter_eq": {"name": "Bob' OR '1'='1"},  # 试图 SQL 注入
            })
        finally:
            _pg_mod.db_session = original

        sql_text = captured["sql"]
        params = captured["params"]
        # bbox 数值不应出现在 SQL 文本里
        assert "116.0" not in sql_text
        assert "117.0" not in sql_text
        # 但应通过命名参数注入
        assert params["minx"] == 116.0
        assert params["maxx"] == 117.0
        # filter_eq 的值也走绑定参数 — 注入串原样进入 params，被 SQL 驱动安全转义
        assert any(v == "Bob' OR '1'='1" for v in params.values())
        # SQL 文本里不应出现注入字符串原文
        assert "'1'='1" not in sql_text
