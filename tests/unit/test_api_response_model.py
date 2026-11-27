"""ApiResponse 字段/方法同名回归（P2-app_models-00）。

pydantic v2 会把与字段同名的类方法当作字段默认值并把它从类命名空间删除：
修复前 ``success`` 字段默认值变成 bound method，``ApiResponse.success()``
不可达，且 ``ApiResponse(data=...)`` 的序列化直接失败。别名改为
``ok_response()`` 后字段恢复布尔默认值。
"""
import json


def test_success_field_default_is_bool_and_serializes():
    from app.models.api_response import ApiResponse

    assert ApiResponse.model_fields["success"].default is True

    resp = ApiResponse(data={"a": 1})
    assert resp.success is True
    assert resp.model_dump()["success"] is True
    assert json.loads(resp.model_dump_json())["success"] is True


def test_success_schema_keeps_default():
    from app.models.api_response import ApiResponse

    prop = ApiResponse.model_json_schema()["properties"]["success"]
    assert prop.get("default") is True


def test_ok_response_alias_is_reachable():
    from app.models.api_response import ApiResponse

    resp = ApiResponse.ok_response(data={"a": 1})
    assert resp.success is True
    assert resp.code == "SUCCESS"
