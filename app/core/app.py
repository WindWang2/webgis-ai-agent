from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import orchestration
from app.api.routes import orchestration_v2

def create_app() -> FastAPI:
    app = FastAPI(
        title="WebGIS AI Agent Orchestration API",
        description="多Agent协同编排层API，支持GIS任务的自动解析、调度与结果聚合",
        version="1.0.0"
    )

    # CORS配置
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    app.include_router(orchestration.router, prefix="/api/v1/orchestration", tags=["orchestration"])
    app.include_router(orchestration_v2.router, prefix="/api/v1/orchestration/tasks", tags=["orchestration-tasks"])

    @app.get("/health")
    async def health_check():
        return {"status": "ok", "service": "orchestration"}

    return app
