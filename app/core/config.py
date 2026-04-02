"""
核心配置模块
"""
import warnings
import os
from typing import List
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator


class Settings(BaseSettings):
    """应用配置"""

    # 项目名称
    PROJECT_NAME: str = "WebGIS AI Agent"

    # 服务器配置
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = True

    # CORS 配置
    CORS_ORIGINS: List[str] = ["*"]

    # ======== 数据库配置 ========
    # 分别配置数据库各组件，支持环境变量覆盖
    DB_HOST: str = Field(default="localhost", description="数据库主机")
    DB_PORT: int = Field(default=5432, description="数据库端口")
    DB_USER: str = Field(default="postgres", description="数据库用户名")
    DB_PASSWORD: str = Field(
        default="postgres",
        description="数据库密码 ⚠️ 生产环境请设置强密码!"
    )
    DB_NAME: str = Field(default="webgis", description="数据库名称")

    # 由组件组合构建的数据库 URL（优先级最高）
    DATABASE_URL: str = Field(
        default="",
        description="完整数据库连接 URL（优先使用）"
    )

    @field_validator("DB_PASSWORD", mode="before")
    @classmethod
    def _warn_default_password(cls, v):
        """警告使用默认密码"""
        # 允许通过环境变量 DISABLE_DB_PASSWORD_WARN=true 禁用警告
        if v == "postgres" and not os.environ.get("DISABLE_DB_PASSWORD_WARN"):
            warnings.warn(
                "🔒 安全警告: 使用默认数据库密码 'postgres'！\n"
                "请通过环境变量 DB_PASSWORD 设置强密码用于生产环境！",
                UserWarning,
            )
        return v

    def build_database_url(self) -> str:
        """构建数据库连接 URL"""
        # 如果显式设置了 DATABASE_URL，直接使用
        if self.DATABASE_URL:
            return self.DATABASE_URL
        # 否则由组件构建
        return (
            f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
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