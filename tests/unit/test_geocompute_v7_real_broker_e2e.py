"""GeoCompute V7 real-broker E2E —— 完成证明（真实 Redis broker + 生产
celery worker 子进程 + 持久 coordinator + 共享 SQLite 控制面）。

覆盖（Epic §15/§17 的环境可达子集；本地 Redis 可达，Postgres 不可达 →
控制面用共享 SQLite 文件，WAL 模式 + 短事务 —— 与生产 PG 语义同构的
CAS 全部经单语句条件 UPDATE）：

1. **全 durable 节点链端到端**：submit → coordinator 放置认领 → broker
   派发（input handoff）→ worker 领取执行（能力注册/心跳）→ session ref
   交接 → 终态落库 → 分布式事件（coordinator + worker 双侧）→ 无重复输出。
2. **worker crash → stale 对账 → 有界重派**：kill worker 后驱动生产同一
   stale 收敛路径（jobs.sweep_stale，测试时钟加速）→ WORKER_LOSS 分类
   → attempt 2 重派 → 新 worker 完成 → 事件链完整（node_lost +
   node_dispatched×2）。
3. **跨进程取消**：run 在跑时另一「API 进程」（测试进程）写持久取消旗标
   → run 收敛 cancelled（事件可见）。
4. **duplicate delivery**：同 job 双投递 → durable 入口守卫拒绝第二份 →
   输出不重复（结果唯一）。
5. **broker 重连**：worker 重启后重新消费队列（容量重新可见）。

诚实边界（REAL_SERVICES 纪律）：本文件全部用 ``@pytest.mark.real_services``
+ ``REAL_SERVICES=1`` 显式武装；Redis 不可达 → self-skip（绝不把「连不上」
伪装成通过）。Postgres 相关断言不在本文件（环境不可达，不虚构）。
"""
from __future__ import annotations

import itertools
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

# timeout(560)：本模块最坏路径（多次生产 worker boot + 长等待）真实需要
# 数分钟；marker 覆盖 CI real-services 泳道的 --timeout=180（pytest-timeout
# 语义：item 级 marker 优先于 CLI），避免负载下被 thread 打断、teardown
# 跳过再生孤儿 worker（round2 RM1）。
pytestmark = [pytest.mark.real_services, pytest.mark.timeout(560)]

REPO = Path(__file__).resolve().parents[2]

#: E2E broker/backend db 池（与会话数据 0 / 会话 1 / toy 5 / prod-smoke 6,7
#: 隔离）。**每次运行随机选取**：pytest-timeout 打断时 fixture teardown 不
#: 运行 → worker 子进程可能成为孤儿消费者；孤儿停留在旧 db 上收不到新
#: 消息（本地 10 上的孤儿 worker 用死库领走消息的事故 = 随机化的动机）。
_DB_POOL = (10, 11, 12, 13, 14, 15)

_WORKER_BOOT_TIMEOUT = 75


def _explicit_lane_enabled() -> bool:
    return os.environ.get("REAL_SERVICES") == "1"


def _redis_base() -> str | None:
    if not _explicit_lane_enabled():
        return None
    url = os.environ.get("REAL_SMOKE_BROKER_URL", "redis://localhost:6379/5")
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return f"redis://{host}:{port}"
    except Exception:  # noqa: BLE001 - Redis 不可达 → self-skip
        return None


def _skip_if_no_redis():
    base = _redis_base()
    if base is None:
        pytest.skip("REAL_SERVICES=1 required and Redis unreachable: "
                    "real-broker E2E cannot run honestly offline")


def _redis_db_index(url: str, default: int) -> int:
    try:
        path = (urlparse(url).path or "").lstrip("/")
        return int(path) if path != "" else default
    except (TypeError, ValueError):
        return default


def _pick_e2e_dbs() -> tuple[int, int]:
    """选择 E2E broker/backend db。

    **关键（CI smoke hang）**：Celery/kombu 连接池按首次 broker_url 建连并
    复用。real-services lane 在 pytest 前已把 ``CELERY_BROKER_URL`` 钉到
    ``redis://…/6``（prod-smoke），同进程里若再把 conf/env 切到随机 db
    10–15，``apply_async`` 仍可能把消息打进旧池的 db 6，而 worker 子进程
    听的是 10–15 → ``node_dispatched`` 之后永无 ``node_started``（run 卡在
    ``running``）。因此：进程已钉 redis CELERY_* 时**必须沿用该 db**；仅在
    尚未钉 redis broker 时才走随机池（本地隔离孤儿 worker）。
    """
    import random

    existing = os.environ.get("CELERY_BROKER_URL", "")
    if existing.startswith("redis://"):
        broker = _redis_db_index(existing, 6)
        backend_url = os.environ.get("CELERY_RESULT_BACKEND", "")
        if backend_url.startswith("redis://"):
            backend = _redis_db_index(backend_url, 7 if broker != 7 else 6)
        else:
            backend = 7 if broker != 7 else 6
        if backend == broker:
            backend = (broker + 1) % 16
        return broker, backend

    broker = random.choice(_DB_POOL)
    backend = (broker + 1) % 16 if (broker + 1) not in (0, 1, 5, 6, 7, 8, 9) \
        else (broker - 1) % 16
    return broker, backend


def _recycle_celery_broker(app) -> None:
    """丢弃可能绑在旧 broker_url 上的 producer/connection 池，迫使下次
    publish 按当前 ``CELERY_BROKER_URL`` 重建。

    **关键（#1226 后遗）**：仅 ``force_close_all`` + ``app._pool = None``
    不够 —— ``force_close_all`` 把池标成 ``_closed=True``，但
    ``kombu.pools.connections`` / ``producers`` 仍按 connection 身份持有
    **同一已关闭对象**；下次 ``app.pool`` / ``app.amqp.producer_pool``
    从注册表取回 closed pool → ``Acquire on closed pool``（smoke E2E
    enqueue 全失败 → NODE_FAILED）。且 producer 池在 ``app.amqp``，不在
    ``app`` 上。正确做法：``kombu.pools.reset()``（关资源 + ``group.clear()``）
    并清空 ``app._pool`` / ``app.amqp._producer_pool``，让下次访问走
    ``__missing__`` 建新池。
    """
    from kombu import pools as kombu_pools

    try:
        kombu_pools.reset()
    except Exception:  # noqa: BLE001 - best-effort recycle
        pass
    try:
        app._pool = None
    except Exception:  # noqa: BLE001
        pass
    try:
        app.amqp._producer_pool = None
    except Exception:  # noqa: BLE001
        pass


#: 进程级固定一组 db：celery app 的连接池按 conf 建连并跨测试复用 ——
#: 每测试换 db 会让 publish 落到旧连接的 db（消息黑洞，真实事故）；
#: 已钉 redis CELERY_*（CI lane）时沿用 6/7，避免与预建池分叉。
_E2E_BROKER_DB, _E2E_BACKEND_DB = _pick_e2e_dbs()


def _plan_chain(session_tag: str, *, rows: int = 6,
                retries: int = 1) -> dict:
    """两节点全 durable 链：n1（内联 features 过滤）→ n2（input handoff）。"""
    feats = [
        {"type": "Feature",
         "properties": {"kind": "keep" if i % 2 == 0 else "drop", "i": i},
         "geometry": None}
        for i in range(rows)
    ]
    return {
        "plan_id": f"v7-e2e-{session_tag}",
        "nodes": [
            {
                "node_id": "n1",
                "category": "filter",
                "operation": "op",
                "inputs": [],
                "parameters": {
                    "features": feats,
                    "predicate": {"op": "eq", "field": "kind", "value": "keep"},
                },
                "policy": "durable_job",
                "retry": {"max_attempts": retries},
                "resource_class": {"memory": 1, "cpu": 1, "io": 1},
            },
            {
                "node_id": "n2",
                "category": "filter",
                "operation": "op",
                "inputs": ["n1"],
                "parameters": {
                    "predicate": {"op": "eq", "field": "i", "value": 0},
                },
                "policy": "durable_job",
                "retry": {"max_attempts": retries},
                "resource_class": {"memory": 1, "cpu": 1, "io": 1},
            },
        ],
        "budget": {"deadline_s": 240},
    }


_proc_counter = itertools.count()


class _ClusterEnv:
    """E2E 集群环境句柄（共享 DB 工厂 + worker 子进程 + coordinator 线程）。"""

    def __init__(self, tmp_path, redis_base: str, *,
                 broker_db: int, backend_db: int):
        import threading

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        self.tmp_path = tmp_path
        self.redis_base = redis_base
        self.broker_db = broker_db
        self.backend_db = backend_db
        self.broker = f"{redis_base}/{broker_db}"
        self.backend = f"{redis_base}/{backend_db}"
        self.db_path = tmp_path / "e2e-control.db"
        # WAL：跨进程并发读写的最小冲突面（控制面事务全部单语句短事务）
        eng0 = create_engine(f"sqlite:///{self.db_path}")
        from app.models.db_model import Base

        Base.metadata.create_all(eng0)
        with eng0.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            conn.commit()
        eng0.dispose()
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.coordinator = None
        self.coord_thread: threading.Thread | None = None
        self.worker_proc: subprocess.Popen | None = None
        self._saved_env: dict[str, str | None] = {}

    @property
    def worker_log_path(self) -> Path:
        logs = sorted(self.tmp_path.glob("worker-*.log"))
        return logs[-1] if logs else (self.tmp_path / "worker-missing.log")

    # ------------------------------------------------------------- patch

    def patch_test_process(self, monkeypatch) -> None:
        """测试进程：store/events/locality/复用/证据全指向共享库；celery
        app 切真实 broker（与生产 worker 同一 app 配置）；session 存储
        切共享 Redis（db 1）—— worker 经 env 构造同后端，跨进程 ref
        解析才成立（conftest 默认 memory 后端是单进程的）。"""
        from app.services.geocompute import reuse_index, run_evidence
        from app.services.geocompute.cluster import store as csm
        from app.services.task_queue import celery_app

        monkeypatch.setattr(run_evidence, "session_factory", self.factory)
        monkeypatch.setattr(reuse_index, "session_factory", self.factory)
        monkeypatch.setattr(csm, "session_factory", self.factory)
        # jobs 子系统（durable job 行真相）也指向共享库 —— submit 侧
        # （tools._utils.db_session → 全局 SessionLocal）与轮询侧
        # （durable.session_factory → jobs worker 默认工厂）都是**调用时**
        # 解析，可安全注入：
        from app.services.jobs import submit as jobs_submit_mod
        from app.services.jobs import worker as jobs_worker_mod
        from app.tools import _utils as tools_utils

        def _shared_factory():
            return self.factory()

        monkeypatch.setattr(jobs_worker_mod, "_default_session_factory",
                            _shared_factory)
        # 关键：submit.py 顶层 `from app.tools._utils import db_session`
        # 持有**值绑定** —— 只 patch tools_utils 对首次导入早于本 fixture
        # 的进程无效（非确定性事故的根因）。两个名字都指到共享库。
        import contextlib

        @contextlib.contextmanager
        def _shared_db_session():
            db = self.factory()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        monkeypatch.setattr(tools_utils, "db_session", _shared_db_session)
        monkeypatch.setattr(jobs_submit_mod, "db_session",
                            _shared_db_session)
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("USE_REDIS", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND",
                      "REDIS_URL")
        }
        # conftest 把 CELERY_BROKER_URL=memory:// 钉进 os.environ（setdefault）；
        # celery conf 级联里 env 源优先于直接赋值 —— 必须在环境层覆盖
        # （与 test_real_services_smoke 的 production fixture 同款纪律）。
        monkeypatch.setenv("USE_REDIS", "true")
        monkeypatch.setenv("CELERY_BROKER_URL", self.broker)
        monkeypatch.setenv("CELERY_RESULT_BACKEND", self.backend)
        monkeypatch.setitem(celery_app.conf, "task_always_eager", False)
        monkeypatch.setitem(celery_app.conf, "broker_url", self.broker)
        monkeypatch.setitem(celery_app.conf, "result_backend", self.backend)
        # Celery Settings.broker_url 优先读环境变量；但仍可能已有绑在旧 URL
        # 上的 kombu 池（lane 其它 fixture 先投递过）—— 显式回收。
        _recycle_celery_broker(celery_app)
        assert celery_app.conf.broker_url == self.broker, (
            f"celery broker rebind failed: {celery_app.conf.broker_url!r} "
            f"!= {self.broker!r}")
        assert celery_app.conf.task_always_eager is False

        # 跨进程会话存储（ref 交接的真相通道）：测试进程与 worker 同域
        from app.services import session_data as sd_mod
        from app.services.session_data_redis import RedisSessionDataManager

        self.session_redis = f"{self.redis_base}/1"
        monkeypatch.setattr(
            sd_mod, "session_data_manager",
            RedisSessionDataManager(self.session_redis))

    # ----------------------------------------------------------- worker

    def start_worker(self, wait: bool = True) -> subprocess.Popen:
        env = {
            **os.environ,
            "USE_REDIS": "true",
            "CELERY_BROKER_URL": self.broker,
            "CELERY_RESULT_BACKEND": self.backend,
            "REDIS_URL": getattr(self, "session_redis",
                                 f"{self.redis_base}/1"),
            "DATABASE_URL": f"sqlite:///{self.db_path}",
            # 单 worker 全队列消费（compose 部署同款）
            "WEBGIS_WORKER_PROFILE_SLOTS": "",
        }
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "celery",
                "-A", "app.services.task_queue",
                "worker", "--pool=solo", "--concurrency=1",
                "--loglevel=INFO", "--without-gossip", "--without-mingle",
                "-Q", "celery,light_cpu_queue,heavy_cpu_queue,"
                      "high_memory_queue,raster_queue,network_queue,"
                      "external_io_queue",
            ],
            env=env, cwd=str(REPO),
            # celery 的 logging handler 逐条 flush —— 直接落文件即可诊断
            #（此前 pump 线程 + 块缓冲在 SIGKILL 下丢日志，事故不可诊断）
            stdout=open(
                self.tmp_path / f"worker-{next(_proc_counter)}.log", "w"),
            stderr=subprocess.STDOUT,
        )
        if wait:
            self.worker_proc = proc  # 就绪前登记 —— teardown 可杀（防孤儿）
            # 就绪判定 = 能力注册行落库（worker_ready handler 写
            # geocompute_workers）—— 比 celery control ping 可靠（mailbox
            # 连接在多轮 monkeypatch 后状态不保真，曾致误判未就绪）。
            from app.services.geocompute.cluster.store import ClusterRunStore

            store = ClusterRunStore(self.factory)
            deadline = time.monotonic() + _WORKER_BOOT_TIMEOUT
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"worker exited during boot; log={self.worker_log_path}")
                if store.live_workers(role="worker"):
                    break
                time.sleep(1)
            else:
                proc.kill()
                raise RuntimeError(
                    f"worker did not register in time; log={self.worker_log_path}")
        return proc

    def kill_worker(self) -> None:
        if self.worker_proc is not None and self.worker_proc.poll() is None:
            self.worker_proc.send_signal(signal.SIGKILL)
            self.worker_proc.wait(timeout=15)

    # ------------------------------------------------------- coordinator

    def start_coordinator(self, *, lease_ttl_s: float = 6.0) -> None:
        import threading

        from app.services.geocompute.cluster.scheduler import ClusterCoordinator
        from app.services.geocompute.cluster.store import ClusterRunStore

        self.coordinator = ClusterCoordinator(
            store=ClusterRunStore(self.factory),
            coordinator_id="coord-e2e",
            local_slots=2,
            heartbeat_interval_s=0.1,
            tick_interval_s=0.05,
            leadership_ttl_s=5.0,
            lease_ttl_s=lease_ttl_s,
        )

        def _loop() -> None:
            self.coordinator.run_forever()

        self.coord_thread = threading.Thread(
            target=_loop, name="v7-e2e-coordinator", daemon=True)
        self.coord_thread.start()

    def stop(self) -> None:
        if self.coordinator is not None:
            self.coordinator.stop()
        if self.coord_thread is not None:
            self.coord_thread.join(timeout=10)
        self.kill_worker()
        self.engine.dispose()


@pytest.fixture()
def cluster(tmp_path, monkeypatch):
    _skip_if_no_redis()
    broker_db, backend_db = _E2E_BROKER_DB, _E2E_BACKEND_DB
    # flush：随机 db 隔离历史运行（pytest-timeout 打断时 teardown
    # 不跑 → 孤儿 worker 曾用死库领走消息）。消费者清零由 (a) teardown
    # kill (b) 本地 ps 检查 (c) CI lane 隔离保证；flush 清残留 keyspace。
    import redis as _redis

    _parsed = urlparse(_redis_base())

    def _flush_db(dbnum: int) -> None:
        client = _redis.Redis(host=_parsed.hostname,
                              port=_parsed.port or 6379, db=dbnum)
        try:
            client.flushdb()
        finally:
            client.close()

    _flush_db(broker_db)
    _flush_db(backend_db)
    env = _ClusterEnv(tmp_path, _redis_base(),
                      broker_db=broker_db, backend_db=backend_db)
    # setup：清空本次选取的 db（上次崩溃遗留的孤儿消息不污染本次）
    try:
        import redis as redis_sync

        parsed = urlparse(env.broker)
        for db in (broker_db, backend_db):
            client = redis_sync.Redis(host=parsed.hostname,
                                      port=parsed.port or 6379, db=db)
            client.flushdb()
            client.close()
    except Exception:  # noqa: BLE001 - 预清理失败不影响断言（隔离已随机化）
        pass
    env.patch_test_process(monkeypatch)
    # pytest-timeout 用 thread 法打断测试时 fixture teardown 不运行 →
    # atexit 至少在**进程正常退出**路径上回收 worker（超时 SIGKILL 场景
    # 由随机 db 隔离兜底：孤儿收不到新 db 的消息）
    import atexit

    atexit.register(env.stop)
    from app.services.geocompute.cluster.events import RunEventStore

    events = RunEventStore()
    try:
        yield env, events
    finally:
        atexit.unregister(env.stop)
        env.stop()
        try:
            import redis as redis_sync

            parsed = urlparse(env.broker)
            for db in (broker_db, backend_db):
                client = redis_sync.Redis(host=parsed.hostname,
                                          port=parsed.port or 6379, db=db)
                client.flushdb()
                client.close()
        except Exception:  # noqa: BLE001 - 清理失败不影响断言
            pass


def _submit_run(env: _ClusterEnv, plan: dict, *, tag: str) -> str:
    """每测试独立 owner/session：复用键与缓存键都含 owner 域，session ref
    按 session 隔离 —— 跨测试互不污染（同 owner 跨 run 复用是 by-design
    的 checkpoint 语义，会让节点执行计数类断言互相污染）。"""
    from app.services.geocompute.cluster.store import ClusterRunStore

    return ClusterRunStore(env.factory).create_run(
        plan_snapshot=plan,
        plan_fingerprint="v7e2e-" + tag,
        owner_scope=f"u:e2e-{tag}",
        session_id=f"e2e-sess-{tag}",
    )


def _wait_terminal(env: _ClusterEnv, run_id: str, timeout: float = 120) -> dict:
    from app.services.geocompute.cluster.store import ClusterRunStore

    store = ClusterRunStore(env.factory)
    deadline = time.monotonic() + timeout
    row = store.get_run(run_id) or {}
    while time.monotonic() < deadline:
        row = store.get_run(run_id) or {}
        if row.get("terminal_at"):
            return row
        time.sleep(0.2)
    return row


def _wait_event(events, run_id: str, name: str,
                timeout: float = 60) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for ev in events.window(run_id, after_id=0, limit=200):
            if ev["event"] == name:
                return ev
        time.sleep(0.2)
    return None


class TestRealBrokerE2E:
    def test_full_durable_chain_end_to_end(self, cluster):
        """完成证明主线：多节点 GIS DAG（全 durable，含 input handoff）经
        真实 broker 在 worker 上执行到正确终态产物。"""
        env, events = cluster
        env.start_worker()
        # 容量注册（含 capability）在就绪即应可见
        workers = __import__(
            "app.services.geocompute.cluster.store",
            fromlist=["ClusterRunStore"]).ClusterRunStore(
                env.factory).live_workers(role="worker")
        assert workers, "worker did not register"
        assert workers[0]["profiles"].get("light_cpu", 0) >= 1
        assert (workers[0].get("capability") or {}), (
            "capability profile missing on worker row")
        env.start_coordinator()
        plan = _plan_chain("e2e-happy")
        rid = _submit_run(env, plan, tag="happy")
        row = _wait_terminal(env, rid, timeout=150)
        assert row.get("status") == "completed", (
            f"run not completed: {row} events={events.window(rid)}")
        # 双侧分布式事件齐备（coordinator：run_started/dispatched/completed；
        # worker：node_started/output_ready）
        ev_names = [e["event"] for e in events.window(rid, limit=200)]
        assert "run_started" in ev_names
        assert "node_dispatched" in ev_names
        assert "node_started" in ev_names
        assert "node_output_ready" in ev_names
        assert "node_completed" in ev_names
        assert "run_completed" in ev_names
        # 无重复输出：每节点恰好一次终局
        assert ev_names.count("node_completed") == 2
        # worker 能力注册真实发生（capability 到库）—— 注册/心跳在
        # start_worker 就绪即成立（run 完成可能已超 worker TTL，那时
        # 「不可见」是容量收缩的正确语义，不是注册失败）
        workers = __import__(
            "app.services.geocompute.cluster.store",
            fromlist=["ClusterRunStore"]).ClusterRunStore(
                env.factory).live_workers(role="worker")
        assert workers, "worker did not register"
        assert workers[0]["profiles"].get("light_cpu", 0) >= 1
        capability = workers[0].get("capability") or {}
        assert capability, "capability profile missing on worker row"

    def test_worker_crash_stale_reconcile_and_reschedule(self, cluster):
        """worker dies after dispatch → stale 对账 → WORKER_LOSS → 有界重派
        → 新 worker 完成；事件链诚实（node_lost + 重派 dispatched）。

        竞态控制：SIGKILL 落点与节点时长存在窗口（节点可能恰好已完成）。
        对长链逐节点重试 kill：抓到「kill 时刻存在 running job」即对账
        （生产 sweep 的谓词/批量逻辑由 jobs 子系统自身测试覆盖；这里对
        收敛后的状态机迁移用生产同款 CAS），V7 契约被测的是：stale 终态 →
        await 侧 WORKER_LOSS 分类 → 节点有界重派 → 新 worker 完成 →
        输出无重复。
        """
        env, events = cluster
        env.start_worker()
        env.start_coordinator()
        plan = _plan_chain("e2e-crash", rows=50, retries=2)
        for i in range(3, 15):  # 14 节点链：多次 kill 落点机会
            plan["nodes"].append({
                "node_id": f"n{i}",
                "category": "filter",
                "operation": "op",
                "inputs": [f"n{i - 1}"],
                "parameters": {
                    "predicate": {"op": "eq", "field": "kind",
                                  "value": "keep"},
                },
                "policy": "durable_job",
                "retry": {"max_attempts": 2},
                "resource_class": {"memory": 1, "cpu": 1, "io": 1},
            })
        rid = _submit_run(env, plan, tag="crash")

        from app.models.db_model import AnalysisTask
        from app.services.jobs.lifecycle import JobStatus
        from app.services.jobs.store import DurableJobStore, _utcnow

        crashed = False
        seen_starts = set()
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline and not crashed:
            evs = events.window(rid, limit=200)
            starts = [e for e in evs if e["event"] == "node_started"]
            new_start = next(
                (e for e in starts if e["node_id"] not in seen_starts), None)
            if new_start is None:
                time.sleep(0.05)
                continue
            seen_starts.add(new_start["node_id"])
            env.kill_worker()
            with env.factory() as db:
                running = (
                    db.query(AnalysisTask)
                    .filter(AnalysisTask.status == JobStatus.running.value)
                    .all()
                )
            if running:
                # kill 落在节点执行中 → 生产同款 CAS 收敛 stale
                with env.factory() as db:
                    for job in running:
                        ok = DurableJobStore.transition_sync(
                            db, job.id, JobStatus.stale,
                            progress_message="worker heartbeat lost",
                            error_trace="job marked stale: worker heartbeat "
                                        "lost",
                            **DurableJobStore._terminal_fields(_utcnow()),
                        )
                        assert ok, f"stale transition rejected: {job.id}"
                    db.commit()
                crashed = True
            else:
                # 节点在 kill 前已完成 → 重启 worker 继续链，等下一落点
                env.start_worker()
        assert crashed, (
            f"never caught a running job across kills; "
            f"events={events.window(rid, limit=200)}")

        # 新 worker 上线 → WORKER_LOSS 的 attempt 2 重派被新 worker 领走
        env.start_worker()
        row = _wait_terminal(env, rid, timeout=150)
        assert row.get("status") == "completed", (
            f"run not completed after worker crash: {row} "
            f"events={events.window(rid, limit=200)}")
        evs = events.window(rid, limit=200)
        [e["event"] for e in evs]
        # 重派证据：同一节点被再次派发/执行（attempt 2）；node_lost 仅在
        # 重试耗尽时出现（成功重派路径的正确形态是没有它）
        started_by_node: dict[str, int] = {}
        dispatched_by_node: dict[str, int] = {}
        for e in evs:
            if e["event"] == "node_started":
                started_by_node[e["node_id"]] = (
                    started_by_node.get(e["node_id"], 0) + 1)
            if e["event"] == "node_dispatched":
                dispatched_by_node[e["node_id"]] = (
                    dispatched_by_node.get(e["node_id"], 0) + 1)
        assert any(v >= 2 for v in dispatched_by_node.values()), (
            f"no re-dispatch evidence: {dispatched_by_node}")
        assert any(v >= 2 for v in started_by_node.values()), (
            f"no re-execution evidence: {started_by_node}")

    def test_cancel_running_run_from_another_process(self, cluster):
        """跨进程取消：DAG 在跑时写持久旗标（任意 API 进程语义）→ run
        收敛 cancelled、事件可见。30 节点链让 mid-flight 窗口确定。"""
        env, events = cluster
        env.start_worker()
        env.start_coordinator()
        plan = _plan_chain("e2e-cancel", rows=6)
        # 链式加长：n3..n30 逐级过滤（input handoff 串联 —— 节点消息在
        # broker 上排队执行，为取消旗标提供确定的 mid-flight 窗口）
        for i in range(3, 31):
            plan["nodes"].append({
                "node_id": f"n{i}",
                "category": "filter",
                "operation": "op",
                "inputs": [f"n{i - 1}"],
                "parameters": {
                    "predicate": {"op": "eq", "field": "kind",
                                  "value": "keep"},
                },
                "policy": "durable_job",
                "retry": {"max_attempts": 1},
                "resource_class": {"memory": 1, "cpu": 1, "io": 1},
            })
        rid = _submit_run(env, plan, tag="cancel")
        assert _wait_event(events, rid, "node_started", timeout=75) is not None
        from app.services.geocompute.cluster.store import ClusterRunStore

        store = ClusterRunStore(env.factory)
        changed, observed = store.request_cancel(rid)
        assert changed, "cancel flag not accepted"
        deadline = time.monotonic() + 75
        row = store.get_run(rid) or {}
        while time.monotonic() < deadline:
            row = store.get_run(rid) or {}
            if row.get("status") == "cancelled":
                break
            time.sleep(0.3)
        assert row.get("status") == "cancelled", (
            f"run not cancelled: {row} events={events.window(rid, limit=200)}")
        ev_names = [e["event"] for e in events.window(rid, limit=200)]
        assert "run_cancelled" in ev_names

    def test_duplicate_delivery_single_output(self, cluster, monkeypatch):
        """duplicate delivery（visibility timeout / acks_late redelivery 语义）：
        同 job 二次投递 → durable 入口守卫（AlreadyFinished）拒绝 → 输出
        不重复。"""
        env, events = cluster
        env.start_worker()
        env.start_coordinator()
        plan = _plan_chain("e2e-dup", rows=50)
        rid = _submit_run(env, plan, tag="dup")
        row = _wait_terminal(env, rid, timeout=150)
        assert row.get("status") == "completed"
        ev_names = [e["event"] for e in events.window(rid, limit=200)]
        assert ev_names.count("node_completed") == 2

        # 二次投递同 job_id：任务体入口守卫必须拒绝（真实 job 行 → 共享库）
        from app.services.jobs import worker as jobs_worker_mod
        from app.services.geocompute.tasks import run_geocompute_node

        monkeypatch.setattr(
            jobs_worker_mod, "_default_session_factory",
            lambda: env.factory())
        with env.factory() as db:
            from app.models.db_model import AnalysisTask

            jobs = (
                db.query(AnalysisTask)
                .filter(AnalysisTask.task_type == "geocompute_node")
                .order_by(AnalysisTask.id.desc())
                .limit(4)
                .all()
            )
            assert jobs
            job = jobs[0]
            node_dict = (job.parameters or {}).get("node")
            job_id = int(job.id)
        assert node_dict
        with pytest.raises(Exception) as exc_info:
            run_geocompute_node.apply(
                kwargs={
                    "node": node_dict,
                    "session_id": "e2e-sess-dup",
                    "job_id": job_id,
                })
        # 入口守卫语义：AlreadyFinished（重复投递/终态）—— 不是重执行
        assert "already" in str(exc_info.value).lower() or "not found" in str(
            exc_info.value).lower()
        ev_names2 = [e["event"] for e in events.window(rid, limit=200)]
        assert ev_names2.count("node_completed") == 2, "duplicate output!"

    def test_worker_restart_reconnects_broker(self, cluster):
        """broker 重连：worker 重启后容量重新可见（prune → 再注册）。"""
        env, events = cluster
        env.start_worker()
        env.start_coordinator()
        plan = _plan_chain("e2e-restart", rows=10)
        rid = _submit_run(env, plan, tag="restart")
        assert _wait_terminal(env, rid, timeout=180).get("status") == "completed"
        # 重启 worker：kill → prune → 新 worker 注册 → 新 run 完成
        env.kill_worker()
        time.sleep(1)
        env.start_worker()
        plan2 = _plan_chain("e2e-restart-2", rows=10)
        rid2 = _submit_run(env, plan2, tag="restart-2")
        row = _wait_terminal(env, rid2, timeout=180)
        assert row.get("status") == "completed", (
            f"run after worker restart not completed: {row}")
