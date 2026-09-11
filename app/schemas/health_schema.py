"""健康/状态子系统契约模型（V9 契约基石，ADR-0138）。

从 `app/api/routes/health.py`、`app/api/routes/version.py` 内联迁出。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """GET /health —— 基础存活检查。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "healthy",
                    "timestamp": "2026-09-11T00:00:00+00:00",
                    "service": "WebGIS AI Agent",
                    "version": "0.1.3",
                    "agent_runtime": "pi",
                    "pi_workers_alive": "2/2",
                }
            ]
        }
    )

    status: str
    timestamp: str
    service: str
    version: str
    agent_runtime: str
    pi_workers_alive: Optional[str] = None


class LivenessResponse(BaseModel):
    """GET /health/live —— k8s livenessProbe 轻量存活。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"status": "alive"}]}
    )

    status: str


class ReadyResponse(BaseModel):
    """GET /ready —— k8s readinessProbe（200=就绪 / 503=暂停接流）。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"ready": True}]}
    )

    ready: bool


class SreComponentStatus(BaseModel):
    """单个组件的健康分类（ok | degraded | down | not_configured）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"status": "ok", "latency_ms": 1.7, "detail": None}
            ]
        }
    )

    status: str
    latency_ms: Optional[float] = None
    detail: Optional[str] = None


class SreStatusReport(BaseModel):
    """GET /status/detailed —— 鉴权 SRE 组件健康分类学（Epic 10 W11）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "ok",
                    "components": {
                        "db": {"status": "ok", "latency_ms": 2.1, "detail": None}
                    },
                    "stuck_jobs": 0,
                    "refresh_age_s": 1.02,
                }
            ]
        }
    )

    status: str  # ok | degraded | down
    components: dict
    stuck_jobs: Optional[int] = None
    refresh_age_s: Optional[float] = None


class VersionResponse(BaseModel):
    """GET /version —— 构建身份（公开、极简、无环境细节）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "version": "0.1.3",
                    "commit": "8b5b8375",
                    "python": "3.12.7",
                    "extensions_api": "1.2.0",
                }
            ]
        }
    )

    version: str
    commit: str
    python: str
    extensions_api: str
    timestamp: str
