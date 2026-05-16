"""核心配置模块"""
import secrets
import logging
import warnings
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """应用配置"""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    PROJECT_NAME: str = "WebGIS AI Agent"
    DEBUG: bool = True
    API_V1_STR: str = "/api"
    ENV: str = "development"

    def is_production(self) -> bool:
        """判断是否为生产环境"""
        return self.ENV.lower() == "production"

    # JWT
    JWT_SECRET_KEY: str = ""

    # 数据库
    DATABASE_URL: str = "sqlite:///./data/webgis.db"

    # LLM 配置 (OpenAI 兼容接口)
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_API_KEY: str = "your-api-key-here"
    LLM_MODEL: str = "deepseek-v4-flash"
    LLM_PROMPT_CACHING_ENABLED: bool = True

    # OSM
    OVERPASS_API_URL: str = "https://overpass.openstreetmap.fr/api/interpreter"
    NOMINATIM_URL: str = "https://nominatim.openstreetmap.org/search"

    # 天地图
    TIANDITU_TOKEN: str = ""

    # 高德地图 (Amap)
    AMAP_API_KEY: str = ""
    AMAP_JS_KEY: str = ""
    AMAP_JS_SECURITY_KEY: str = ""

    # 百度地图 (Baidu Maps)
    BAIDU_MAP_AK: str = ""

    # 百度千帆 (Baidu Qianfan AI Search v2) — 网络搜索能力
    # token 形如 bce-v3/ALTAK-xxx/sk-xxx，作为 Authorization: Bearer 头使用
    BAIDU_QIANFAN_TOKEN: str = ""

    # MapBox / Bing / Tencent
    MAPBOX_TOKEN: str = ""
    BING_MAP_KEY: str = ""
    TENCENT_MAP_KEY: str = ""

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
    CORS_ORIGINS: List[str] = ["*"]

    # Celery & Redis
    REDIS_URL: str = "redis://localhost:16379/0"
    CELERY_BROKER_URL: str = "redis://localhost:16379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:16379/1"
    USE_REDIS: bool = True

    # 代理设置
    HTTP_PROXY: Optional[str] = None
    HTTPS_PROXY: Optional[str] = None

    @model_validator(mode="after")
    def _ensure_jwt_secret(self) -> "Settings":
        if not self.JWT_SECRET_KEY:
            if self.is_production():
                raise RuntimeError(
                    "JWT_SECRET_KEY is required in production. "
                    "Set it via the JWT_SECRET_KEY environment variable."
                )
            self.JWT_SECRET_KEY = secrets.token_urlsafe(32)
            warnings.warn(
                "JWT_SECRET_KEY is not set. A random secret has been generated. "
                "Set JWT_SECRET_KEY in .env for persistent sessions.",
                stacklevel=2,
            )
            logger.warning("JWT_SECRET_KEY not set, generated random secret for this session")
        return self


settings = Settings()
