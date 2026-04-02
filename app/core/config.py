"""
核心配置模块
"""
import json
import os
import warnings
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
    # 开发环境的默认源（本地开发服务器）
    DEFAULT_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000",
    ]

    CORS_ORIGINS: List[str] = Field(
        default=[],
        description="允许的 CORS 源列表，空则使用默认值"
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v):
        """解析 CORS 源配置，支持环境变量"""
        # 如果传入空字典，从环境变量读取
        if not v:
            env_value = os.environ.get("CORS_ORIGINS")
            if env_value:
                try:
                    return json.loads(env_value)
                except json.JSONDecodeError:
                    return [origin.strip() for origin in env_value.split(",") if origin.strip()]
        # 如果已经是列表直接返回
        if isinstance(v, list):
            return v
        return []

    def get_cors_origins(self) -> List[str]:
        """获取有效的 CORS 源列表"""
        if self.CORS_ORIGINS:
            return self.CORS_ORIGINS
        return self.DEFAULT_CORS_ORIGINS

    @field_validator("CORS_ORIGINS", mode="after")
    @classmethod
    def _warn_wildcard_credentials(cls, v):
        """警告通配符与 credentials 冲突"""
        if "*" in v:
            warnings.warn(
                "⚠️ 安全警告：CORS 配置中使用通配符 '*' "
                "与 allow_credentials=True 存在冲突！",
                UserWarning,
            )
        return v

    # ======== 数据库配置 ========
    DB_HOST: str = Field(default="localhost", description="数据库主机")
    DB_PORT: int = Field(default=5432, description="数据库端口")
    DB_USER: str = Field(default="postgres", description="数据库用户名")
    DB_PASSWORD: str = Field(
        default="postgres",
        description="数据库密码"
    )
    DB_NAME: str = Field(default="webgis", description="数据库名称")

    DATABASE_URL: str = Field(
        default="",
        description="完整数据库连接 URL"
    )

    @field_validator("DB_PASSWORD", mode="before")
    @classmethod
    def _warn_default_password(cls, v):
        if v == "postgres" and not os.environ.get("DISABLE_DB_PASSWORD_WARN"):
            warnings.warn(
                "🔒 安全警告：使用默认数据库密码 'postgres'！",
                UserWarning,
            )
        return v

    def build_database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    # Redis 配置
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # Celery 配置
    CELERY_BROKER_URL: str = Field(default="redis://localhost:6379/0")
    CELERY_RESULT_BACKEND: str = Field(default="redis://localhost:6379/0")

    # GIS 配置
    DATA_DIR: str = Field(default="./data")
    TMP_DIR: str = Field(default="./tmp")

    model_config = {"env_file": ".env", "case_sensitive": True}


# 全局配置实例
settings = Settings()