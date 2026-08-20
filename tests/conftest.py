import os

# Provide a stable test JWT secret so Settings doesn't warn on every import
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("USE_REDIS", "false")
# 防 load_dotenv 注入：`import app.main` 会执行 `load_dotenv()`（app/main.py），把
# .env 里的 CELERY_BROKER_URL / CELERY_RESULT_BACKEND 写进 os.environ（override=False
# 只补不覆盖）。Celery 的 conf 对未显式设置的键（如 result_backend）会回退读取
# CELERY_* 环境变量 —— 于是本应 eager 的测试进程里 backend 变成 RedisBackend，
# 任何 update_state 都会去连 localhost:16379 并失败。这里预先占位，load_dotenv
# 便不会覆盖，测试进程保持离线（broker=memory、backend=cache+memory）。
os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")

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
    from app.services.rag.faiss_store import FaissVectorStore

    def fail_fast(self):
        raise RuntimeError(
            "test suite must not load the real SentenceTransformer model "
            "(unbounded network); stub FaissVectorStore.embed_texts instead"
        )

    monkeypatch.setattr(FaissVectorStore, "_get_embedding_model", fail_fast)
