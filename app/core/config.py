"""核心配置模块"""
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """应用配置"""
    PROJECT_NAME: str = "WebGIS AI Agent"
    DEBUG: bool = True
    API_V1_STR: str = "/api"

    # 数据库 (SQLite)
    DATABASE_URL: str = "sqlite:///./webgis.db"

    # LLM 配置 (OpenAI 兼容接口)
    LLM_BASE_URL: str = "http://192.168.193.70:8000/v1"
    LLM_API_KEY: str = "not-needed"
    LLM_MODEL: str = "MiniMax-M2.5"

    # OSM
    OVERPASS_API_URL: str = "https://overpass-api.de/api/interpreter"
    NOMINATIM_URL: str = "https://nominatym.openstreetmap.org"

    # 天地图
    TIANDITU_TOKEN: str = ""

    # Sentinel Hub
    SENTINELHUB_CLIENT_ID: str = ""
    SENTINELHUB_CLIENT_SECRET: str = ""

    # NASA EarthData
    NASA_EARTHDATA_USERNAME: str = ""
    NASA_EARTHDATA_PASSWORD: str = ""

    # 数据目录
    DATA_DIR: str = "./data"
    TMP_DIR: str = "./tmp"

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
