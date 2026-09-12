"""API v2 挂载层（V9 契约基石，ADR-0138 / P7）。

v2 = v1 复用 router + 统一契约语义的示范挂载：
- 默认新错误信封（全局默认即新信封；v2 拒绝 X-Error-Envelope: detail 回退）；
- 统一分页语义（Page[T]；ad-hoc limit alias 自 v2 起标 deprecated）；
- v1 加 Deprecation / Sunset 头 + 使用量打点（见 main.py 的中间件）。

示范迁移子系统（V7/V8 特性）：lakehouse、geocompute、workflow-runtime。
路由对象**复用**（同一实现、同一契约测试），v2 仅是挂载面 + 语义承诺；
不复制任何业务逻辑，不下线任何 v1 端点。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import geocompute as geocompute_routes
from app.api.routes import lakehouse as lakehouse_routes
from app.api.routes import lakehouse_datasets as lakehouse_datasets_routes
from app.api.routes import workflow_runtime as workflow_runtime_routes

#: v1 Sunset 候选日（ADR-0138 迁移计划；实际下线由使用量数据驱动，另行公告）。
V1_SUNSET_DATE = "Wed, 30 Jun 2027 00:00:00 GMT"


def build_v2_router() -> APIRouter:
    router = APIRouter(prefix="/api/v2")

    def _v2_unique_id(route) -> str:
        module = route.endpoint.__module__.rsplit(".", 1)[-1]
        # 同一函数可挂多条路径（如 cancel 的 runs/plans 双路由）——path 参与唯一化
        path_token = route.path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
        return f"v2_{module}_{route.name}_{path_token}"

    router.include_router(
        lakehouse_routes.router,
        tags=["Lakehouse / 空间数据湖仓"],
        generate_unique_id_function=_v2_unique_id,
    )
    router.include_router(
        lakehouse_datasets_routes.router,
        tags=["Lakehouse / 数据集版本（V8）"],
        generate_unique_id_function=_v2_unique_id,
    )
    router.include_router(
        geocompute_routes.router,
        tags=["GeoCompute / 执行平面"],
        generate_unique_id_function=_v2_unique_id,
    )
    router.include_router(
        workflow_runtime_routes.router,
        tags=["Workflow Runtime V5"],
        generate_unique_id_function=_v2_unique_id,
    )
    return router
