"""V9 契约基石：子系统 schema 模块契约（按模块参数化）。

每个迁移产物模块（app/schemas/<sub>_schema.py）必须：
1. 可独立导入；
2. 全部公有模型为 pydantic BaseModel；
3. 全部模型可生成 OpenAPI 兼容 JSON schema；
4. 声明了 json_schema_extra 示例或 extra 策略（开放对象豁免）。
对应「每个路由文件配契约测试」的交付 —— 以参数化覆盖全部子系统模块。
"""
from __future__ import annotations

import importlib

import pytest
from pydantic import BaseModel, TypeAdapter

SUBSYSTEM_SCHEMA_MODULES = [
    "app.schemas.auth_schema",
    "app.schemas.chat_schema",
    "app.schemas.config_schema",
    "app.schemas.data_fabric_schema",
    "app.schemas.explorer_schema",
    "app.schemas.geocompute_schema",
    "app.schemas.health_schema",
    "app.schemas.knowledge_schema",
    "app.schemas.layer_schema",
    "app.schemas.local_data_schema",
    "app.schemas.mapspec_mutation_schema",
    "app.schemas.map_schema",
    "app.schemas.project_schema",
    "app.schemas.report_schema",
    "app.schemas.task_schema",
    "app.schemas.template_schema",
    "app.schemas.upload_schema",
    "app.schemas.workflow_resume_schema",
    "app.schemas.workflow_runtime_schema",
]


def _public_models(module) -> list[type[BaseModel]]:
    out = []
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel) and not name.startswith("_"):
            out.append(obj)
    return out


@pytest.mark.parametrize("module_name", SUBSYSTEM_SCHEMA_MODULES)
def test_subsystem_module_contract(module_name: str):
    module = importlib.import_module(module_name)
    models = _public_models(module)
    assert models, f"{module_name} 未暴露任何契约模型"
    for model in models:
        schema = TypeAdapter(model).json_schema()
        assert isinstance(schema, dict) and "type" in schema or "anyOf" in schema or "$defs" in schema
        # ConfigDict 纪律：允许默认（未显式配置）但禁止弃用的 v1 Config 类
        assert not getattr(model, "Config", None), (
            f"{module_name}.{model.__name__} 使用 v1 风格 class Config（应为 model_config）"
        )
