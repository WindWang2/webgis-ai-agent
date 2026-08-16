"""Real-service smoke subset for the dedicated CI lane (Issue #477).

CI 一直在花钱跑 postgis + redis service container，但套件实际跑的是
SQLite / fakeredis / eager-celery（conftest.py 强制 USE_REDIS=false +
CELERY_BROKER_URL=memory://）—— asyncpg 驱动行为、PostGIS 类型、真实
Redis 线协议、真实 broker 投递从未被验证过（prod-only 回归只能等部署后爆。

本文件是**有界的定向 smoke 子集**（不是把整套测试搬到真实后端）：
  - Postgres/asyncpg：异步引擎真实往返 + 服务端版本；
  - PostGIS：geometry 列建/插/查（真实 PostGIS 类型，0011 迁移的前提）；
  - Redis：真实线协议的 SET/GET/EXPIRE/TTL/pipeline 语义；
  - Celery：真实 broker 投递 + chain + 真实 result backend + revoke 丢弃。

全部用 @pytest.mark.real_services 标记，由 CI 的 real-services-smoke lane
（postgis + redis service containers）执行。服务不可达时逐条 self-skip
（本地开发无容器也能跑整套套件），绝不把"连不上"伪装成"通过"。
"""
import os
import socket
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.real_services


def _tcp_reachable(url: str, default_port: int) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or default_port
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except Exception:  # noqa: BLE001
        return False


def _postgres_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+")):
        pytest.skip("real-services lane: DATABASE_URL 未指向 Postgres")
    if not _tcp_reachable(url, 5432):
        pytest.skip("real-services lane: Postgres 不可达")
    return url


def _redis_url() -> str:
    url = os.environ.get("REDIS_URL", "")
    if not url.startswith("redis://"):
        pytest.skip("real-services lane: REDIS_URL 未设置")
    if not _tcp_reachable(url, 6379):
        pytest.skip("real-services lane: Redis 不可达")
    return url


# ── Postgres / asyncpg ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_postgres_asyncpg_roundtrip():
    """asyncpg 驱动的真实往返：DDL + 参数化写 + 读 + 服务端版本。"""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    url = _postgres_url()
    if "+asyncpg" not in url and url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url)
    table = f"_real_smoke_{uuid.uuid4().hex[:8]}"
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE TABLE {table} (id INT, name TEXT)"))
            await conn.execute(
                text(f"INSERT INTO {table} (id, name) VALUES (:i, :n)"),
                [{"i": 1, "n": "asyncpg-真实往返"}],
            )
        async with engine.connect() as conn:
            rows = (await conn.execute(text(f"SELECT id, name FROM {table}"))).fetchall()
            version = (await conn.execute(text("SELECT version()"))).scalar()
        assert [(r[0], r[1]) for r in rows] == [(1, "asyncpg-真实往返")]
        assert "PostgreSQL" in version
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgis_geometry_roundtrip():
    """PostGIS 类型真实往返 —— 0011 迁移与空间查询在生产 Postgres 上的前提。"""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    url = _postgres_url()
    if "+asyncpg" not in url and url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url)
    table = f"_real_gis_{uuid.uuid4().hex[:8]}"
    try:
        async with engine.connect() as conn:
            postgis_version = (await conn.execute(text("SELECT PostGIS_Version()"))).scalar()
        assert postgis_version, "PostGIS 扩展不可用 —— postgis service container 配置错误"

        async with engine.begin() as conn:
            await conn.execute(
                text(f"CREATE TABLE {table} (id INT, geom geometry(Point, 4326))")
            )
            await conn.execute(
                text(
                    f"INSERT INTO {table} (id, geom) "
                    "VALUES (1, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))"
                ),
                {"lon": 116.397, "lat": 39.909},
            )
        async with engine.connect() as conn:
            wkt = (await conn.execute(
                text(f"SELECT ST_AsText(geom) FROM {table} WHERE id = 1")
            )).scalar()
            srid = (await conn.execute(
                text(f"SELECT ST_SRID(geom) FROM {table} WHERE id = 1")
            )).scalar()
        assert wkt == "POINT(116.397 39.909)"
        assert srid == 4326
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
        await engine.dispose()


# ── Redis 真实线协议 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redis_real_wire_semantics():
    """SET/GET 字节保真、EXPIRE/TTL 语义、pipeline 事务 —— fakeredis 无法
    捕获真实服务端行为回归（如 TTL 精度/RESP 编码差异）。"""
    import redis.asyncio as aioredis

    url = _redis_url()
    r = aioredis.from_url(url, decode_responses=False)
    prefix = f"real-smoke:{uuid.uuid4().hex[:8]}"
    try:
        key = f"{prefix}:kv"
        # RESP 二进制安全：非 ASCII 值往返不失真
        value = "值-with-émoji-🗺️".encode("utf-8")
        assert await r.set(key, value)
        assert await r.get(key) == value

        # TTL 语义：EX 后 TTL 有界递减
        ttl_key = f"{prefix}:ttl"
        await r.set(ttl_key, b"1", ex=30)
        ttl = await r.ttl(ttl_key)
        assert 1 <= ttl <= 30, f"EX=30 后 TTL 应在 (0,30]，实际 {ttl}"
        # 持久化覆盖：SET 不带 EX 清除 TTL（服务端语义）
        await r.set(ttl_key, b"2")
        assert await r.ttl(ttl_key) == -1

        # pipeline：命令批量原子提交
        pipe_keys = [f"{prefix}:p{i}" for i in range(3)]
        async with r.pipeline(transaction=True) as pipe:
            for i, k in enumerate(pipe_keys):
                pipe.set(k, str(i).encode())
            await pipe.execute()
        for i, k in enumerate(pipe_keys):
            assert await r.get(k) == str(i).encode()
    finally:
        await r.delete(
            *(await r.keys(f"{prefix}:*") or [f"{prefix}:none"])
        )
        await r.aclose()


# ── Celery 真实 broker 投递 ─────────────────────────────────────────────

# 独立的极简 celery app：不复用 app.services.task_queue 单例（它按 conftest
# 环境是 eager + memory broker）。worker 子进程通过
# tests.real_services_celery_app 导入同一 app —— broker/backend 指向真实
# Redis（专用 db 5，不污染会话数据）。
SMOKE_BROKER_DB = 5


def _broker_urls(redis_url: str) -> tuple[str, str]:
    parsed = urlparse(redis_url)
    base = f"redis://{parsed.netloc}"
    return f"{base}/{SMOKE_BROKER_DB}", f"{base}/{SMOKE_BROKER_DB}"


@pytest.fixture(scope="module")
def celery_worker():
    """启动/停止一个真实 celery worker 子进程（solo pool，避免 fork 噪声）。"""
    import redis.asyncio as aioredis

    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    redis_url = _redis_url()
    broker, backend = _broker_urls(redis_url)
    env = {
        **os.environ,
        "REAL_SMOKE_BROKER_URL": broker,
        "REAL_SMOKE_RESULT_BACKEND": backend,
    }
    # 测试进程里的 app 在 import 时读这两个变量 —— 必须先设再 import，
    # 否则它落到默认 localhost（与 worker 的 URL 不一致，ping 永远无响应）。
    os.environ["REAL_SMOKE_BROKER_URL"] = broker
    os.environ["REAL_SMOKE_RESULT_BACKEND"] = backend
    # conftest.py 为整套套件强制 CELERY_BROKER_URL=memory://（测试进程离线）。
    # Celery 的 Settings.broker_url/result_backend 是 property，每次访问都
    # **先读环境变量**（celery/app/utils.py）—— 进程内任何 conf 赋值都赢不了
    # 它。真实 broker 测试期间临时摘掉这两个变量，finally 恢复（本 lane 只跑
    # real_services 子集；混合本地跑时恢复保证其余测试保持 eager/离线）。
    _saved_celery_env = {
        k: os.environ.pop(k)
        for k in ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
        if k in os.environ
    }
    # worker 子进程同样不能继承 memory://（已在 env 字典里剔除）。
    env = {
        k: v for k, v in env.items()
        if k not in ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
    }
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "celery",
            "-A", "tests.real_services_celery_app",
            "worker", "--pool=solo", "--concurrency=1",
            "--loglevel=WARNING", "--without-gossip", "--without-mingle",
        ],
        env=env,
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        # 就绪探测：worker 的 pidbox 响应 control ping（有界等待）
        from tests.real_services_celery_app import smoke_celery_app

        # 级联守卫：app 若被 conftest 的 CELERY_* 环境降到 memory://，
        # ping 永远无响应 —— 提前用可诊断的错误失败。
        assert smoke_celery_app.conf.broker_url == broker, (
            f"smoke celery app broker 配置错误: {smoke_celery_app.conf.broker_url} != {broker}"
        )
        deadline = time.monotonic() + 60
        ready = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                pytest.fail(f"celery worker 提前退出:\n{out[-2000:]}")
            try:
                reply = smoke_celery_app.control.ping(timeout=2)
            except Exception:  # noqa: BLE001
                reply = None
            if reply:
                ready = True
                break
            time.sleep(1)
        assert ready, "celery worker 60s 内未就绪（ping 无响应）"
        yield smoke_celery_app
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        os.environ.update(_saved_celery_env)
        # 清掉本次 run 的 broker/result 键（backend db）—— 不影响其它 db
        import asyncio

        async def _flush() -> None:
            r = aioredis.from_url(broker)
            await r.flushdb()
            await r.aclose()

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_flush())
        finally:
            loop.close()


@pytest.mark.asyncio
async def test_celery_real_broker_delivery_and_chain(celery_worker):
    """真实 broker 投递 + chain 组合 + 真实 result backend 取回。"""
    from celery import chain

    from tests.real_services_celery_app import add, mul

    simple = add.delay(2, 3)
    assert simple.get(timeout=30) == 5, "任务未通过真实 broker 送达 worker"

    composed = chain(add.s(2, 3), mul.s(10))
    assert composed().get(timeout=30) == 50, "chain 第二跳未在真实 worker 上执行"


@pytest.mark.asyncio
async def test_celery_revoke_discards_pending_task(celery_worker):
    """revoke 丢弃未执行任务 —— 前端"取消任务"依赖的真实 broker 语义。"""
    from celery.exceptions import TaskRevokedError, TimeoutError as CeleryTimeoutError

    from tests.real_services_celery_app import slow

    # countdown 给 revoke 一个有界窗口；任务被 worker 标记 revoked 后丢弃，
    # 客户端 get() 要么超时（结果停在 PENDING）要么收到 TaskRevokedError。
    result = slow.apply_async(args=[1], countdown=3)
    celery_worker.control.revoke(result.id)
    with pytest.raises((CeleryTimeoutError, TaskRevokedError)):
        result.get(timeout=6)
    assert result.status in ("PENDING", "REVOKED"), (
        f"被 revoke 的任务不应执行，实际状态: {result.status}"
    )
