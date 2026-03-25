"""
核心配置模块
"""

from typing import List
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """应用配置"""
    
    # 项目名称
    PROJECT_NAME: str = "WebGIS AI Agent"
    
    # 服务器配置
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = True
    
    # CORS 配置
    CORS_ORIGINS: List[str] = ["*"]
    
    # 数据库配置（测试时使用 SQLite，生产环境使用 PostgreSQL）
    DATABASE_URL: str = Field(
        default="sqlite:///./test.db",
        description="数据库连接 URL"
    )
    
    # Redis 配置 (用于 Celery)
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis 连接 URL"
    )
    
    # Celery 配置
    CELERY_BROKER_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Celery Broker URL"
    )
    CELERY_RESULT_BACKEND: str = Field(
        default="redis://localhost:6379/0",
        description="Celery Result Backend URL"
    )
    
    # GIS 配置
    DATA_DIR: str = Field(
        default="./data",
        description="数据存储目录"
    )
    TMP_DIR: str = Field(
        default="./tmp",
        description="临时文件目录"
    )
    
    class Config:
        env_file = ".env"
        case_sensitive = True


# 全局配置实例
settings = Settings()
