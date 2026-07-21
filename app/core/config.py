"""核心配置模块"""
import ipaddress
import logging
import re
import secrets
import warnings
from typing import List, Optional
from urllib.parse import urlparse

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
    # 默认 False，避免 .env 缺失时生产端泄漏堆栈/凭证。
    # 本地开发请在 .env 显式设置 DEBUG=true。
    DEBUG: bool = False
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
    # 规划阶段专用模型；留空时回退 LLM_MODEL（便于以后单独配更便宜的模型）
    LLM_PLANNER_MODEL: str = ""
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
    # 审计 P2：默认改为 localhost:3000（Next.js dev server），而非 ["*"]。
    # 生产环境 validator 会强制要求显式 allow-list。
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]

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

    @model_validator(mode="after")
    def _validate_required_env_vars(self) -> "Settings":
        """Fail fast if critical env vars are missing or set to placeholder values.

        审计 P0：LLM_API_KEY 默认值为 "your-api-key-here"，若环境变量未设置，
        应用会以占位符密钥启动，导致 LLM 调用时返回 401 而非在启动时报错。
        生产模式下必须显式配置；开发模式下仅警告。
        """
        _PLACEHOLDER = "your-api-key-here"
        _PROD_REQUIRED: dict[str, str] = {
            "LLM_API_KEY": _PLACEHOLDER,
        }

        if self.is_production():
            for var_name, placeholder in _PROD_REQUIRED.items():
                value = getattr(self, var_name)
                if not value or value == placeholder:
                    raise RuntimeError(
                        f"{var_name} must be set to a real value in production. "
                        f"Current value: '{value}'. "
                        f"Set it via the {var_name} environment variable."
                    )
        else:
            # 开发模式：检查占位符并警告
            for var_name, placeholder in _PROD_REQUIRED.items():
                value = getattr(self, var_name)
                if value == placeholder:
                    logger.warning(
                        "%s is set to placeholder value '%s'. "
                        "LLM calls will fail. Set %s in .env for full functionality.",
                        var_name, placeholder, var_name,
                    )
        return self

    @model_validator(mode="after")
    def _validate_cors_origins(self) -> "Settings":
        """生产环境禁止 CORS_ORIGINS=['*']：与 allow_credentials=True 组合
        会把任意来源都视为可信凭证调用方，等同于关闭同源保护。"""
        if self.is_production() and "*" in self.CORS_ORIGINS:
            raise RuntimeError(
                "CORS_ORIGINS=['*'] is not allowed in production. "
                "Set an explicit allow-list (e.g. CORS_ORIGINS=https://your.app)."
            )
        return self

    @model_validator(mode="after")
    def _validate_external_urls(self) -> "Settings":
        """验证外部 URL 配置，防止 SSRF 攻击。

        审计 P1：之前只对 *非默认值* 做校验，若攻击者通过环境变量注入
        覆盖默认 URL（如 LLM_BASE_URL=https://evil.com），SSRF 校验被完全绕过。
        现在对所有 URL 统一校验，默认值也不例外。
        """
        for attr in ("LLM_BASE_URL", "OVERPASS_API_URL", "NOMINATIM_URL"):
            url = getattr(self, attr)
            self._validate_no_ssrf(url, field=attr)
        return self

    @staticmethod
    def _validate_no_ssrf(url: str, field: str = "URL") -> None:
        """校验单个 URL 不允许指向内网/元数据/非 HTTP 协议。"""
        parsed = urlparse(url)

        # 只允许 http / https 协议
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"{field}='{url}' uses disallowed scheme '{parsed.scheme}'. "
                f"Only http:// and https:// are allowed."
            )

        hostname = parsed.hostname
        if not hostname:
            raise ValueError(f"{field}='{url}' has no hostname.")

        # 阻止本地回环
        if hostname in ("localhost", "127.0.0.1", "::1"):
            raise ValueError(
                f"{field}='{url}' points to localhost. "
                f"Localhost URLs are blocked to prevent SSRF."
            )

        # 阻止云元数据端点（AWS / GCP / Azure）
        _METADATA_IPS = {
            "169.254.169.254",  # AWS / GCP
            "metadata.google.internal.",  # GCP（尾部点保留 FQDN 习惯）
            "169.254.169.254.",   # 带尾点的变体
        }
        if hostname.lower() in _METADATA_IPS:
            raise ValueError(
                f"{field}='{url}' points to a cloud metadata endpoint. Blocked."
            )

        # 尝试解析 hostname → IP，检查是否为私有地址
        try:
            addr = ipaddress.ip_address(hostname)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                raise ValueError(
                    f"{field}='{url}' resolves to private/loopback IP {addr}. "
                    f"Only public IPs are allowed."
                )
        except ValueError as exc:
            # 不是纯 IP 地址（可能是域名如 api.openai.com）— 尝试 DNS 解析
            if "is not allowed" in str(exc):
                raise
            # 域名：做基本黑名单检查
            _BLOCKED_DOMAIN_PATTERNS = [
                r"^169\.254",        # link-local
                r"^10\.",            # 10.0.0.0/8
                r"^172\.(1[6-9]|2\d|3[01])\.",  # 172.16.0.0/12
                r"^192\.168\.",      # 192.168.0.0/16
                r"^127\.",           # loopback
                r"metadata",         # 元数据服务
                r"internal$",        # k8s internal
            ]
            host_lower = hostname.lower()
            for pat in _BLOCKED_DOMAIN_PATTERNS:
                if re.match(pat, host_lower):
                    raise ValueError(
                        f"{field}='{url}' uses blocked domain pattern '{pat}'."
                    )


settings = Settings()
