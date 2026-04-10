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
    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:15432/webgis"

    # LLM 配置 (OpenAI 兼容接口)
    LLM_BASE_URL: str = "http://localhost:8000/v1"
    LLM_API_KEY: str = "not-needed"
    LLM_MODEL: str = "MiniMax-M2.5"

    # OSM
    OVERPASS_API_URL: str = "https://overpass.openstreetmap.fr/api/interpreter"
    NOMINATIM_URL: str = "https://nominatim.openstreetmap.org/search"

    # 天地图
    TIANDITU_TOKEN: str = ""

    # Sentinel Hub
    SENTINELHUB_CLIENT_ID: str = ""
    SENTINELHUB_CLIENT_SECRET: str = ""

    # NASA EarthData
    NASA_EARTHDATA_USERNAME: str = ""
    NASA_EARTHDATA_PASSWORD: str = ""

    # OpenTopography
    OPENTOPOGRAPHY_API_KEY: str = ""

    # 数据目录
    DATA_DIR: str = "./data"
    TMP_DIR: str = "./tmp"

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://192.168.193.121:3000", "http://192.168.193.121:3003"]

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
