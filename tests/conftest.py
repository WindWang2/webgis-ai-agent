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
