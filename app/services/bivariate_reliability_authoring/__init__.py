"""G41 双变量与可靠性表达作者工作台（领域服务）。

在既有 G33 ``thematic_authoring`` 作者框架、``bivariate`` 分级/色阵、
``uncertainty`` 证据契约与 ``apply_gis_mutation`` 单一写路径之上做**加性**
接线；不新建第二套 agent loop、工具注册表、MapSpec store、撤销栈或证据库。
"""
from app.services.bivariate_reliability_authoring.contract import (
    ALGORITHM_VERSION,
    PLAN_SCHEMA_VERSION,
    AuthoringPlan,
    parse_plan,
)
from app.services.bivariate_reliability_authoring.errors import (
    AuthoringError,
    error_payload,
    http_status,
)
from app.services.bivariate_reliability_authoring.service import (
    commit_expression,
    inspect_layer,
    preview_expression,
)

__all__ = [
    "AuthoringError", "AuthoringPlan", "parse_plan", "error_payload",
    "http_status", "PLAN_SCHEMA_VERSION", "ALGORITHM_VERSION",
    "inspect_layer", "preview_expression", "commit_expression",
]
