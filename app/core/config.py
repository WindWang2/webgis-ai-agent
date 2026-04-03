"""
核心配置模块
"""
import json
import os
import warnings
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator

class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        case_sensitive=True,
        extra="ignore"
    )

    # 项目名称
    PROJECT_NAME: str = "WebGIS AI Agent"

    # 环境配置：development / production
    ENV: str = Field(default="development", description="运行环境")

    # 服务器配置
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = Field(default=False, description="Enable debug mode")

    # CORS 配置
    DEFAULT_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000",
    ]

    CORS_ORIGINS: List[str] = Field(default=[], description="允许的 CORS 源列表")

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v):
        """解析 CORS 源配置，支持环境变量"""
        if not v:
            env_value = os.environ.get("CORS_ORIGINS")
            if env_value:
                try:
                    return json.loads(env_value)
                except json.JSONDecodeError:
                    return [origin.strip() for origin in env_value.split(",") if origin.strip()]
        if isinstance(v, list):
            return v
        return []

    def get_cors_origins(self) -> List[str]:
        if self.CORS_ORIGINS:
            return self.CORS_ORIGINS
        return self.DEFAULT_CORS_ORIGINS

    @field_validator("CORS_ORIGINS", mode="after")
    @classmethod
    def _warn_wildcard_credentials(cls, v):
        if "*" in v:
            warnings.warn(
                "⚠️ 安全警告：CORS 配置中使用通配符 '*' 与 allow_credentials=True 存在冲突！",
                UserWarning,
            )
        return v

    # 数据库配置
    DB_HOST: str = Field(default="localhost", description="数据库主机")
    DB_PORT: int = Field(default=5432, description="数据库端口")
    DB_USER: str = Field(default="postgres", description="数据库用户名")
    DB_PASSWORD: str = Field(default="postgres", description="数据库密码")
    DB_NAME: str = Field(default="webgis", description="数据库名称")
    DATABASE_URL: str = Field(default="", description="完整数据库连接 URL")

    @field_validator("DB_PASSWORD", mode="before")
    @classmethod
    def _warn_default_password(cls, v):
        if v == "postgres" and not os.environ.get("DISABLE_DB_PASSWORD_WARN"):
            warnings.warn("🔒 安全警告：使用默认数据库密码 'postgres'！", UserWarning)
        return v

    def build_database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    # JWT 配置 - 支持从 SECRET_KEY 或 JWT_SECRET_KEY 环境变量读取
    JWT_SECRET_KEY: str = Field(default="", description="JWT密钥(首选)")
    SECRET_KEY: str = Field(default="", description="JWT密钥(备用)")

    @field_validator("SECRET_KEY", mode="before")
    @classmethod
    def _load_jwt_secret(cls, v):
        """优先级读取JWT密钥: JWT_SECRET_KEY -> SECRET_KEY -> .env文件 -> 默认"""
        # 1. 优先 JWT_SECRET_KEY 环境变量
        jwt_env = os.environ.get("JWT_SECRET_KEY")
        if jwt_env:
            return jwt_env

        # 2. SECRET_KEY 环境变量
        if v:
            return v
        secret_env = os.environ.get("SECRET_KEY")
        if secret_env:
            return secret_env

        # 3. 检查 .env 文件 (手动加载)
        env_file = Path(".env")
        if env_file.exists():
            for line in env_file.read_text().strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key, val = key.strip(), val.strip().strip('"').strip("'")
                    if key == "JWT_SECRET_KEY" and val:
                        return val
                    if key == "SECRET_KEY" and val:
                        return val

        # 4. 都无值时 - 根据环境决定
        env = os.environ.get("ENV", "development").lower()
        if env == "production":
            raise ValueError("🚫 生产环境必须配置 JWT_SECRET_KEY 或 SECRET_KEY 环境变量！")
        
        # 开发环境使用临时key
        warnings.warn("⚠️ 开发环境使用临时 JWT 密钥！生产环境请配置环境变量", UserWarning)
        return "dev-unsafe-secret-key-must-change-in-prod-2024"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60 * 24, description="Access token过期时间(分钟)")

    # Redis 配置
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # Celery 配置
    CELERY_BROKER_URL: str = Field(default="redis://localhost:6379/0")
    CELERY_RESULT_BACKEND: str = Field(default="redis://localhost:6379/0")

    # GIS 配置
    DATA_DIR: str = Field(default="./data")
    TMP_DIR: str = Field(default="./tmp")

    # ============ GitHub PR 审核配置 ============
    # GitHub Webhook Secret（在 GitHub Webhook 设置中配置的签名密钥）
    GITHUB_WEBHOOK_SECRET: str = Field(
        default="YOUR_GITHUB_WEBHOOK_SECRET_HERE",
        description="GitHub Webhook 签名密钥，用于验证请求来源"
    )

    # GitHub Personal Access Token（需要 repo 权限）
    GITHUB_TOKEN: str = Field(
        default="YOUR_GITHUB_TOKEN_HERE",
        description="GitHub API Token，在 GitHub Settings > Developer settings > Personal access tokens 生成"
    )

    # 仓库信息（格式：owner/repo 例如 myorg/myproject）
    GITHUB_REPO_OWNER: str = Field(
        default="YOUR_REPO_OWNER",
        description="GitHub 仓库所有者（组织名或用户名）"
    )
    GITHUB_REPO_NAME: str = Field(
        default="YOUR_REPO_NAME",
        description="GitHub 仓库名称"
    )

    # 是否启用 PR 自动检查
    ENABLE_PR_CHECK: bool = Field(
        default=True,
        description="是否在 PR 事件触发自动检查"
    )

    # PR 检查规则配置
    PR_CHECK_ROUFF: bool = Field(
        default=True,
        description="是否启用 ruff 代码规范检查"
    )
    PR_CHECK_BLACK: bool = Field(
        default=False,
        description="是否启用 black 代码格式化检查"
    )
    PR_CHECK_COVERAGE: bool = Field(
        default=True,
        description="是否启用测试覆盖度检查"
    )
    PR_MIN_COVERAGE_PERCENT: int = Field(
        default=80,
        description="最小测试覆盖度要求百分比"
    )
    PR_CHECK_BANDIT: bool = Field(
        default=True,
        description="是否启用 bandit 安全扫描"
    )
    PR_CHECK_COMMIT: bool = Field(
        default=True,
        description="是否启用 Conventional Commits 提交信息检查"
    )

    # 审核人配置
    PR_REVIEWERS: List[str] = Field(
        default=[],
        description="PR 审核人 GitHub username 列表用于自动分配按列表轮询"
    )
    PR_AUTO_ASSIGN_REVIEWER: bool = Field(
        default=True,
        description="是否自动分配审核人"
    )

    # 超时配置（单位：小时）
    PR_TIMEOUT_HOURS: int = Field(
        default=24,
        description="PR 审核超时时间超过此时间未审核自动提醒"
    )
    PR_ENABLE_TIMEOUT_REMINDER: bool = Field(
        default=True,
        description="是否启用超时自动提醒"
    )

    # 合并后自动操作
    PR_AUTO_ADD_LABEL_ON_MERGE: bool = Field(
        default=True,
        description="合并后是否自动添加标签"
    )
    PR_MERGE_LABEL: str = Field(
        default="merged",
        description="合并后自动添加的标签名称"
    )
    PR_ENABLE_MERGE_NOTIFICATION: bool = Field(
        default=True,
        description="合并后是否发送飞书通知"
    )

    # ============ 飞书通知配置 ============
    # 飞书群 Webhook URL（群机器人设置中获得）
    FEISHU_WEBHOOK_URL: str = Field(
        default="",
        description="飞书自定义机器人 Webhook URL 在群设置 > 群机器人 > 自定义机器人中获得"
    )

    # 飞书群 Chat ID（可选用于私聊或群聊）
    FEISHU_CHAT_ID: str = Field(
        default="",
        description="飞书群 ID 格式如 oc_xxx 用于发送群消息"
    )

    # 飞书 App ID/Secret（如需要使用官方 API 而不是 Webhook）
    FEISHU_APP_ID: str = Field(
        default="",
        description="飞书企业应用的 App ID"
    )
    FEISHU_APP_SECRET: str = Field(
        default="",
        description="飞书企业应用的 App Secret"
    )

    # 是否启用飞书通知
    ENABLE_FEISHU_NOTIFY: bool = Field(
        default=True,
        description="是否启用飞书通知功能"
    )

    @field_validator("DEBUG", mode="before")
    @classmethod
    def _set_debug_from_env(cls, v):
        env = os.environ.get("ENV", "development").lower()
        return env != "production"

    @field_validator("ENV", mode="before")
    @classmethod
    def _normalize_env(cls, v):
        if v is None:
            return "development"
        v_lower = v.lower().strip()
        if v_lower in ("prod", ):
            return "production"
        return v_lower

    def is_production(self) -> bool:
        return self.ENV.lower() == "production"


# 全局配置实例 - 手动加载环境变量
def _load_env_variables():
    """从 .env 文件手动加载环境变量"""
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and val and key not in os.environ:
                    os.environ[key] = val

_load_env_variables()

# 创建全局配置实例
settings = Settings()

def get_settings() -> Settings:
    """获取全局配置实例"""
    return settings

__all__ = ["Settings", "settings", "get_settings"]