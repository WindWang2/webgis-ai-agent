"""核心配置模块"""
import secrets
import logging
import warnings
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field, model_validator

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """应用配置"""
    PROJECT_NAME: str = "WebGIS AI Agent"
    DEBUG: bool = True
    API_V1_STR: str = "/api"
    ENV: str = "development"

    def is_production(self) -> bool:
        """判断是否为生产环境"""
        return self.ENV.lower() == "production"

    # JWT
    JWT_SECRET_KEY: str = Field(default="", env="JWT_SECRET_KEY")

    # 数据库
    DATABASE_URL: str = Field(default="sqlite:///./data/webgis.db", env="DATABASE_URL")

    # LLM 配置 (OpenAI 兼容接口)
    LLM_BASE_URL: str = Field(default="http://localhost:8000/v1", env="LLM_BASE_URL")
    LLM_API_KEY: str = Field(default="not-needed", env="LLM_API_KEY")
    LLM_MODEL: str = Field(default="MiniMax-M2.5", env="LLM_MODEL")
    LLM_PROMPT_CACHING_ENABLED: bool = True

    # OSM
    OVERPASS_API_URL: str = "https://overpass.openstreetmap.fr/api/interpreter"
    NOMINATIM_URL: str = "https://nominatim.openstreetmap.org/search"

    # 天地图
    TIANDITU_TOKEN: str = Field(default="", env="TIANDITU_TOKEN")

    # Sentinel Hub
    SENTINELHUB_CLIENT_ID: str = Field(default="", env="SENTINELHUB_CLIENT_ID")
    SENTINELHUB_CLIENT_SECRET: str = Field(default="", env="SENTINELHUB_CLIENT_SECRET")

    # NASA EarthData
    NASA_EARTHDATA_USERNAME: str = Field(default="", env="NASA_EARTHDATA_USERNAME")
    NASA_EARTHDATA_PASSWORD: str = Field(default="", env="NASA_EARTHDATA_PASSWORD")

    # OpenTopography
    OPENTOPOGRAPHY_API_KEY: str = Field(default="", env="OPENTOPOGRAPHY_API_KEY")

    # 数据目录
    DATA_DIR: str = "./data"
    TMP_DIR: str = "./tmp"

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # Celery & Redis
    REDIS_URL: str = Field(default="redis://localhost:16379/0", env="REDIS_URL")
    CELERY_BROKER_URL: str = Field(default="redis://localhost:16379/0", env="CELERY_BROKER_URL")
    CELERY_RESULT_BACKEND: str = Field(default="redis://localhost:16379/1", env="CELERY_RESULT_BACKEND")
    USE_REDIS: bool = False # 默认不开启，除非显式配置且可用

    @model_validator(mode="after")
    def _ensure_jwt_secret(self) -> "Settings":
        if not self.JWT_SECRET_KEY:
            self.JWT_SECRET_KEY = secrets.token_urlsafe(32)
            warnings.warn(
                "JWT_SECRET_KEY is not set. A random secret has been generated. "
                "Set JWT_SECRET_KEY in .env for persistent sessions.",
                stacklevel=2,
            )
            logger.warning("JWT_SECRET_KEY not set, generated random secret for this session")
        return self

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore" # 忽略多余的环境变量


settings = Settings()
