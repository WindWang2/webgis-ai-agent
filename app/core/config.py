"""核心配置模块"""
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field


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
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"

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
    TIANDITU_TOKEN: str = "2f2497677943d79a29e344e760c41f92"

    # Sentinel Hub
    SENTINELHUB_CLIENT_ID: str = ""
    SENTINELHUB_CLIENT_SECRET: str = ""

    # NASA EarthData
    NASA_EARTHDATA_USERNAME: str = ""
    NASA_EARTHDATA_PASSWORD: str = ""

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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore" # 忽略多余的环境变量


settings = Settings()
