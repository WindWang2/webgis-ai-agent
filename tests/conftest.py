import os

# 测试套件环境基线（#663-B）：把 .env.example 全部键 setdefault 预占与
# Settings 默认等价的安全值 —— 完整性由 tests/unit/test_env_hygiene.py 锁定。
#   - CI 各 lane 在 pytest 启动前显式导出的变量不受 setdefault 影响
#     （real-services lane 的 REDIS_URL/DATABASE_URL、主 lane 的 env 原样生效）；
#   - 本地 shell 里导出的真实键（真 API key / 真 Redis / 真 DATABASE_URL）
#     不再能改变套件行为：脏机器等价于干净机器；
#   - HTTP_PROXY/HTTPS_PROXY 是唯二不钉的键：空串在 httpx/requests 语义里
#     不等于"未设置"，钉 "" 反而改变网络行为。
#
# 历史注记：CELERY_* 钉扎最初是防 import app.main 执行 load_dotenv() 把
# .env 的 CELERY_BROKER_URL/CELERY_RESULT_BACKEND 注进 os.environ（Celery
# conf 的 property 每次访问先读环境变量，eager 测试进程会去连 localhost
# 的 Redis 并失败）。#663-A 把 env 加载上移到启动器后，import app 代码不再
# 改写 os.environ（tests/unit/test_env_hygiene.py 的 import 纯净测试锁死），
# 钉扎继续对 shell 环境生效。
_ENV_BASELINE = {
    "DEBUG": "false",
    "ENV": "development",
    "JWT_SECRET_KEY": "test-secret-key-not-for-production",
    "DATABASE_URL": "sqlite:///./data/webgis.db",
    "DB_PASSWORD": "",
    "REDIS_PASSWORD": "",
    "LLM_BASE_URL": "https://api.stepfun.com/step_plan/v1",
    "LLM_API_KEY": "your-api-key-here",
    "LLM_MODEL": "step-3.7-flash",
    "TIANDITU_TOKEN": "",
    "AMAP_API_KEY": "",
    "AMAP_JS_KEY": "",
    "AMAP_JS_SECURITY_KEY": "",
    "BAIDU_MAP_AK": "",
    "BAIDU_QIANFAN_TOKEN": "",
    "SENTINELHUB_CLIENT_ID": "",
    "SENTINELHUB_CLIENT_SECRET": "",
    "NASA_EARTHDATA_USERNAME": "",
    "NASA_EARTHDATA_PASSWORD": "",
    "OPENTOPOGRAPHY_API_KEY": "",
    # 等于 Settings 默认（redis://localhost:16379/0）。USE_REDIS=false 钉扎
    # 保证主消费者（session_data）走内存实现；懒连接消费者都有有界超时。
    "REDIS_URL": "redis://localhost:16379/0",
    # 强于 Settings 默认的离线钉扎（历史遗留，见上方注记）：eager + memory。
    "CELERY_BROKER_URL": "memory://",
    "CELERY_RESULT_BACKEND": "cache+memory://",
    "USE_REDIS": "false",
    "WEBGIS_DEV_MOUNT": "",
    "AUTH_DISABLED": "false",
    "LOCAL_GEODATA_DIR": "",
    "LOCAL_QUERY_FIRST": "true",
    "RAG_EMBEDDING_OFFLINE": "false",
        # E-4/E-9（#895/#900）：.env.example 新登记键的钉扎（与 Settings 默认等价）
        "LLM_PLANNER_MODEL": "",
        "LLM_PROMPT_CACHING_ENABLED": "true",
        "LLM_NO_PROGRESS_THRESHOLD": "3",
        # audit4 #997：采样/超时/预算参数（LLM_TEMPERATURE 空串经 field_validator 映射回 None）
        "LLM_TIMEOUT_S": "120.0",
        "LLM_MAX_TOKENS": "16384",
        "LLM_TEMPERATURE": "",
        "LLM_TITLE_MODEL": "",
        # Settings 默认 None（空串经 field_validator 折回 None = 服务端默认）
        "LLM_CONTEXT_WINDOW": "",
        # AC-01（ADR-0150）：意图证据置信度权重 / 澄清阈值 / 实体服务开关
        "INTENT_CONF_W_TASK": "0.40",
        "INTENT_CONF_W_SLOTS": "0.25",
        "INTENT_CONF_W_ENTITY": "0.20",
        "INTENT_CONF_W_SESSION": "0.15",
        "INTENT_CLARIFY_CONFIDENCE_FLOOR": "0.55",
        "INTENT_ENTITY_SERVICE": "true",
        "MAPBOX_TOKEN": "",
        "BING_MAP_KEY": "",
        "TENCENT_MAP_KEY": "",
        "NOMINATIM_URL": "https://nominatim.openstreetmap.org/search",
        "OVERPASS_API_URL": "https://overpass-api.de/api/interpreter",
        "HEATMAP_MIN_POINTS": "10",
        "CARTO_LOAD_WARN_RATIO": "0.15",
        "CARTO_LOAD_FAIL_RATIO": "0.40",
        "CARTO_LABEL_WARN_RATIO": "0.10",
        "CARTO_LABEL_FAIL_RATIO": "0.25",
        "CARTO_COLOR_SEP_WARN_DELTA_E": "10.0",
        "CARTO_COLOR_SEP_FAIL_DELTA_E": "5.0",
        "CARTO_VISUALVAR_WARN_COUNT": "3",
        "CARTO_VISUALVAR_FAIL_COUNT": "4",
        "CARTO_SVS_AREA_PX": "2.25",
        "CARTO_DRIFT_RELATIVE_THRESHOLD": "0.15",
        "CARTO_DRIFT_NULL_RATIO_THRESHOLD": "0.10",
        "MAP_QUALITY_GATE_MODE": "enforce",
        "MAP_QUALITY_GATE_MAX_FEATURES": "5000",
        "DATA_FABRIC_QUERY_TIMEOUT": "30.0",
        "DATA_FABRIC_TOTAL_QUERY_TIMEOUT": "120.0",
        "DATA_FABRIC_MAX_PAGES": "200",
        "DATA_FABRIC_MAX_RESPONSE_BYTES": "268435456",
        "DATA_FABRIC_MAX_FEATURES": "50000",
        "DATA_FABRIC_SYNC_CONCURRENCY": "4",
        "DATA_FABRIC_LOCAL_FILE_ROOTS": "",
        "DATA_FABRIC_LOCAL_FILE_MAX_BYTES": "1073741824",
        # Data Fabric V7（ADR-0115）：连接池与结果缓存
        "DATA_FABRIC_V7_CONNECTION_MAX_ENTRIES": "1024",
        "DATA_FABRIC_V7_CONNECTION_IDLE_TTL_S": "1800.0",
        "DATA_FABRIC_V7_PROBE_TTL_S": "300.0",
        "DATA_FABRIC_V7_RESULT_CACHE_MAX_ENTRIES": "256",
        "DATA_FABRIC_V7_RESULT_CACHE_MAX_BYTES": "67108864",
        "DATA_FABRIC_V7_RESULT_CACHE_TTL_S": "300.0",
        "DATA_FABRIC_V7_FEEDBACK_MAX_ROWS": "20000",
        # Data Fabric V8（ADR-0132）：引擎熔断 + 结果缓存后端
        "DATA_FABRIC_V8_ENGINE_BREAKER_THRESHOLD": "3",
        "DATA_FABRIC_V8_ENGINE_BREAKER_COOLDOWN_S": "60.0",
        "DATA_FABRIC_V8_RESULT_CACHE_SINGLEFLIGHT_WAIT_S": "10.0",
        "DATA_FABRIC_V8_RESULT_CACHE_BACKEND": "memory",
        "DATA_FABRIC_V8_RESULT_CACHE_REDIS_URL": "",
        "DATA_DIR": "./data",
        "TMP_DIR": "./tmp",
        "PROJECT_ARTIFACT_CONTENT_DIR": "",
        "TOOL_TIMEOUT_S": "300",
        "SESSION_CACHE_SIZE": "200",
        "SESSION_MESSAGE_CAP": "200",
        # ADR-0119 ModelOps 旋钮（与 app/services/modelops/config.py 默认等价）
        "MODELOPS_REGISTRY_DIR": "",
        "MODELOPS_MAX_LOADED_MODELS": "4",
        "MODELOPS_VRAM_BUDGET_BYTES": "8589934592",
        "MODELOPS_MAX_CONCURRENT_INFERENCES": "2",
        "MODELOPS_INFERENCE_DEADLINE_S": "600",
        "MODELOPS_REUSE_MAX_ENTRIES": "128",
        "MODELOPS_REUSE_MAX_BYTES": "2147483648",
        "MODELOPS_REMOTE_ALLOWLIST": "",
        # V3 §B：子进程 worker 通道默认关闭（钉扎空 = 无 allowlist）。
        "MODELOPS_SUBPROCESS_WORKERS": "",
        "MODELOPS_SUBPROCESS_DEADLINE_S": "120",
        # V3 §D：PostGIS 矢量发布通道默认关闭（GeoJSON 兜底不受影响）。
        "MODELOPS_POSTGIS_DSN": "",
        # V3 §E：warm pool 默认空（无启动常驻加载）。
        "MODELOPS_WARM_POOL": "",
        "RASTER_PROCESSING_MEMORY_MB": "256",
        "RASTER_GDAL_CACHE_MAX_MB": "64",
        "CLEAR_QUIESCE_TIMEOUT_S": "5.0",
        "CANCEL_WAIT_TIMEOUT_S": "5.0",
        "CHAT_MAX_ROUNDS": "60",
        "TURN_TOTAL_TIMEOUT_S": "900",
        # 测试钉 false：生产/dev 默认 true（仓内 vendor/pi）。pytest 不得
        # 每个 TestClient lifespan 拉起 Node 子进程。
        "USE_NEW_AGENT": "false",
        # GIS Extension Platform（ADR-0104）——与 Settings 默认等价的
        # 安全值（默认全关；空白名单/黑名单/授权表；空目录）。
        "EXTENSIONS_ENABLED": "false",
        "EXTENSIONS_DIRS": "",
        "EXTENSIONS_ALLOW": "",
        "EXTENSIONS_BLOCK": "",
        "EXTENSIONS_BUILTIN_IDS": "",
        "EXTENSION_PERMISSION_GRANTS": "",
        "EXTENSIONS_ACTIVATE_UNTRUSTED": "false",
        "EXTENSION_FEATURE_FLAGS": "{}",
        "EXTENSION_SETTINGS_JSON": "{}",
        # ADR-0105 V2：与 Settings 默认等价（供应链/隔离配置全部关闭/空）。
        "EXTENSION_SECRETS_JSON": "{}",
        "EXTENSION_NETWORK_ALLOW": "",
        "EXTENSION_ARTIFACT_ROOTS": "",
        "EXTENSION_TRUSTED_PUBLISHERS": "",
        "EXTENSIONS_TRUST_SIGNED": "false",
        "EXTENSIONS_ALLOW_UNSIGNED_DEV": "false",
        "EXTENSIONS_MAX_WORKER_CRASHES": "2",
        # ADR-0109 V3：分发市场、版本钉扎与流式窗口
        "EXTENSION_TRUST_STORE_PATH": "",
        "EXTENSION_REGISTRY_DIR": "",
        "EXTENSION_REGISTRY_URLS": "",
        "EXTENSIONS_INSTALL_ROOT": "",
        "EXTENSIONS_ISOLATION_BACKEND": "process",
        "EXTENSION_VERSION_PIN": "",
        "EXTENSIONS_KEEP_VERSIONS": "3",
        "EXTENSION_STREAM_WINDOW": "16",
        "EXTENSION_MAX_STREAM_EVENTS": "10000",
        "STAC_API_URL": "https://earth-search.aws.element84.com/v1",
        # Spatial Lakehouse V6（ADR-0118）：对象存储后端选择（默认 filesystem
        # = 行为零变化）。s3 凭据钉空串 —— 测试进程绝不持有真实 secret。
        "WEBGIS_OBJECT_STORE_BACKEND": "filesystem",
        "WEBGIS_S3_ENDPOINT_URL": "",
        "WEBGIS_S3_BUCKET": "",
        "WEBGIS_S3_REGION": "",
        "WEBGIS_S3_ACCESS_KEY_ID": "",
        "WEBGIS_S3_SECRET_ACCESS_KEY": "",
        "WEBGIS_S3_PREFIX": "",
        # V9 错误信封（ADR-0138）：测试套件默认统一信封（与 Settings 默认
        # 等价）；legacy 回退由显式设置该变量的专项测试自行 monkeypatch。
        "LEGACY_DETAIL_ENVELOPE": "false",
}
for _key, _value in _ENV_BASELINE.items():
    os.environ.setdefault(_key, _value)

import pytest


@pytest.fixture(autouse=True)
def _pin_auth_bypass_off(monkeypatch):
    """认证测试套件对 AUTH_DISABLED 环境开关免疫。

    本地 .env 在测试阶段可能开着免登录（AUTH_DISABLED=true）；存量认证
    测试断言的是真实 401/403 行为，不能被环境开关污染。默认钉死关闭；
    需要旁路的测试（tests/test_auth_bypass.py）在自己的 fixture 里显式
    monkeypatch 打开（autouse 先行设置，后设者胜出，teardown 反序恢复）。

    settings 惰性导入：conftest 顶层 import 会提前固化配置单例，抢在
    测试模块自己的 env 布置之前。
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "AUTH_DISABLED", False, raising=False)
    # 真实 LOCAL_GEODATA_DIR 会让远程 OSM/高德工具先走本地 GPKG，打穿 mock。
    monkeypatch.setattr(settings, "LOCAL_QUERY_FIRST", False, raising=False)


def pytest_collection_modifyitems(config, items):
    """#664：perf 基准只在显式 `-m perf`（或含 perf 的选择式）时执行。

    perf 基线的契约是隔离运行（CI 专属 test-perf lane、基准文件 docstring
    的 `-m perf` 用法）。无 marker 过滤的本地全量跑会把 perf 项混在 ~4500
    个测试中段执行 —— 堆积累 + 机器负载相位使 median 超基线（同机三次实测
    0/4/7 failed，干净 master 最差）。未选择 perf 时给 perf 项追加**可见
    skip** 并教学正确命令：确定性 skip 代替非确定性红。

    markexpr 按 token 匹配：`-m perf` / `-m "cartography or perf"` 放行；
    `-m "not perf ..."` 也含 perf token，但那些项本就被 marker 过滤剔除，
    双保险无害；无 `-m`（本地全量）→ skip。行为由
    tests/unit/test_perf_isolation_wiring.py 以子进程两态锁定。

    Quality V2 W10：seeded 测试顺序轮换（默认关闭）。``QUALITY_ORDER_SEED``
    为非零整数时以该 seed 确定性 shuffle 收集顺序 —— runner 的
    changed/full-local profile 用它暴露顺序污染（隐藏的全局/registry
    泄漏在随机序下以 flake 显形）。未设/0 保持目录序：既有套件与 CI
    契约不受影响；同 seed 顺序可复现（失败可精确重放）。
    """
    seed_raw = (os.environ.get("QUALITY_ORDER_SEED") or "").strip()
    if seed_raw and seed_raw != "0":
        import random as _random
        import warnings as _warnings

        try:
            seed = int(seed_raw)
        except ValueError:
            seed = 0
            _warnings.warn(
                f"QUALITY_ORDER_SEED={seed_raw!r} 不是整数，顺序轮换未启用"
                "（R2 review：静默退化会掩盖 runner 侧笔误）")
        if seed:
            _random.Random(seed).shuffle(items)

    markexpr = (getattr(config.option, "markexpr", "") or "").strip()
    if "perf" in markexpr.split():
        return
    skip_marker = pytest.mark.skip(
        reason="perf 基线要求隔离运行（全量中段执行会抖动超基线，#664）："
        "pytest -m perf --no-cov"
    )
    for item in items:
        # 只认显式 marker，不用 "perf" in item.keywords —— pytest 的 keyword
        # 索引把目录名也算进去（tests/perf/ 下的功能测试无 marker、CI 主
        # lane 照跑，误伤会让本地与 CI 行为分叉）。
        if item.get_closest_marker("perf") is not None:
            item.add_marker(skip_marker)


@pytest.fixture(autouse=True)
def _offline_embedding_model(monkeypatch):
    """测试套件禁止惰性加载真实 SentenceTransformer 模型（#660）。

    FaissVectorStore._get_embedding_model 首次调用会从 HuggingFace 下载模型；
    网络不可达时该同步请求卡在 TLS 握手且无超时。它跑在 asyncio.to_thread
    的 worker 线程里，wait_for 取消不了线程 —— RAG 降级路径照常返回，但事件
    循环关停时 shutdown_default_executor(wait=True) 等不到卡死的 worker，
    pytest-timeout 在 teardown 打断整个套件（无汇总、全量中止）。这里让真模型
    加载快速失败：需要 embeddings 的测试按既有惯例 stub embed_texts
    （test_rag_durability.patch_embed 等），其余路径走文档化的 RAG 降级。
    """
    # Windows 开发机没有 fcntl（faiss_store 的 Unix-only 依赖）：守卫降级为
    # no-op —— 仅针对缺 fcntl 这一类 ImportError；faiss_store 自身的真实
    # 损坏（缺可选依赖等）仍然照常抛出，不静默吞掉。
    try:
        from app.services.rag.faiss_store import FaissVectorStore
    except ImportError as _import_error:  # pragma: no cover - Windows-only path
        if "fcntl" not in str(_import_error):
            raise
        import warnings

        warnings.warn(
            f"offline embedding guard unavailable on this platform: {_import_error}"
        )
        return

    def fail_fast(self):
        raise RuntimeError(
            "test suite must not load the real SentenceTransformer model "
            "(unbounded network); stub FaissVectorStore.embed_texts instead"
        )

    # 专门测试加载器 wiring 的文件（如 test_rag_embedding_offline.py）可以
    # 显式换回真实现 —— 它们 mock 构造器或强制离线快失败，不会真加载。
    fail_fast._real_implementation = FaissVectorStore._get_embedding_model

    monkeypatch.setattr(FaissVectorStore, "_get_embedding_model", fail_fast)
