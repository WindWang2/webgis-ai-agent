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


class GeoComputeNodeResult(Base):
    """GeoCompute V5 跨进程 checkpoint 复用索引（audit 06 §6.1 step 3）。

    durable 节点完成后把 ``{owner_scope, node_semantic_fingerprint,
    result_ref, session_id, upstream_fingerprints, created_at}`` 记入本表：
    下一次同 owner 同语义指纹的 durable 节点派发**之前**，执行器先查本表
    （上游输出指纹一致 + result_ref 仍可解析才复用），命中即跳过派发。

    边界（诚实声明）：
    - 这是**缓存索引**，不是第二任务状态机 —— 只在节点完成后 upsert 一次、
      无状态迁移；job 真相仍在 analysis_tasks（单一任务真相不变）。
    - ``result_ref`` 是 session ref；``session_id`` 随行存储（解析 ref 必需）。
    - owner 域沿用 ``executor.owner_scope_for`` 的哈希域（绝不明文身份，
      绝不跨 owner 共享）。
    - 有界：每 owner 仅保留最近 64 条（写入时按 created_at LRU 剪枝）；
      (owner_scope, node_fingerprint) 唯一 —— 重写即刷新。
    - 读取方 fail-open：表缺失/DB 不可用 → 视为未命中，重算（诚实但变慢）。
    """
    __tablename__ = "geocompute_node_results"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    #: executor.owner_scope_for 输出（"u:<hash>" / "s:<hash>" / "anonymous"）
    owner_scope = Column(String(40), nullable=False)
    #: 节点语义指纹（plan.ExecutionNode.semantic_fingerprint，16 hex）
    node_fingerprint = Column(String(32), nullable=False)
    #: session ref 指针（解析会话存储里的特征载荷）
    result_ref = Column(String(512), nullable=False)
    #: ref 所属会话（session_data_manager.get(session_id, ref) 必需）
    session_id = Column(String(255), nullable=False)
    #: 完成时的上游输出指纹 {node_id: fp} —— 复用前的陈旧校验依据
    upstream_fingerprints = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("uq_gc_node_result_owner_fp", "owner_scope", "node_fingerprint", unique=True),
        Index("idx_gc_node_result_owner_created", "owner_scope", "created_at"),
    )


class GeoComputeRunEvidence(Base):
    """GeoCompute V5 run 终态证据快照（audit 06 §6.1 step 2）。

    run 进入终态时把**有界（≤16KB）**的 ExecutionRun 摘要（status/evidence，
    绝无载荷）以 run_id 为主键写入一行；进程重启后 ``get_run`` 内存未命中
    时按 owner 域校验读本表回放，REST 读取不再 404。

    边界：append-once/upsert-on-terminal 的只读证据表 —— 没有状态迁移、
    不参与调度决策，绝不是第二 run 注册表（进程内注册表仍是运行期真相）。
    """
    __tablename__ = "geocompute_run_evidence"

    #: run id（"gexec-<hex12>"）
    run_id = Column(String(64), primary_key=True)
    #: owner 域（owner_scope_for）；读取侧按它做隔离，他人一律 404
    owner_scope = Column(String(40), nullable=False)
    #: 终态：completed | failed | cancelled
    status = Column(String(20), nullable=False)
    #: 有界快照 JSON（≤16KB；{run, evidence, truncated?}）
    snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("idx_gc_run_evidence_owner", "owner_scope"),
    )


class GeoComputeClusterRun(Base):
    """GeoCompute V6 run 级持久生命周期（cluster control plane 真相）。

    V5 的 run 注册表是进程内存态（executor._runs）—— 多副本下 cancel/
    get_run 只见本进程，进程崩溃后在飞 run 无任何记录。本表给 run 获得
    job 级（analysis_tasks）早已具备的语义：持久状态、lease/epoch fencing、
    心跳、取消旗标、attempt 计数。

    单一事实源边界（01-architecture.md）：
    - 本表 = run **生命周期**真相（状态/lease/取消/优先级/plan 快照）；
    - 节点 job 真相仍 = ``analysis_tasks``（绝不建第二任务状态机）；
    - 终态证据仍 = ``geocompute_run_evidence``（本表不复制 snapshot，
      ``terminal`` 后读取侧按 run_id 关联证据表）；
    - 载荷 = session ref（绝无 features/rows 进 DB 行）。
    """
    __tablename__ = "geocompute_runs"

    #: 自增提交序号（SQLite 需要 INTEGER PK 才有 autoincrement；fairness
    #: 的 submit seq / 列表排序都以它为准 —— 单调、无墙钟歧义）。
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    #: run id（"gexec-<hex12>"，与 engine 内存注册表/证据表同一词表）
    run_id = Column(String(64), nullable=False)
    #: owner 域（executor.owner_scope_for）；读隔离与证据表同一纪律
    owner_scope = Column(String(40), nullable=False)
    #: cluster 状态机（cluster.contracts.ClusterRunStatus 词表）
    status = Column(String(20), nullable=False, default="queued")
    #: 计划指纹（graph_fingerprint）
    plan_fingerprint = Column(String(32), nullable=False)
    #: plan JSON 快照（ExecutionPlan.model_dump；≤256KB 上界在写入侧强制，
    #: coordinator 崩溃后据此重建计划 —— 恢复的唯一输入）
    plan_snapshot = Column(JSON, nullable=False)
    session_id = Column(String(255), nullable=True)
    #: 身份域哈希（tenant ← org_id、project ← project_id；owner_scope 同款
    #: 哈希域 —— 绝不明文身份入集群控制面）
    tenant_key = Column(String(40), nullable=True)
    project_key = Column(String(40), nullable=True)
    #: 优先级（RunPriority 词表 0/5/10；抢占与公平排序键）
    priority = Column(Integer, nullable=False, default=5)
    #: lease 认领次数（reclaim 上界 DEFAULT_MAX_RUN_ATTEMPTS）
    #: lease 丢失（reclaim）次数（上界 DEFAULT_MAX_RUN_ATTEMPTS）
    attempts = Column(Integer, nullable=False, default=0)
    #: 被抢占次数（livelock 保险丝 MAX_PREEMPTS）
    preempts = Column(Integer, nullable=False, default=0)
    #: fencing epoch（每次认领 +1；所有写路径 CAS 校验）
    lease_epoch = Column(Integer, nullable=False, default=0)
    coordinator_id = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    #: 取消请求的持久事实源（任意进程可写；与 analysis_tasks.cancel_requested_at 同模式）
    cancel_requested_at = Column(DateTime, nullable=True)
    #: 抢占请求旗标（跨 coordinator；执行侧心跳循环观察后在节点边界让出）
    yield_requested_at = Column(DateTime, nullable=True)
    #: 最近一次派发序号（= 本行 id；fairness 的租户轮转状态可由
    #: MAX(dispatch_seq) GROUP BY tenant 重建 —— coordinator 无隐藏内存态）
    dispatch_seq = Column(Integer, nullable=True)
    #: 集群账本预留（reclaim 时按此精确归还 —— 与状态转移同事务）
    reserved_rows = Column(Integer, nullable=False, default=0)
    reserved_bytes = Column(Integer, nullable=False, default=0)
    reserved_units = Column(Integer, nullable=False, default=0)
    #: 必需 profile 通道（["raster","heavy_cpu"]）；能力匹配的依据
    required_profiles = Column(JSON, nullable=True)
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    started_at = Column(DateTime, nullable=True)
    terminal_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", name="uq_gc_run_run_id"),
        CheckConstraint(
            "status IN ('queued','leased','running','completed','failed',"
            "'cancelled','preempted')",
            name="ck_gc_run_status",
        ),
        CheckConstraint("priority IN (0,5,10)", name="ck_gc_run_priority"),
        Index("idx_gc_run_owner_id", "owner_scope", "id"),
        Index("idx_gc_run_status_priority", "status", "priority"),
        Index("idx_gc_run_lease_expiry", "status", "lease_expires_at"),
        Index("idx_gc_run_tenant_dispatch", "tenant_key", "dispatch_seq"),
    )


class GeoComputeClusterWorker(Base):
    """GeoCompute V6 worker/coordinator 注册表（心跳 + 能力 + leadership）。

    coordinator 行复用本表（role='coordinator'）：leadership 就是它身上的
    lease epoch CAS —— 任一时刻仅一个 coordinator 持有调度权（脑裂时旧者
    的 CAS 全部失败）。celery worker 行声明其消费的 profile 队列与槽位，
    scheduler 据此做能力匹配（无对应通道的 run 留队，消除队头阻塞）。
    """
    __tablename__ = "geocompute_workers"

    worker_id = Column(String(128), primary_key=True)
    role = Column(String(20), nullable=False, default="worker")
    #: {profile: slots}（coordinator 为空 dict）
    profiles = Column(JSON, nullable=True)
    heartbeat_at = Column(DateTime, nullable=False)
    lease_epoch = Column(Integer, nullable=False, default=0)
    lease_expires_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    info = Column(JSON, nullable=True)

    __table_args__ = (
        CheckConstraint("role IN ('coordinator','worker')", name="ck_gc_worker_role"),
        Index("idx_gc_worker_role_heartbeat", "role", "heartbeat_at"),
    )


class GeoComputeResourceUsage(Base):
    """GeoCompute V6 集群资源账本（tenant/project/global 三级，run 粒度）。

    记账全部是**单语句条件 UPDATE**（SQLite/PG 同语义）：
    - reserve：``usage + Δ <= limit`` 才生效（rowcount=0 → 拒绝）；
    - release：``MAX(0, usage - Δ)`` 钳零（与 reclaim 同事务，CAS 保证
      exactly-once）。
    ``limit`` 列 NULL = 不设限。这是集群层准入（防止 N coordinator 各自
    L1 governor 叠加成 N 倍全局限额）；进程内 L1 树（budgets.ResourceGovernor）
    在任何模式下仍是执行进程的权威 —— 两层各管一个爆炸半径。
    """
    __tablename__ = "geocompute_resource_usage"

    #: "global" | "t:<hash12>" | "p:<hash12>"
    scope_key = Column(String(80), primary_key=True)
    usage_rows = Column(Integer, nullable=False, default=0)
    usage_bytes = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    usage_units = Column(Integer, nullable=False, default=0)
    limit_rows = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=True)
    limit_bytes = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=True)
    limit_units = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


__all__ = ["Base", "Organization", "User", "Layer", "AnalysisTask", "LayerPermission", "Conversation", "Message", "CartographyTemplate", "GeoComputeNodeResult", "GeoComputeRunEvidence", "GeoComputeClusterRun", "GeoComputeClusterWorker", "GeoComputeResourceUsage", "get_init_sql"]