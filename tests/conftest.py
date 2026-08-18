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
