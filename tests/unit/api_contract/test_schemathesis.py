"""V9 契约基石：Schemathesis 契约模糊测试（ADR-0138 / P8）。

资源受控（任务书 §2 P8）：
- ``max_examples=30`` / 每操作（hypothesis settings）；
- 按 tag/path 分片：本 shard 只跑「无外部依赖面」allowlist 端点
  （health / version / auth 校验层）。DB/Redis/Celery 依赖端点在单测与
  CI contract lane 均无真实 2xx 可达性，不进模糊面（排除清单镜像进
  ADR-0138 与 PR 附表；这些端点的契约由 api_contract 覆盖率门禁 +
  各子系统服务测试保证）；
- 检查：not_a_server_error（5xx 零容忍）+ status_code_conformance。
"""
from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import schemathesis  # noqa: E402
from hypothesis import HealthCheck, settings  # noqa: E402

from app.main import app as fastapi_app  # noqa: E402

#: 本 shard 的端点 allowlist（无外部依赖面）
ALLOWED_PATHS = (
    "/api/v1/health/live",
    "/api/v1/version",
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
)

#: not_a_server_error 的例外：/auth/register 的 503 = 公开注册显式关闭
#: （文档已声明的业务态，非服务器内部错误）。该端点仍受
#: status_code_conformance 约束（不允许未声明状态码）。
NO_5XX_CHECK_EXCLUDED = ("/api/v1/auth/register",)

schema = schemathesis.openapi.from_asgi("/openapi.json", app=fastapi_app).include(
    path=list(ALLOWED_PATHS),
    method=["post", "get"],
)

ST = settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


@ST
@schema.parametrize()
def test_contract_fuzz_no_5xx_and_conformance(case):
    """5xx 零容忍 + 响应与 OpenAPI 声明一致（status_code_conformance）。"""
    if case.operation.path in NO_5XX_CHECK_EXCLUDED:
        case.call_and_validate(
            checks=(schemathesis.checks.status_code_conformance,)
        )
    else:
        case.call_and_validate(
            checks=(
                schemathesis.checks.not_a_server_error,
                schemathesis.checks.status_code_conformance,
            )
        )


def test_allowlist_paths_exist_in_openapi():
    """allowlist 防腐化：路径必须真实存在于当前 OpenAPI。"""
    schema_raw = fastapi_app.openapi()
    for path in ALLOWED_PATHS:
        assert path in schema_raw["paths"], f"{path} 不在 OpenAPI 中（allowlist 过期）"
