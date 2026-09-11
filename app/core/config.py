"""核心配置模块"""
import ipaddress
import logging
import re
import secrets
import warnings
from typing import List, Optional
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, model_validator

logger = logging.getLogger(__name__)


def _worker_env_file() -> str | None:
    """worker 子进程禁止读取 ``.env``（Round-2 审查 C-1）。

    worker 以 repo root 为 PYTHONPATH，pydantic-settings 的 ``.env`` 解析
    相对 CWD——不堵住该通道，扩展 worker 可经 ``app.core.config.settings``
    一次性读走全部宿主 secrets（伪造每扩展按 ref 供给的模型）。spawn 侧
    注入 ``WEBGIS_EXTENSION_WORKER=1`` 标记，类定义期即短路。
    """
    import os

    return None if os.environ.get("WEBGIS_EXTENSION_WORKER") == "1" else ".env"


class Settings(BaseSettings):
    """应用配置"""
    model_config = SettingsConfigDict(
        env_file=_worker_env_file(),
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

    # 测试阶段免登录开关：true 时所有受保护端点按 "test-admin"（admin 角色）
    # 放行，无需 Bearer token。仅限本地/测试环境；生产启动会打警告。
    AUTH_DISABLED: bool = False

    # 本地地理数据根目录：行政区 SHP（ChinaAdminDivisonSHP/）与 OSM 预处理
    # 产物（osm_gpkg/）从这里解析。空串回退仓库内 data/ 旧布局。
    LOCAL_GEODATA_DIR: str = ""
    # 远程地理工具出网前是否先查本地 SHP/GPKG。测试默认由 conftest 钉成
    # false，避免真实数据目录污染 Overpass/高德 mock。
    LOCAL_QUERY_FIRST: bool = True

    # ── 栅格运行时资源预算（Raster & Remote Sensing Runtime V3，ADR-0089）──
    # 单次窗口化栅格运算的工作内存预算（MB）。窗口边长由此推导：
    # cells ≈ budget / WORKING_BYTES_PER_CELL（64B：窗口化循环峰值约 8 个
    # float64/bool 工作数组），而不是拍脑袋的固定 512×512。保守默认。
    RASTER_PROCESSING_MEMORY_MB: int = 256
    # GDAL 块缓存上限（MB）——rasterio_env() 内生效；GDAL 缓存是进程内共享
    # 的，独立于窗口预算可调。
    RASTER_GDAL_CACHE_MAX_MB: int = 64
    # STAC API 基地址（app/services/rs/stac_client.py 的检索目录）。
    # 默认 earth-search；自建/私有 STAC 目录经 env 覆盖。
    STAC_API_URL: str = "https://earth-search.aws.element84.com/v1"

    def is_production(self) -> bool:
        """判断是否为生产环境"""
        return self.ENV.lower() == "production"

    # JWT
    JWT_SECRET_KEY: str = ""

    # 数据库
    DATABASE_URL: str = "sqlite:///./data/webgis.db"

    # ── 制图质量规则阈值（specs/cartographic-quality-rules-and-memory-spec）──
    # 默认值取制图学保守惯例。规则本体仍是纯函数：阈值经参数注入，规则内
    # 不读全局配置——否则规则就不再可单测/可复现。
    # 视口墨量负载（估计可见符号面积 / 视口面积）
    CARTO_LOAD_WARN_RATIO: float = 0.15
    CARTO_LOAD_FAIL_RATIO: float = 0.40
    # 图例相邻类感知色差 CIEDE2000
    CARTO_COLOR_SEP_WARN_DELTA_E: float = 10.0
    CARTO_COLOR_SEP_FAIL_DELTA_E: float = 5.0
    # 注记盒占视口比例
    CARTO_LABEL_WARN_RATIO: float = 0.10
    CARTO_LABEL_FAIL_RATIO: float = 0.25
    # 最小可视尺寸（0.4mm@96dpi ≈ 1.5px 边长 → 2.25px² 面积）
    CARTO_SVS_AREA_PX: float = 2.25
    # 单图层并置数据变量数（Bertin 过载）
    CARTO_VISUALVAR_WARN_COUNT: int = 3
    CARTO_VISUALVAR_FAIL_COUNT: int = 4
    # 分布漂移：分位向量相对偏差 / 空值率绝对变化
    CARTO_DRIFT_RELATIVE_THRESHOLD: float = 0.15
    CARTO_DRIFT_NULL_RATIO_THRESHOLD: float = 0.10

    # LLM 配置 (OpenAI 兼容接口)
    # 项目默认：阶跃 Step Plan 的 step-3.7-flash（推理模型，响应含
    # reasoning_content，正文在 content）。API key 只经环境变量注入，
    # 代码默认值保持占位符（生产模式启动校验会拒绝占位符）。
    LLM_BASE_URL: str = "https://api.stepfun.com/step_plan/v1"
    LLM_API_KEY: str = "your-api-key-here"
    LLM_MODEL: str = "step-3.7-flash"
    # 规划阶段专用模型；留空时回退 LLM_MODEL（便于以后单独配更便宜的模型）
    LLM_PLANNER_MODEL: str = ""
    LLM_PROMPT_CACHING_ENABLED: bool = True
    # audit4 #997: 采样/超时/预算参数进配置层（此前硬编码在 llm_client 与调用点，
    # 无法按角色调参，换供应商时 16384 可能超其上限直接 400）。
    LLM_TIMEOUT_S: float = 120.0        # 非流式请求超时；流式 read = max(180, 1.5×)
    LLM_MAX_TOKENS: int = 16384         # 执行角色默认输出预算
    LLM_TEMPERATURE: Optional[float] = None  # None = 不发送（用 provider 默认）
    # ADR-0101/0102：主模型上下文窗（tokens）。None/0 = 未知（预算器按 8k
    # 保守规划）；按模型精细值可配 MODEL_DESCRIPTORS_FILE 描述符。
    LLM_CONTEXT_WINDOW: Optional[int] = None
    # 标题/摘要等辅助任务的廉价模型；空回退 LLM_MODEL
    LLM_TITLE_MODEL: str = ""

    # OSM
    OVERPASS_API_URL: str = "https://overpass.openstreetmap.fr/api/interpreter"
    # E-9（#900）：运行期可覆盖的行为参数登记（各读取点保留 lazy env 读以
    # 兼容逐 case 重置的测试；登记目的是可发现/可审计/模板可见）。
    # 安全面：公开注册开关 —— 生产环境为 true 时下方 fail-fast 校验直接拒绝启动。
    ALLOW_PUBLIC_REGISTER: bool = False
    TOOL_TIMEOUT_S: float = 300.0
    SESSION_CACHE_SIZE: int = 200
    SESSION_MESSAGE_CAP: int = 200
    CLEAR_QUIESCE_TIMEOUT_S: float = 5.0
    CANCEL_WAIT_TIMEOUT_S: float = 5.0
    CHAT_MAX_ROUNDS: int = 60
    TURN_TOTAL_TIMEOUT_S: float = 900.0

    # ── GIS Extension Platform（ADR-0104）────────────────────────────
    # 默认全关：不配置 EXTENSIONS_DIRS 时启动行为与旧版完全一致。
    # EXTENSIONS_DIRS: os.pathsep 冒号分隔的扩展根目录列表（<root>/<ext>/manifest.json）。
    # EXTENSIONS_ALLOW/BLOCK: 逗号分隔的扩展 id 信任白/黑名单（block 优先）。
    # EXTENSIONS_BUILTIN_IDS: 随仓库发行的受信扩展包 id（逗号分隔）。
    # EXTENSION_PERMISSION_GRANTS: "id:perm1,perm2;id2:perm3" 形式的授权表。
    # EXTENSION_FEATURE_FLAGS / EXTENSION_SETTINGS_JSON: 按 extension id 的
    #   JSON object 覆盖（特性开关 / settings_schema 实例值）。
    EXTENSIONS_ENABLED: bool = False
    EXTENSIONS_DIRS: str = ""
    EXTENSIONS_ALLOW: str = ""
    EXTENSIONS_BLOCK: str = ""
    EXTENSIONS_BUILTIN_IDS: str = ""
    EXTENSION_PERMISSION_GRANTS: str = ""
    EXTENSIONS_ACTIVATE_UNTRUSTED: bool = False
    EXTENSION_FEATURE_FLAGS: str = "{}"
    EXTENSION_SETTINGS_JSON: str = "{}"
    # ── V2（ADR-0105）：隔离执行 / 供应链。默认全部关闭/为空 = V1 行为 ──
    # EXTENSION_SECRETS_JSON: {extension_id: {ref: value}}；供给即授权，
    #   值只经 broker 送达对应扩展，不进入状态/日志/LLM 可见面。
    EXTENSION_SECRETS_JSON: str = "{}"
    # EXTENSION_NETWORK_ALLOW: "id:host1,host2;id2:*" 形式的出网 allowlist
    #   （worker broker 的 network 能力默认 deny；按 host 匹配）。
    EXTENSION_NETWORK_ALLOW: str = ""
    # EXTENSION_ARTIFACT_ROOTS: os.pathsep 分隔的 artifact 根目录（worker
    #   broker 的 artifact_read/write 仅限根内路径；空 = 拒绝全部）。
    EXTENSION_ARTIFACT_ROOTS: str = ""
    # EXTENSION_TRUSTED_PUBLISHERS: "key_id:keyfile_path,..." 发布者密钥表。
    EXTENSION_TRUSTED_PUBLISHERS: str = ""
    # EXTENSIONS_TRUST_SIGNED: 验签通过且发布者受信 → 提权 trusted_extension。
    EXTENSIONS_TRUST_SIGNED: bool = False
    # EXTENSIONS_ALLOW_UNSIGNED_DEV: 未签名包的显式开发模式（大声告警）。
    EXTENSIONS_ALLOW_UNSIGNED_DEV: bool = False
    # EXTENSIONS_MAX_WORKER_CRASHES: worker 连续崩溃达到该值 → quarantine。
    EXTENSIONS_MAX_WORKER_CRASHES: int = 2

    # ── V3（ADR-0119）：非对称签名 / marketplace / 分发 / 强隔离。默认 ──
    # 全部关闭/为空 = V2 行为逐字节不变。
    # EXTENSION_TRUST_STORE_PATH: trust store JSON（发布者公钥/rotation/
    #   retired/revocation）。空 = 不启用 trust store（V2 HMAC 语义不变）。
    EXTENSION_TRUST_STORE_PATH: str = ""
    # EXTENSION_REGISTRY_DIR: 本地 registry 根（marketplace store；publish/
    # search/download 的服务端存储）。空 = marketplace 关闭。
    EXTENSION_REGISTRY_DIR: str = ""
    # EXTENSION_REGISTRY_URLS: 逗号分隔的远端 registry base URL（installer
    # 下载通道；每个 URL 都过核心 SSRF gate + allowlist）。
    EXTENSION_REGISTRY_URLS: str = ""
    # EXTENSIONS_INSTALL_ROOT: 分发安装根（active pack 目录 + versions/ +
    # .staging + .refresh 信号）。空 = 分发关闭。
    EXTENSIONS_INSTALL_ROOT: str = ""
    # EXTENSIONS_ISOLATION_BACKEND: worker 隔离后端（process = V2 语义；
    # bubblewrap = netns+ro-bind+tmpfs 的 namespace 级 OS 隔离；bwrap 不可用
    # 时 per-spawn typed 激活失败，绝不静默回退）。
    EXTENSIONS_ISOLATION_BACKEND: str = "process"
    # EXTENSION_VERSION_PIN: "id==1.2.0;id2==0.3.1" 版本钉（activate/upgrade/
    # install/rollback 预检统一消费；非 pin 版本 typed 拒绝）。
    EXTENSION_VERSION_PIN: str = ""
    # EXTENSIONS_KEEP_VERSIONS: versions/ 每扩展保留的历史版本数（含回滚余量）。
    EXTENSIONS_KEEP_VERSIONS: int = 3
    # EXTENSION_STREAM_WINDOW: V3 流式初始 credit 窗口。守序 worker 的
    # 宿主内存上界 ≈ window × execution.max_output_bytes；敌意 worker 由
    # 独立防线约束（reader 帧队列 maxsize=64 × 68MiB 帧硬顶 + 管道背压）。
    EXTENSION_STREAM_WINDOW: int = 16
    # EXTENSION_MAX_STREAM_EVENTS: V3 单次流事件数上界（结构性防无界流）。
    EXTENSION_MAX_STREAM_EVENTS: int = 10000

    # 仓内 vendor/pi 是默认 agent 宿主：API 启动即拉起 bundled RPC 子进程。
    # 测试套件在 conftest 钉 false，避免每个 TestClient 起 Node。
    # 紧急回退 ChatEngine：USE_NEW_AGENT=false。
    USE_NEW_AGENT: bool = True

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
    # 项目产物内容寻址存储根（ADR-0092 A3 artifact promotion）。空 = DATA_DIR/project_artifacts。
    PROJECT_ARTIFACT_CONTENT_DIR: str = ""

    # CORS
    # 审计 P2：默认改为 localhost:3000（Next.js dev server），而非 ["*"]。
    # 生产环境 validator 会强制要求显式 allow-list。
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # Celery & Redis
    REDIS_URL: str = "redis://localhost:16379/0"
    CELERY_BROKER_URL: str = "redis://localhost:16379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:16379/1"
    USE_REDIS: bool = True

    # #662：RAG embedding 模型离线加载（local_files_only）。开启后未缓存的
    # 首次加载秒级失败（有界），而不是无超时的 HF 下载把 to_thread worker
    # 线程挂死、连累进程优雅关停。默认 False：开发机首用自动下载行为不变；
    # 生产部署面显式打开，模型缓存预置方式见 docs/DEPLOYMENT.md。
    RAG_EMBEDDING_OFFLINE: bool = False

    # Geospatial Data Fabric resource guards (Section 70). Bounded hard limits so
    # one remote query (or a server that ignores `limit`) cannot OOM the process
    # or materialize an unbounded payload. Override per-env; values are clamped
    # by the limits module so an operator cannot disable protection by setting 0.
    DATA_FABRIC_MAX_FEATURES: int = 50_000
    DATA_FABRIC_MAX_RESPONSE_BYTES: int = 256 * 1024 * 1024  # 256 MiB
    DATA_FABRIC_MAX_PAGES: int = 200
    DATA_FABRIC_QUERY_TIMEOUT: float = 30.0       # seconds (connect+read budget per request)
    DATA_FABRIC_TOTAL_QUERY_TIMEOUT: float = 120.0  # seconds (whole multi-page operation)
    # Local-file adapter guard (Section 44). Comma-separated absolute/relative
    # roots that geoparquet/flatgeobuf/pmtiles local reads must stay within
    # (symlink-escape defense). Empty = no root enforcement, but sensitive
    # system dirs (/etc, /proc, …) are always blocked. Add DATA_DIR by default.
    DATA_FABRIC_LOCAL_FILE_ROOTS: str = "./data"
    DATA_FABRIC_LOCAL_FILE_MAX_BYTES: int = 1024 * 1024 * 1024  # 1 GiB
    # Catalog sync (Section 30): bounded concurrency for parallel dataset
    # describe() calls, clamped to [1, 16] so a 5000-dataset source no longer
    # serializes ~5000 remote round-trips.
    DATA_FABRIC_SYNC_CONCURRENCY: int = 4

    # ── Data Fabric V7 联邦数据面（ADR-0119）────────────────────────────
    # 全部有界：连接注册表 / capability 探测缓存 / 结果缓存 / 反馈持久层。
    DATA_FABRIC_V7_CONNECTION_MAX_ENTRIES: int = 1024
    DATA_FABRIC_V7_CONNECTION_IDLE_TTL_S: float = 1800.0
    DATA_FABRIC_V7_PROBE_TTL_S: float = 300.0
    DATA_FABRIC_V7_RESULT_CACHE_MAX_ENTRIES: int = 256
    DATA_FABRIC_V7_RESULT_CACHE_MAX_BYTES: int = 64 * 1024 * 1024  # 64 MiB
    DATA_FABRIC_V7_RESULT_CACHE_TTL_S: float = 300.0
    DATA_FABRIC_V7_FEEDBACK_MAX_ROWS: int = 20_000

    # ── Data Fabric V8 自适应联邦数据面（ADR-0132）──────────────────────
    # 引擎回退熔断：连续 V6 崩溃（非 typed 异常回退 V5）达阈值后，engine=v6
    # 请求在 cool_down 窗口内直接走 V5（双执行成本归零）；窗口后半开单
    # trial 探测恢复。进程级（引擎是进程资源，不是源资源）。
    DATA_FABRIC_V8_ENGINE_BREAKER_THRESHOLD: int = 3
    DATA_FABRIC_V8_ENGINE_BREAKER_COOLDOWN_S: float = 60.0
    # 结果缓存 stampede 保护 + 可选分布式二线（进程内 LRU 恒为 L1，后端故障
    # fail-open）。backend=redis 时经 REDIS_URL（或显式 URL）惰性连接。
    DATA_FABRIC_V8_RESULT_CACHE_SINGLEFLIGHT_WAIT_S: float = 10.0
    DATA_FABRIC_V8_RESULT_CACHE_BACKEND: str = "memory"
    DATA_FABRIC_V8_RESULT_CACHE_REDIS_URL: str = ""

    # #690：原生热力图确定性守卫阈值（点数 < 阈值或非点几何 → 拦截 native heatmap）
    # 对齐 skill 正文 "<10 点热力图无统计意义"，移至配置层恒生效；settings/env 可覆盖，
    # 复用仓内 config 惯例。max(1,) 防零在读取侧 clamp，字段层仅定义默认值。
    HEATMAP_MIN_POINTS: int = 10

    # Chat 引擎：连续“无进展”轮次的熔断阈值（#685）
    # 定义：当连续 N 轮工具调用既未产生新的成功结果也未推进计划进度时，判定为无进展循环。
    # 具体实现中以 consecutive_no_progress 计数器跟踪，连续 N 轮满足「全失败/重复/错误」
    # 或「无工具调用但内容为空」即触发熔断，提前结束 max_rounds 循环并以 FAILED/no_progress
    # 诚实 settle。可被环境变量 LLM_NO_PROGRESS_THRESHOLD 覆盖，便于测试与运维调参。
    LLM_NO_PROGRESS_THRESHOLD: int = 3

    # 代理设置
    HTTP_PROXY: Optional[str] = None
    HTTPS_PROXY: Optional[str] = None

    @field_validator("LLM_TEMPERATURE", mode="before")
    @classmethod
    def _empty_temperature_is_none(cls, v):
        """Optional[float] 的 env 字符串没有 None 形态——模板留空（""）映射回 None，
        与 LLM_TITLE_MODEL / LLM_PLANNER_MODEL 的留空语义对齐。"""
        if v == "":
            return None
        return v

    @field_validator("LLM_CONTEXT_WINDOW", mode="before")
    @classmethod
    def _empty_context_window_is_none(cls, v):
        """Optional[int] 同一空串语义（.env.example 留空 = None = 服务端默认/
        预算器保守 8k）—— 缺这个 validator 时空模板直接让 Settings 解析失败。"""
        if v == "" or v is None:
            return None
        return v

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
            # CONFIG-01：生产环境禁止 sqlite —— 文件型 DB 无并发/无 HA，且
            # 默认 ./data/webgis.db 在容器内不可靠。必须用 Postgres。
            if not self.DATABASE_URL.startswith(("postgresql://", "postgres://")):
                raise RuntimeError(
                    "DATABASE_URL must use PostgreSQL in production "
                    "(e.g. postgresql://user:pass@host/db). "
                    f"Current value does not start with postgresql:// or postgres://: "
                    f"'{self.DATABASE_URL[:60]}...'."
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
    def _validate_auth_disabled(self) -> "Settings":
        """审计 #756：AUTH_DISABLED=true 是完整认证旁路（含 require_admin），
        必须像 JWT_SECRET_KEY / CORS['*'] / SQLite-in-prod 一样 fail-loud。
        一个从本地测试复制到生产的 .env 不应静默打开所有端点。"""
        if self.is_production() and self.AUTH_DISABLED:
            raise RuntimeError(
                "AUTH_DISABLED=true is not allowed in production — it disables all "
                "authentication including admin endpoints. Remove it from the "
                "environment."
            )
        return self

    @model_validator(mode="after")
    def _validate_external_urls(self) -> "Settings":
        """验证外部 URL 配置，防止 SSRF 攻击。

        审计 P1：之前只对 *非默认值* 做校验，若攻击者通过环境变量注入
        覆盖默认 URL（如 LLM_BASE_URL=https://evil.com），SSRF 校验被完全绕过。
        现在对所有 URL 统一校验，默认值也不例外。
        #925: LLM_BASE_URL 允许企业内网/集群内私网地址，仅做轻量校验。
        """
        for attr in ("OVERPASS_API_URL", "NOMINATIM_URL"):
            url = getattr(self, attr)
            self._validate_no_ssrf(url, field=attr)
        self._validate_no_ssrf(getattr(self, "LLM_BASE_URL"), field="LLM_BASE_URL", allow_private=True)
        return self

    @staticmethod
    def _validate_no_ssrf(url: str, field: str = "URL", allow_private: bool = False) -> None:
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

        # 阻止本地回环（#925: LLM 内网豁免）
        if not allow_private and hostname in ("localhost", "127.0.0.1", "::1"):
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

        # 尝试解析 hostname → IP，检查是否为私有地址（#925: LLM 豁免）
        if allow_private:
            return
        try:
            addr = ipaddress.ip_address(hostname)
            if not addr.is_global or addr.is_private or addr.is_loopback or addr.is_link_local:
                # is_global 语义与错误消息一致（"Only public IPs"）：覆盖
                # is_private 词表不包含的保留段（100.64/10 CGNAT、IPv6 ULA、
                # benchmark/reserved 段）。
                raise ValueError(
                    f"{field}='{url}' resolves to private/loopback IP {addr}. "
                    f"Only public IPs are allowed."
                )
        except ValueError as exc:
            # 私有/回环 IP 字面量的拒绝必须重新抛出（#1214：原哨兵
            # "is not allowed" 与实际消息 "Only public IPs are allowed."
            # 不匹配，第一层防线被吞，仅靠后续 regex/DNS 层兜底）。
            # ip_address() 对非 IP 字符串（域名）抛的 ValueError 则继续走域名分支。
            if "Only public IPs are allowed" in str(exc):
                raise
            # 域名：做基本黑名单检查
            _BLOCKED_DOMAIN_PATTERNS = [
                r"^169\.254",              # link-local
                r"^10\.",                  # 10.0.0.0/8
                r"^172\.(1[6-9]|2\d|3[01])\.",  # 172.16.0.0/12
                r"^192\.168\.",            # 192.168.0.0/16
                r"^127\.",                 # loopback
                r"^metadata\.",            # 元数据服务 (FQDN only)
                r"\.internal\.?$",         # k8s internal (.internal FQDN)
            ]
            host_lower = hostname.lower()
            for pat in _BLOCKED_DOMAIN_PATTERNS:
                if re.match(pat, host_lower):
                    raise ValueError(
                        f"{field}='{url}' uses blocked domain pattern '{pat}'."
                    )

            # 审计 SEC-07：DNS 解析 — 阻止域名解析到私有 IP。
            # 之前只做字符串模式匹配，攻击者可用解析到 169.254.169.254 的域名绕过。
            # 注意：这是静态检查（validate 时解析一次）。真正的 DNS rebinding TOCTOU
            # 需在 HTTP client 层 pin IP（aiohttp custom connector），是独立大工作。
            import socket
            try:
                infos = socket.getaddrinfo(hostname, None)
                for family, _, _, _, sockaddr in infos:
                    ip_str = sockaddr[0]
                    try:
                        resolved_ip = ipaddress.ip_address(ip_str)
                    except ValueError:
                        continue
                    if resolved_ip.is_global:
                        continue
                    # 非 global 解析结果（private/loopback/link-local/multicast/
                    # CGNAT/保留段）：除非在 fake-IP DNS 豁免清单内，否则拒绝。
                    # 豁免清单（#1204）：Clash/VpnKit 类解析器把所有外部域名
                    # 映射进 198.18.0.0/15 保留段，构造期硬失败会让应用/质量
                    # 闸脚本在合法开发环境无法启动。清单覆盖 Settings 默认值
                    # 与 tests/conftest 基线值所用的 OSM 域（默认
                    # overpass.openstreetmap.fr、conftest 基线 overpass-api.de），
                    # 保持与既有 nominatim/tile 先例同族。真实 SSRF 防线在请求层
                    # （DataFabricSecurity pin + aiohttp connector）。
                    if hostname in {
                        "nominatim.openstreetmap.org",
                        "tile.openstreetmap.org",
                        "overpass-api.de",
                        "overpass.openstreetmap.fr",
                        "overpass.kumi.systems",
                        "data.beijing.gov.cn",
                        "data.sh.gov.cn",
                        "gddata.gd.gov.cn",
                    }:
                        continue
                    raise ValueError(
                        f"{field}='{url}' hostname '{hostname}' resolves to "
                        f"private/reserved IP {resolved_ip}. Blocked (SSRF)."
                    )
            except socket.gaierror:
                # DNS 解析失败 — 不阻断（可能是开发环境临时域名），
                # 但 log warning 让运维知道。
                import logging
                logging.getLogger(__name__).warning(
                    "SEC-07: DNS resolution failed for %s in %s — "
                    "cannot verify SSRF safety, allowing with warning",
                    hostname, field,
                )


    @model_validator(mode="after")
    def _validate_allow_public_register_prod(self) -> "Settings":
        """E-9（#900）：安全开关终审 —— 生产禁止开放注册。

        独立且最后定义的 validator（pydantic 按定义顺序执行）：确保
        CORS/AUTH_DISABLED 等既有生产检查先有机会报出它们更具体的错误，
        本守卫只兜"其余全合法但开放注册"的组合。
        """
        if self.ALLOW_PUBLIC_REGISTER and self.is_production():
            raise RuntimeError(
                "ALLOW_PUBLIC_REGISTER=true is forbidden in production "
                "(use manage.py create-admin instead)"
            )
        return self


settings = Settings()
