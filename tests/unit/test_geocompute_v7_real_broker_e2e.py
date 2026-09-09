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

pytestmark = pytest.mark.real_services

REPO = Path(__file__).resolve().parents[2]

#: E2E broker/backend db 池（与会话数据 0 / 会话 1 / toy 5 / prod-smoke 6,7
#: 隔离）。**每次运行随机选取**：pytest-timeout 打断时 fixture teardown 不
#: 运行 → worker 子进程可能成为孤儿消费者；孤儿停留在旧 db 上收不到新
#: 消息（本地 10 上的孤儿 worker 用死库领走消息的事故 = 随机化的动机）。
_DB_POOL = (10, 11, 12, 13, 14, 15)

_WORKER_BOOT_TIMEOUT = 90


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


def _pick_e2e_dbs() -> tuple[int, int]:
    import random

    broker = random.choice(_DB_POOL)
    backend = (broker + 1) % 16 if (broker + 1) not in (0, 1, 5, 6, 7, 8, 9) \
        else (broker - 1) % 16
    return broker, backend


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
        from app.services.jobs import worker as jobs_worker_mod
        from app.tools import _utils as tools_utils

        self._patched_factories = (jobs_worker_mod, tools_utils)

        def _shared_factory():
            return self.factory()

        monkeypatch.setattr(jobs_worker_mod, "_default_session_factory",
                            _shared_factory)
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
                "--loglevel=WARNING", "--without-gossip", "--without-mingle",
                "-Q", "celery,light_cpu_queue,heavy_cpu_queue,"
                      "high_memory_queue,raster_queue,network_queue,"
                      "external_io_queue",
            ],
            env=env, cwd=str(REPO),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        self.worker_log_path = tmp_path / f"worker-{next(_proc_counter)}.log"
        self._worker_log_fh = open(self.worker_log_path, "w")
        proc.stdout = getattr(proc, "stdout")  # keep reader accessible
        import threading as _th

        _th.Thread(
            target=lambda: [
                self._worker_log_fh.write(line)
                for line in iter(proc.stdout.readline, "")
            ],
            daemon=True,
        ).start()
        if wait:
            deadline = time.monotonic() + _WORKER_BOOT_TIMEOUT
            from app.services.task_queue import celery_app

            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError("worker exited during boot")
                try:
                    if celery_app.control.ping(timeout=2):
                        break
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(1)
            else:
                proc.kill()
                raise RuntimeError("worker did not become ready in time")
        self.worker_proc = proc
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
    broker_db, backend_db = _pick_e2e_dbs()
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


def _submit_run(env: _ClusterEnv, plan: dict) -> str:
    from app.services.geocompute.cluster.store import ClusterRunStore

    return ClusterRunStore(env.factory).create_run(
        plan_snapshot=plan,
        plan_fingerprint="v7e2e",
        owner_scope="u:e2e",
        session_id="e2e-sess",
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


def _wait_event(events: RunEventStore, run_id: str, name: str,
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
        rid = _submit_run(env, plan)
        row = _wait_terminal(env, rid, timeout=180)
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
        """worker dies after dispatch → 生产 stale sweep（真实代码路径，
        测试时钟 5s）→ WORKER_LOSS → 有界重派 → 新 worker 完成；事件链
        诚实（node_lost + 重派 dispatched）。"""
        env, events = cluster
        env.start_worker()
        env.start_coordinator()
        plan = _plan_chain("e2e-crash", rows=2500, retries=2)
        rid = _submit_run(env, plan)
        # 第一跳已被 worker 领取
        assert _wait_event(events, rid, "node_started", timeout=90) is not None
        # 杀死 worker（SIGKILL：无清理 —— 真实 crash 语义）
        env.kill_worker()
        # 驱动**生产同一** stale 收敛路径（jobs.DurableJobStore.sweep_stale；
        # stale_after_s=5 是测试时钟 —— 生产默认 300s，语义同一代码）
        import asyncio

        from sqlalchemy.ext.asyncio import (
            async_sessionmaker,
            create_async_engine,
        )

        from app.services.jobs.store import DurableJobStore

        async def _drive() -> int:
            eng = create_async_engine(f"sqlite+aiosqlite:///{env.db_path}")
            maker = async_sessionmaker(eng, expire_on_commit=False)
            swept_total = 0
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                async with maker() as db:
                    swept = await DurableJobStore.sweep_stale(
                        db, stale_after_s=5)
                    # 生产调用方（lifespan sweep）的 session 上下文负责
                    # commit —— store.transition 本身不提交，这里同契约
                    # 显式提交，否则 stale 迁移随 session 关闭回滚。
                    await db.commit()
                if swept:
                    swept_total += swept
                    break
                await asyncio.sleep(1.0)
            await eng.dispose()
            return swept_total

        swept = asyncio.run(_drive())
        assert swept >= 1, "stale sweep did not converge the dead worker job"
        # 新 worker 上线 → attempt 2 重派被新 worker 领走
        env.start_worker()
        row = _wait_terminal(env, rid, timeout=240)
        assert row.get("status") == "completed", (
            f"run not completed after worker crash: {row} "
            f"events={events.window(rid, limit=200)}")
        ev_names = [e["event"] for e in events.window(rid, limit=200)]
        assert "node_lost" in ev_names
        # attempt 2 的重派真实发生（n1 两次 + n2 一次 ≥ 3）
        assert ev_names.count("node_dispatched") >= 3

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
        rid = _submit_run(env, plan)
        assert _wait_event(events, rid, "node_started", timeout=90) is not None
        from app.services.geocompute.cluster.store import ClusterRunStore

        store = ClusterRunStore(env.factory)
        changed, observed = store.request_cancel(rid)
        assert changed, "cancel flag not accepted"
        deadline = time.monotonic() + 90
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
        rid = _submit_run(env, plan)
        row = _wait_terminal(env, rid, timeout=180)
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
                    "session_id": "e2e-sess",
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
        rid = _submit_run(env, plan)
        assert _wait_terminal(env, rid, timeout=180).get("status") == "completed"
        # 重启 worker：kill → prune → 新 worker 注册 → 新 run 完成
        env.kill_worker()
        time.sleep(1)
        env.start_worker()
        plan2 = _plan_chain("e2e-restart-2", rows=10)
        rid2 = _submit_run(env, plan2)
        row = _wait_terminal(env, rid2, timeout=180)
        assert row.get("status") == "completed", (
            f"run after worker restart not completed: {row}")
