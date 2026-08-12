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
