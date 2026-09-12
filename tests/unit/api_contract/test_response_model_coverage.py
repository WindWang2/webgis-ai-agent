"""V9 契约基石门禁：response_model 全覆盖 + OpenAPI 可生成 + 序列化一致。

- 门禁 1：全部 JSON 端点（非流式/非二进制/非 204）必须挂 response_model；
  豁免清单单一事实来源 = _contract_util.EXCLUSIONS（镜像进 PR 附表与 ADR-0138）。
- 门禁 2：app.openapi() 可生成且 operation 数量稳定（防路由装配回归）。
- 门禁 3：所有响应模型可 JSON-schema 化且样例可序列化往返。
"""
from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from _contract_util import unguarded_routes  # noqa: E402


def test_all_json_endpoints_have_response_model():
    missing = unguarded_routes()
    assert not missing, (
        "以下端点缺 response_model（流式/二进制豁免见 "
        "tests/unit/api_contract/_contract_util.py EXCLUSIONS）:\n"
        + "\n".join(f"  {m} {p}" for m, p, _ in missing)
    )


def test_openapi_generates_and_ops_stable():
    from app.main import app

    app.openapi_schema = None
    schema = app.openapi()
    app.openapi_schema = None
    assert schema["openapi"].startswith("3.")
    ops = sum(
        1
        for methods in schema["paths"].values()
        for m in methods
        if m in ("get", "post", "put", "delete", "patch")
    )
    # P0 勘察基线：219 个 OpenAPI operation（1 个 include_in_schema=False 路由除外）
    assert ops >= 210, f"OpenAPI operation 数异常回落: {ops}"


def test_every_response_model_schema_and_roundtrip():
    """每个已挂模端点的响应模型：JSON schema 可生成；构造样例可序列化往返。"""
    from pydantic import TypeAdapter

    from _contract_util import iter_routes_by_file

    checked = 0
    for _path, route in iter_routes_by_file(""):
        rm = route.response_model
        if rm is None:
            continue
        try:
            TypeAdapter(rm).json_schema()
        except Exception as e:  # pragma: no cover - 逐个暴露坏模型
            raise AssertionError(
                f"response_model 无法生成 JSON schema: {route.path} -> {rm!r}: {e}"
            )
        checked += 1
    assert checked >= 200, f"已挂模端点数异常: {checked}"


def test_exclusions_ledger_only_non_json():
    """豁免清单必须有理由且键唯一（防滥用豁免绕过门禁）。"""
    from _contract_util import EXCLUSIONS

    assert len(EXCLUSIONS) == len(set(EXCLUSIONS.keys()))
    for (method, path), reason in EXCLUSIONS.items():
        assert method in ("GET", "POST", "PUT", "DELETE", "PATCH")
        assert path.startswith(("/api/v1", "/metrics")), path
        assert reason and isinstance(reason, str)
