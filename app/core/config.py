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

    # 环境配置：development / production
    ENV: str = Field(default="development", description="运行环境")

    # 服务器配置
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = True  # 将在 __init__ 中根据 ENV 自动调整

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

    # JWT 配置
    SECRET_KEY: str = Field(
        default="",
        description="JWT密钥用于签名和验签"
    )

    @field_validator("SECRET_KEY", mode="before")
    @classmethod
    def _validate_secret_key(cls, v):
        """验证SECRET_KEY，如果为空则抛出错误或使用默认值"""
        if not v:
            # 从环境变量读取
            env_key = os.environ.get("SECRET_KEY")
            if env_key:
                return env_key
            # 生产环境必须有key，开发可以使用临时key
            import warnings
            warnings.warn(
                "⚠️ 安全警告：SECRET_KEY 未配置！JWT认证可能无法正常工作。建议设置环境变量 SECRET_KEY",
                UserWarning,
            )
            # 返回临时key（仅开发环境使用）
            return "dev-temp-secret-key-change-in-production-"
        return v

    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60 * 24, description="Access token过期时间(分钟)")

    # Redis 配置
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # Celery 配置
    CELERY_BROKER_URL: str = Field(default="redis://localhost:6379/0")
    CELERY_RESULT_BACKEND: str = Field(default="redis://localhost:6379/0")

    # GIS 配置
    DATA_DIR: str = Field(default="./data")
    TMP_DIR: str = Field(default="./tmp")

    @field_validator("DEBUG", mode="before")
    @classmethod
    def _set_debug_from_env(cls, v):
        """根据环境自动设置 DEBUG 标志"""
        # 完全忽略传入值，只基于环境决定
        env = os.environ.get("ENV", "development").lower()
        if env == "production":
            return False
        # 非生产环境默认启用DEBUG(True)
        return True

    @field_validator("ENV", mode="before")
    @classmethod
    def _normalize_env(cls, v):
        """标准化环境名称"""
        if v is None:
            return "development"
        v_lower = v.lower().strip()
        if v_lower in ("prod", ):
            return "production"
        return v_lower

    def is_production(self) -> bool:
        """判断是否为生产环境"""
        return self.ENV.lower() == "production"

    # ============ Issue 管理配置 ============

    # 是否启用 Issue 自动分类和分配
    ENABLE_ISSUE_CHECK: bool = Field(
        default=True,
        description="是否在 Issue 事件触发自动分类和分配"
    )

    # Issue 自动分配开关
    ISSUE_AUTO_ASSIGN: bool = Field(
        default=True,
        description="是否自动分配 Issue 给负责人"
    )

    # Issue 是否按分类自动分配（True=按分类映射角色False=轮询）
    ISSUE_USE_CATEGORY_ASSIGN: bool = Field(
        default=True,
        description="是否根据 Issue 分类自动分配给对应角色"
    )

    # Issue 角色到具体人员的映射（格式：{"coder": ["user1", "user2"], ...}）
    ISSUE_ASSIGNEES: dict = Field(
        default={},
        description="Issue 角色到具体人员的映射，用于分配"
    )

    # Issue 角色到角色的中文映射（显示用）
    ISSUE_ROLE_MAPPING: dict = Field(
        default={
            "coder": "👨‍💻 开发工程师",
            "researcher": "🔬 技术研究员",
            "academic": "🎓 学术顾问",
        },
        description="Issue 角色到中文名的映射"
    )

    # Issue 通知开关
    ENABLE_ISSUE_NOTIFY: bool = Field(
        default=True,
        description="是否发送 Issue 相关飞书通知"
    )

    # Issue 超时配置（单位：小时）
    ISSUE_TIMEOUT_HOURS: int = Field(
        default=72,
        description="Issue 处理超时时间，超过此时间未处理自动提醒"
    )

    ISSUE_ENABLE_TIMEOUT_REMINDER: bool = Field(
        default=True,
        description="是否启用 Issue 处理超时提醒"
    )

    # ============ GitHub PR 审核配置（保留原配置） ============
    # GitHub Webhook Secret
    GITHUB_WEBHOOK_SECRET: str = Field(
        default="",
        description="GitHub Webhook 签名密钥"
    )

    # GitHub Personal Access Token
    GITHUB_TOKEN: str = Field(default="", description="GitHub API Token")

    # 仓库信息
    GITHUB_REPO_OWNER: str = Field(default="", description="GitHub 仓库所有者")
    GITHUB_REPO_NAME: str = Field(default="", description="GitHub 仓库名称")

    # PR 检查开关
    ENABLE_PR_CHECK: bool = Field(default=True, description="是否启用 PR 自动检查")

    # PR 检查规则
    PR_CHECK_ROUFF: bool = Field(default=True, description="是否启用 ruff 代码检查")
    PR_CHECK_COVERAGE: bool = Field(default=False, description="是否启用测试覆盖度检查")
    PR_MIN_COVERAGE_PERCENT: int = Field(default=80, description="最小测试覆盖度要求")
    PR_CHECK_BANDIT: bool = Field(default=False, description="是否启用安全扫描")

    # 审核人配置
    PR_REVIEWERS: List[str] = Field(default=[], description="PR 审核人列表")
    PR_AUTO_ASSIGN_REVIEWER: bool = Field(default=True, description="是否自动分配审核人")

    # 超时配置
    PR_TIMEOUT_HOURS: int = Field(default=24, description="PR 审核超时时间")
    PR_ENABLE_TIMEOUT_REMINDER: bool = Field(default=True, description="是否启用超时提醒")

    # 合并后操作
    PR_ENABLE_MERGE_NOTIFICATION: bool = Field(default=True, description="合并后是否发送通知")

    # ============ 飞书通知配置 ============
    FEISHU_WEBHOOK_URL: str = Field(default="", description="飞书 Webhook URL")
    FEISHU_CHAT_ID: str = Field(default="", description="飞书群 ID")
    FEISHU_APP_ID: str = Field(default="", description="飞书 App ID")
    FEISHU_APP_SECRET: str = Field(default="", description="飞书 App Secret")

    # 飞书通知总开关
    ENABLE_FEISHU_NOTIFY: bool = Field(default=True, description="是否启用飞书通知")

    model_config = {"env_file": ".env", "case_sensitive": True}

# 全局配置实例
settings = Settings(_env_file="")  # 先创建实例，避免循环依赖

def get_settings() -> Settings:
    """获取全局配置实例（供其他模块使用）"""
    return settings

__all__ = ["Settings", "settings", "get_settings"]