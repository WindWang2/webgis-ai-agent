"""
PostgreSQL + PostGIS 数据库模型
B011 Fix: 使用统一的 Base 单例，避免重复定义冲突
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, BigInteger, ForeignKey, Index, UniqueConstraint, JSON, CheckConstraint
)
from sqlalchemy.orm import relationship
from app.core.database import Base

class Organization(Base):
    """组织机构表"""
    __tablename__ = "organizations"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class User(Base):
    """用户表"""
    __tablename__ = "users"
    
    id = Column(String(255), primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"))
    username = Column(String(100), nullable=False, unique=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255))
    full_name = Column(String(255))
    avatar_url = Column(String(500))
    role = Column(String(20), default="viewer")
    is_active = Column(Boolean, default=True)
    email_verified = Column(Boolean, default=False)
    last_login = Column(DateTime)
    login_count = Column(Integer, default=0)
    token_version = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    organization = relationship("Organization", backref="users", lazy="selectin")

    __table_args__ = (
        CheckConstraint("role IN ('viewer', 'editor', 'admin')", name="ck_user_role"),
    )

class Layer(Base):
    """图层表 - 支持 Vector/Raster/Tile 三种类型"""
    __tablename__ = "layers"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    creator_id = Column(String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    category = Column(String(50), index=True)
    layer_type = Column(String(20), nullable=False)
    geometry_type = Column(String(50))
    source_format = Column(String(50))
    source_url = Column(String(1000))
    crs = Column(String(100), default="EPSG:4326")
    bounds = Column(JSON)
    feature_count = Column(BigInteger, default=0)
    # style_config 充当当前图层套用的 template_id 指针（如 {"template_id": "tmpl_..."} 或直接存 ID 字符串）
    style_config = Column(JSON)
    visibility = Column(String(20), default="org")
    is_basemap = Column(Boolean, default=False)
    status = Column(String(20), default="pending")
    error_message = Column(Text)
    processing_progress = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_layer_org_name"),
        Index("idx_layer_status", "status"),
        Index("idx_layer_created", "created_at"),
        Index("idx_layer_org_status", "org_id", "status"),
        Index("idx_layer_org_category_status", "org_id", "category", "status"),
        CheckConstraint("layer_type IN ('vector', 'raster', 'tile')", name="ck_layer_type"),
        CheckConstraint("visibility IN ('org', 'public', 'private')", name="ck_layer_visibility"),
        CheckConstraint("status IN ('pending', 'processing', 'ready', 'error')", name="ck_layer_status"),
    )
    
    organization = relationship("Organization", backref="layers", lazy="selectin")
    creator = relationship("User", backref="layers", lazy="selectin")

class AnalysisTask(Base):
    """统一 durable job 表（ADR-0052）。

    原为「空间分析任务表」且无生产调用方。ADR-0052 把它演进成 Agent task /
    空间分析 job / Celery job 的统一持久化事实源，而不是新建第二套 Job 表 ——
    它已经具备 status CHECK、progress、retry_count、queued/started/completed
    时间戳、org/creator 归属与 JSON 载荷列，缺的只是关联与取消/租约字段。

    迁移：migrations/versions/0013_unified_durable_job_runtime.py（additive，
    老数据行不需要改写）。
    """
    __tablename__ = "analysis_tasks"
    
    # ADR-0052: SQLite 只把「INTEGER PRIMARY KEY」当 rowid 别名（即自增）；BIGINT 主键
    # 不自增，插入时会 NOT NULL 失败。durable job 现在是热路径，必须能在本地
    # SQLite 与生产 PostgreSQL 上都自增，故用 dialect variant。
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    # 归属由 creator_id + owner_token + session_id 三元组证明。
    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True)
    creator_id = Column(String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    layer_id = Column(BigInteger, ForeignKey("layers.id", ondelete="SET NULL"), nullable=True)
    result_layer_id = Column(BigInteger, ForeignKey("layers.id", ondelete="SET NULL"), nullable=True)
    task_type = Column(String(50), nullable=False)
    parameters = Column(JSON, nullable=False)
    celery_task_id = Column(String(100), unique=True)
    # 注意：不要在这里加 index=True —— __table_args__ 里已有 idx_task_status，
    # 两者会在 create_all 下生成两个内容相同的索引（写放大且无收益）。
    status = Column(String(20), default="pending")
    progress = Column(Integer, default=0)
    progress_message = Column(String(255))
    result_summary = Column(JSON)
    error_trace = Column(Text)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    queued_at = Column(DateTime)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # ── ADR-0052 统一 durable job 字段 ──────────────────────────────
    #: 执行域：agent / analysis / workflow / explorer（JobKind）
    job_kind = Column(String(20), nullable=True, default="analysis")
    #: 展示名。前端任务中心的标题，不含用户原文（避免 §35 的原文泄漏面扩大）
    display_name = Column(String(200), nullable=True)
    #: 会话归属。匿名会话的权限证明链：session_id → Conversation.owner_token
    session_id = Column(String(255), nullable=True)
    #: 匿名归属令牌（镜像 Conversation.owner_token 的 SEC-08 模式）
    owner_token = Column(String(64), nullable=True)
    project_id = Column(String(255), nullable=True)
    #: Agent 侧关联（形成 Agent Turn → Tool Step → Durable Job 链）
    run_id = Column(String(64), nullable=True)
    turn_id = Column(String(64), nullable=True)
    tool_call_id = Column(String(128), nullable=True)
    agent_task_id = Column(String(64), nullable=True)
    agent_step_id = Column(String(32), nullable=True)
    #: 幂等键。同一逻辑提交重复到达（SSE 重连 / 双击 / API retry）时复用同一行
    idempotency_key = Column(String(128), nullable=True, unique=True)
    #: 当前尝试序号（从 1 开始）。retry 创建新 attempt 而不是覆盖失败证据
    attempt = Column(Integer, default=1)
    #: 执行者标识（hostname:pid 或 celery worker 名），用于 stale 归因
    worker_id = Column(String(128), nullable=True)
    #: 取消请求的持久事实源 —— 进程重启不丢
    cancel_requested_at = Column(DateTime, nullable=True)
    #: worker 心跳。running 且心跳超时 → stale（规范 §25）
    heartbeat_at = Column(DateTime, nullable=True)
    #: 结果指针（artifact id / 存储路径），巨型结果不入 result_summary（规范 §38）
    result_ref = Column(String(512), nullable=True)
    #: 重跑描述符 {task, args, kwargs}。retry 靠它忠实重新入队 —— parameters 是
    #: 脱敏+截断后的展示摘要，无法用于重跑。写入前经敏感键脱敏与体积上限校验，
    #: 且**绝不**通过任何 API 返回（JobView 里没有这个字段）。
    dispatch_spec = Column(JSON, nullable=True)
    
    __table_args__ = (
        Index("idx_task_status", "status"),
        Index("idx_task_org_status", "org_id", "status"),
        Index("idx_task_org_type_status", "org_id", "task_type", "status"),
        # ADR-0052: 任务中心的三条主查询路径
        Index("idx_task_session_created", "session_id", "created_at"),
        Index("idx_task_creator_created", "creator_id", "created_at"),
        # SEC-08: 匿名归属路径（_ownership_predicate 的 OR 分支）—— 无索引会全表扫描
        Index("idx_task_owner_token", "owner_token"),
        Index("idx_task_status_heartbeat", "status", "heartbeat_at"),
        Index("idx_task_agent_task", "agent_task_id"),
        CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'cancelling', 'completed', 'failed', 'cancelled', 'stale')",
            name="ck_task_status",
        ),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_task_progress"),
    )

class LayerPermission(Base):
    """图层权限细粒度控制"""
    __tablename__ = "layer_permissions"
    
    id = Column(Integer, primary_key=True)
    layer_id = Column(BigInteger, ForeignKey("layers.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(255), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    permission = Column(String(20), nullable=False)
    granted_by = Column(String(255), ForeignKey("users.id", ondelete="SET NULL"))
    granted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime)
    
    __table_args__ = (
        UniqueConstraint("layer_id", "user_id", name="uq_permission"),
        CheckConstraint("permission IN ('read', 'write', 'admin')", name="ck_permission"),
    )
    
    layer = relationship("Layer", backref="permissions", lazy="selectin")
    user = relationship("User", foreign_keys=[user_id], lazy="selectin")

def get_init_sql():
    """获取 PostGIS 初始化 SQL"""
    return """
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS postgis_topology;
    """

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String(255), primary_key=True)
    # Nullable：兼容历史匿名会话；新认证会话写入 users.id；查询时按 owner 过滤
    user_id = Column(String(255), ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String(200), default="新对话")
    # SEC-08：匿名会话的 owner_token。仅新建的匿名会话会生成（非 NULL）。
    # NULL = grandfather（旧匿名会话，知道 session_id 即能力令牌）。
    # 认证会话从不依赖此列。访问带 token 的匿名会话需通过 X-Session-Token
    # 提供匹配值（见 history_service_async.get_session）。
    owner_token = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(255), ForeignKey("conversations.id", ondelete="CASCADE"))
    role = Column(String(20), nullable=False)  # user / assistant / tool
    content = Column(Text, default="")
    reasoning_content = Column(Text, nullable=True)  # reasoning/thinking process
    tool_calls = Column(JSON, nullable=True)  # FC tool calls
    tool_call_id = Column(String(255), nullable=True)
    tool_result = Column(JSON, nullable=True)  # tool execution result
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    conversation = relationship("Conversation", back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'tool')", name="ck_message_role"),
        # BUG-15: 按会话取消息列表（history load）几乎总是
        # WHERE conversation_id = ? ORDER BY created_at，原表无覆盖索引 → 全表扫描 + filesort。
        Index("idx_message_conversation_created", "conversation_id", "created_at"),
    )


class CartographyTemplate(Base):
    """地图制图模板表 - 支持 basemap / symbology / layout / thematic 四种类别"""
    __tablename__ = "cartography_templates"
    
    id = Column(String(255), primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True)
    creator_id = Column(String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    kind = Column(String(50), nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    category = Column(String(100), index=True)
    keywords = Column(JSON, nullable=False, default=list)
    description = Column(Text)
    payload = Column(JSON, nullable=False)
    is_builtin = Column(Boolean, nullable=False, default=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_template_builtin_kind", "is_builtin", "kind"),
        Index("idx_template_org_kind", "org_id", "kind"),
        CheckConstraint("kind IN ('basemap', 'symbology', 'layout', 'thematic')", name="ck_template_kind"),
    )

    organization = relationship("Organization", backref="templates", lazy="selectin")
    creator = relationship("User", backref="templates", lazy="selectin")


__all__ = ["Base", "Organization", "User", "Layer", "AnalysisTask", "LayerPermission", "Conversation", "Message", "CartographyTemplate", "get_init_sql"]