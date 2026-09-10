"""测试专用确定性故障注入框架（ADR-0104 Wave 7+8）。

**生产关闭是结构性的**：本模块只存在于 ``tests/`` 包内，``app/`` 任何代码
都不允许 import 它（由 tests/quality/test_chaos_foundation.py 的结构测试
对 app/ 全量源码扫描锁定）。没有环境开关、没有生产死代码。

设计约束（与仓内既有 chaos 套件同一纪律）：

- **只用既有接缝**：monkeypatch 式补丁（unittest.mock.patch / 手工
  save-restore）、补丁超时常量、``asyncio.Event`` 屏障、确定性假 Redis
  客户端（fakeredis 语义子集）、``httpx.MockTransport``。绝不为了注入
  而改生产代码；生产侧没有 chaos 分支。
- **确定性 schedule**：只接受显式次数/序列（``times=``、``fail_after=``、
  ``cancel_at_chunk=`` 等参数）；无 RNG、无同步用途的 sleep（顺序协调
  一律 Event/计数屏障；等待补丁后时钟的轮询只用有界小步长）。
- **journal**：每次注入都记录 ``armed → fired* → disarmed`` 事件序列，
  测试必须同时断言「故障真的打中了」（``handle.fired`` / 事件明细）和
  系统的恢复/诚实失败行为。

用法::

    from tests.fixtures.chaos import chaos

    with chaos("CACHE_COPY_FAIL") as fault:
        path = publish_artifact(key, src, compute)
    assert fault.fired                     # 故障确实开火
    assert get_artifact(key) is None       # 系统行为：无半截发布

故障注册表：``FAULTS``（id → FaultSpec）；``fault_catalog()`` 导出为
纯数据 dict 列表，由 ``scripts/gen_chaos_registry.py`` 渲染成
``docs/quality/certifications/CHAOS_FAULT_REGISTRY.md``（字节一致性闸）。

显式缺口（审计 05 Top-25 中**未**注册的项及原因）：

- export PNG / report HTML 写失败（#13）：写路径没有模块级可打补丁的
  接缝（直接 ``open().write`` 内联在生产函数体内），注入需改生产代码
  —— 按「不 touch 生产」约束跳过。
- cache_broadcast 监听重连（#9）：守护循环用真实 5s 重试 sleep，无法
  在不改生产代码的前提下确定性驱动；且 ADR-0101 的 TTL/指纹权威已把
  爆炸半径限为陈旧窗口 —— 跳过。
- ``sanitize_json_obj`` NaN/Inf（#11）：纯函数，无故障窗口，属于普通
  单测网格而非故障注入 —— 跳过（留给 unit lane）。
- 并发重复摄入（INGEST_DUP_RACE）：竞态被**钉死现状**（两个 ref 都
  成功 = 文档化残余风险，审计 #6）；管线的修复受本波次只读约束，
  不在生产代码里做。

Quality V3（Epic 10 W13）跨系统场景归属（诚实披露，避免重复注册伪装
成新增覆盖）：

- SSE 断线重连 / Last-Event-ID 重放 / 重复投递语义：由
  ``tests/test_runtime_chaos_resume.py``（28 项专项行为套件）覆盖，
  不另注册注入型 fault；
- API / coordinator 进程重启、worker 进程损失（真实 OS 进程级）：
  ``scripts/integration_harness.py``（--chaos，kill -9 → 重启 → 恢复）；
- Redis 瞬时故障：锁域由 LOCK_RENEW_ERROR / LOCK_ACQUIRE_DEGRADE 覆盖；
  真实服务瞬时断连由 ``tests/integration/test_real_services_lane.py``
  （REAL_SERVICES opt-in）覆盖。health 端点的内联 redis 连接无接缝，
  不谎报可注入。
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import os
import tempfile
import threading
import unittest.mock
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

# ── journal：每次注入做了什么，全量可审计 ─────────────────────────────────


@dataclass(frozen=True)
class ChaosEvent:
    """单条注入事件（fault 生命周期：armed → fired* → disarmed）。"""

    fault_id: str
    seq: int
    action: str
    detail: str


_JOURNAL: list[ChaosEvent] = []
_JOURNAL_LOCK = threading.Lock()
_JOURNAL_SEQ = itertools.count(1)
_JOURNAL_CAP = 4096  # 有界：异常测试泄漏时不无界增长


def _emit(fault_id: str, action: str, detail: str = "") -> ChaosEvent:
    ev = ChaosEvent(fault_id, next(_JOURNAL_SEQ), action, detail)
    with _JOURNAL_LOCK:
        _JOURNAL.append(ev)
        if len(_JOURNAL) > _JOURNAL_CAP:  # 丢最旧的，保最新
            del _JOURNAL[: len(_JOURNAL) - _JOURNAL_CAP]
    return ev


def journal_snapshot() -> tuple[ChaosEvent, ...]:
    """当前进程全部注入事件（测试断言 / 诊断用）。"""
    with _JOURNAL_LOCK:
        return tuple(_JOURNAL)


def reset_journal() -> None:
    """清空全局 journal（测试隔离 fixture 用）。"""
    with _JOURNAL_LOCK:
        _JOURNAL.clear()


# ── 注册表条目 ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FaultSpec:
    """一个命名故障点：ID + 文档 + 注入工厂（``<SUBSYS>_<FAULT>`` 命名）。"""

    fault_id: str
    subsystem: str
    description: str
    attack: str       # 注入方式 / 所用接缝
    expected: str     # 期望系统行为（测试断言的对象）
    injection_point: str  # file:line 证据（审计 05）
    factory: Callable[["ChaosFault"], Any]  # contextmanager factory(handle)

    def as_dict(self) -> dict:
        """纯数据投影（fault_catalog / 注册表文档渲染用，不含工厂对象）。"""
        return {
            "fault_id": self.fault_id,
            "subsystem": self.subsystem,
            "description": self.description,
            "attack": self.attack,
            "expected": self.expected,
            "injection_point": self.injection_point,
        }


class ChaosFault:
    """一次注入的句柄 = context manager + journal + 故障参数出口。

    工厂可在 yield 前往句柄上挂东西（``fault.client``、``fault.token``、
    ``fault.handles`` …），测试在 with 体内直接使用。
    """

    def __init__(self, spec: FaultSpec, params: dict):
        self.spec = spec
        self.params = params
        self.fault_id = spec.fault_id
        self.events: list[ChaosEvent] = []
        self._cm: Any = None
        # 工厂附加属性（client / token / handles / state …）
        self.extra: dict[str, Any] = {}

    # -- journal ----------------------------------------------------------
    def record(self, action: str, detail: str = "") -> ChaosEvent:
        ev = _emit(self.fault_id, action, detail)
        self.events.append(ev)
        return ev

    @property
    def fired(self) -> bool:
        """故障是否真的开过火（armed 不算 —— 必须观察到实际注入动作）。"""
        return any(e.action == "fired" for e in self.events)

    # -- context manager --------------------------------------------------
    def __enter__(self) -> "ChaosFault":
        self.record("armed", self.spec.attack)
        self._cm = self.spec.factory(self)
        self._cm.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            return bool(self._cm.__exit__(exc_type, exc, tb))
        finally:
            self.record("disarmed", "")

    def __getattr__(self, name: str) -> Any:
        # 仅在正常属性缺失时走 extra（避免干扰 record/fired 等真实属性）
        try:
            return self.__dict__["extra"][name]
        except KeyError:
            raise AttributeError(name) from None


def chaos(fault_id: str, /, *, times: Optional[int] = None, **params: Any) -> ChaosFault:
    """按稳定 fault ID 构造一次故障注入（context manager）。

    ``times``：故障开火的显式次数上限（None = 按各故障默认 / 永远）。
    所有 schedule 参数都是显式计数 —— 无 RNG、无 wall-clock 依赖。
    未注册的 ID 直接 KeyError（防手滑打出无人听单的故障）。
    """
    spec = FAULTS.get(fault_id)
    if spec is None:
        raise KeyError(
            f"unregistered chaos fault {fault_id!r}; see tests/fixtures/chaos.py FAULTS"
        )
    if times is not None:
        params.setdefault("times", times)
    return ChaosFault(spec, params)


# ── 接缝 1：确定性假 Redis 客户端（分布式锁 / registry 用）────────────────


class ChaosLockClient:
    """fakeredis 语义的极小确定性故障客户端（无网络、无 TTL 时钟）。

    - ``acquire_error``：SET NX 一律抛错（LOCK_ACQUIRE_DEGRADE /
      REGISTRY_DEGRADED_EXCLUSION_DROP）；
    - ``renew_fail_times``：RENEW eval 连续抛 ``ConnectionError`` 的显式
      次数（None = 永远；0 = 不注入；耗尽后恢复真实 token 语义）
      （LOCK_RENEW_ERROR）。

    注：不模拟 px 过期 —— 锁丢失语义由「eval 抛错 / 返回 0」驱动，
    与生产 Lua 脚本的真实判定一致。
    """

    def __init__(
        self,
        *,
        acquire_error: Optional[BaseException] = None,
        renew_fail_times: Optional[int] = None,
        on_event: Optional[Callable[[str, str], Any]] = None,
    ):
        self._acquire_error = acquire_error
        self._renew_fail_times = renew_fail_times
        self._on_event = on_event
        self._store: dict[str, str] = {}
        self.acquire_calls = 0
        self.renew_calls = 0

    def _emit(self, action: str, detail: str) -> None:
        if self._on_event is not None:
            self._on_event(action, detail)

    async def set(self, key: str, value: str, nx: bool = False, px: int | None = None):
        self.acquire_calls += 1
        if self._acquire_error is not None:
            self._emit("fired", f"SET {key} -> {type(self._acquire_error).__name__}")
            raise self._acquire_error
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def eval(self, script: bytes, numkeys: int, *args):
        key = args[0]
        if b"pexpire" in script:  # token-checked renew（生产 _RENEW_SCRIPT）
            self.renew_calls += 1
            if self._renew_fail_times is None or self.renew_calls <= self._renew_fail_times:
                self._emit("fired", f"renew #{self.renew_calls} -> ConnectionError")
                raise ConnectionError("chaos: redis renew eval failed")
            if self._store.get(key) != args[1]:
                return 0  # token 已易主 / 键消失 → 真实「丢失」判定
            return 1
        if b"'del'" in script or b"del" in script:  # token-checked release
            if self._store.get(key) == args[1]:
                self._store.pop(key, None)
                return 1
            return 0
        raise AssertionError(f"ChaosLockClient: unexpected script {script[:48]!r}")


def asyncio_event():
    """Event 工厂（3.13 起 Event 不绑 loop；独立小工厂便于统一替换）。"""
    import asyncio

    return asyncio.Event()


# ── 接缝 2：os 模块代理（仅对特定 dst 抛 OSError）─────────────────────────


class _FailOnReplaceOS:
    """``os`` 透传代理：``replace(src, dst)`` 命中 dst 后缀时抛 ENOSPC。"""

    def __init__(self, real_os, dst_suffix: str, on_fire=None):
        self._real = real_os
        self._suffix = dst_suffix
        self._on_fire = on_fire

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def replace(self, src, dst):  # noqa: A003 — 与 os.replace 同签名
        if str(dst).endswith(self._suffix):
            if self._on_fire is not None:
                self._on_fire("fired", f"os.replace({dst}) -> OSError(ENOSPC)")
            raise OSError(28, "chaos: simulated ENOSPC on artifact publish replace")
        return self._real.replace(src, dst)


# ── 接缝 3：LLM MockTransport 公共壳（test_provider_contract_v2 同款）──────


@contextlib.contextmanager
def _llm_mock_transport(handler, handle: ChaosFault) -> Iterator[None]:
    import httpx

    import app.services.chat.llm_client as llm_client

    llm_client._registry._reset_for_tests()
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return real_async_client(*args, **kwargs)

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            unittest.mock.patch.object(llm_client.httpx, "AsyncClient", _factory)
        )
        # 重试退避常量补丁为 0：分类语义不变，测试不睡 0.5s 真钟
        stack.enter_context(
            unittest.mock.patch.object(llm_client, "_RETRY_BACKOFF_S", 0.0)
        )
        try:
            yield
        finally:
            llm_client._registry._reset_for_tests()


# ── 各故障工厂（contextmanager；yield 前布置接缝，必要时挂句柄属性）───────


@contextlib.contextmanager
def _cache_cap_shrink(handle: ChaosFault) -> Iterator[None]:
    """补丁字节上限常量 → 下一次 publish 必然触发 LRU 驱逐路径。"""
    import app.lib.artifact_cache as ac

    max_bytes = int(handle.params.get("max_bytes", 1))
    with unittest.mock.patch.object(ac, "MAX_ARTIFACT_BYTES", max_bytes):
        handle.record("fired", f"MAX_ARTIFACT_BYTES -> {max_bytes}")
        yield


@contextlib.contextmanager
def _cache_copy_fail(handle: ChaosFault) -> Iterator[None]:
    """os.replace（publish 原子改名）对 .tif 目标抛 ENOSPC。"""
    import app.lib.artifact_cache as ac

    suffix = str(handle.params.get("dst_suffix", ".tif"))
    proxy = _FailOnReplaceOS(os, suffix, on_fire=handle.record)
    with unittest.mock.patch.object(ac, "os", proxy):
        yield  # 是否真打中由 publish 是否走到 replace 决定；测试断言 fault.fired


@contextlib.contextmanager
def _cache_meta_write_fail(handle: ChaosFault) -> Iterator[None]:
    """_meta_path 指向必然打不开的黑洞目录 → .meta 落盘 OSError。"""
    import app.lib.artifact_cache as ac

    blackhole = str(
        handle.params.get(
            "meta_dir",
            os.path.join(
                tempfile.gettempdir(), "chaos-meta-blackhole-must-not-exist"
            ),
        )
    )

    def _blackhole_meta_path(key: str) -> str:
        return os.path.join(blackhole, f"{key}.meta")

    with unittest.mock.patch.object(ac, "_meta_path", _blackhole_meta_path):
        handle.record("fired", f"_meta_path -> {blackhole}（open 必然 OSError）")
        yield


@contextlib.contextmanager
def _cache_meta_corrupt(handle: ChaosFault) -> Iterator[None]:
    """把指定 key 的 .meta 覆写成坏字节（字节级损坏 —— 既有套件同款）。"""
    import app.lib.artifact_cache as ac

    key = str(handle.params.get("key", ""))
    if not key:
        raise ValueError("CACHE_META_CORRUPT 需要 params['key']")
    payload = handle.params.get("payload", b"{not-json-corrupt")
    os.makedirs(ac.ARTIFACT_DIR, exist_ok=True)
    meta = ac._meta_path(key)
    with open(meta, "wb") as f:
        f.write(payload)
    handle.record("fired", f"corrupted {meta} ({len(payload)} bytes of garbage)")
    yield


@contextlib.contextmanager
def _lock_renew_error(handle: ChaosFault) -> Iterator[None]:
    """RENEW eval 连续抛 ConnectionError（times 次后恢复 / None = 永远）。"""
    import app.services.distributed_lock as dl

    times = handle.params.get("times")
    interval_s = float(handle.params.get("interval_s", 0.01))
    client = ChaosLockClient(
        renew_fail_times=times if times is None else int(times),
        on_event=handle.record,
    )
    handle.client = client
    # 补丁续约间隔常量（仓内既有模式：tiny patched timeout constants）
    with unittest.mock.patch.object(dl, "_RENEW_INTERVAL_S", interval_s):
        yield


@contextlib.contextmanager
def _lock_acquire_degrade(handle: ChaosFault) -> Iterator[None]:
    """SET NX 一律抛 ConnectionError → acquire 降级路径。"""
    client = ChaosLockClient(
        acquire_error=ConnectionError("chaos: redis acquire refused"),
        on_event=handle.record,
    )
    handle.client = client
    yield


@contextlib.contextmanager
def _registry_degraded(handle: ChaosFault) -> Iterator[None]:
    """registry 关键段的 session lock 被迫降级（Redis 不可达）。

    - 补丁 SessionLockRegistry._get_client → 永远 SET 失败的假客户端；
    - 同时把 USE_REDIS 拧成 true（save/restore），让 ``redis_configured``
      语义为「配置了 Redis 但它挂了」；
    - spy 包一层 ``lock()``，句柄上收集真实锁对象（``fault.handles``），
      测试据此断言 ``mode == "degraded"``（跨 Pod 互斥被静默丢弃的可观测
      证据）。
    """
    from app.services.distributed_lock import session_lock_registry

    client = ChaosLockClient(
        acquire_error=ConnectionError("chaos: redis down during registry write"),
        on_event=handle.record,
    )
    handle.client = client
    handles: list[Any] = []
    handle.handles = handles

    real_lock = session_lock_registry.lock

    def _spy_lock(session_id, **kwargs):
        lock_handle = real_lock(session_id, **kwargs)
        handles.append(lock_handle)
        return lock_handle

    saved_env = {k: os.environ.get(k) for k in ("USE_REDIS", "REDIS_URL")}
    os.environ["USE_REDIS"] = "true"
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                unittest.mock.patch.object(
                    session_lock_registry, "_get_client", lambda: client
                )
            )
            stack.enter_context(
                unittest.mock.patch.object(session_lock_registry, "lock", _spy_lock)
            )
            handle.record("fired", "session_lock_registry._get_client -> failing client")
            yield
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _ingest_dup_race(handle: ChaosFault) -> Iterator[None]:
    """Event 屏障：放行 wait_for 个并发 dedup 检查后再继续（确定性竞态窗）。"""
    from app.services.data_ingest import pipeline as pipeline_mod

    wait_for = int(handle.params.get("wait_for", 2))
    real = pipeline_mod.IngestPipeline._find_duplicate
    state = {"arrived": 0}
    handle.state = state
    gate = asyncio_event()

    async def _gated(self, session_id, fingerprint):
        res = await real(self, session_id, fingerprint)
        state["arrived"] += 1
        if state["arrived"] >= wait_for and not gate.is_set():
            gate.set()
            handle.record(
                "fired",
                f"{state['arrived']} concurrent dedup checks passed before either registered",
            )
        await asyncio.wait_for(gate.wait(), timeout=5)  # R1 review：序列化变更时 fail-fast
        return res

    with unittest.mock.patch.object(
        pipeline_mod.IngestPipeline, "_find_duplicate", _gated
    ):
        yield


@contextlib.contextmanager
def _ingest_register_fail(handle: ChaosFault) -> Iterator[None]:
    """第 fail_after+1 次 register_artifact 起返回 None（注册被拒）。"""
    import asyncio

    import app.services.artifact_registry as ar

    fail_after = int(handle.params.get("fail_after", 1))
    state = {"calls": 0}
    handle.state = state
    real = ar.register_artifact
    count_lock = asyncio.Lock()

    async def _counting(*args, **kwargs):
        async with count_lock:
            state["calls"] += 1
            n = state["calls"]
        if n > fail_after:
            handle.record("fired", f"register_artifact call #{n} declined (returns None)")
            return None
        return await real(*args, **kwargs)

    with unittest.mock.patch.object(ar, "register_artifact", _counting):
        yield


@contextlib.contextmanager
def _cancel_mid_write(handle: ChaosFault) -> Iterator[None]:
    """CURRENT_TOKEN 接缝：第 cancel_at_chunk 个 chunk 写完后触发 cancel()。"""
    from app.lib.cancellation import CancellationToken, use_token

    cancel_at = int(handle.params.get("cancel_at_chunk", 2))
    token = CancellationToken(job_id=handle.params.get("job_id", "chaos-cancel-write"))
    counter = {"chunks": 0}
    handle.token = token
    handle.counter = counter

    def gate() -> bool:
        """挂进写入循环：第 N 块后取消（返回 cancel() 是否首次生效）。"""
        counter["chunks"] += 1
        if counter["chunks"] >= cancel_at:
            fired = token.cancel(reason=f"chaos: cancel at chunk {counter['chunks']}")
            if fired:
                handle.record("fired", f"cancel() after chunk {counter['chunks']}")
            return fired
        return False

    handle.gate = gate
    with use_token(token):
        yield


@contextlib.contextmanager
def _llm_timeout(handle: ChaosFault) -> Iterator[None]:
    """MockTransport handler 抛 httpx.ReadTimeout（读取相位超时）。"""
    import httpx

    def _handler(request):
        handle.record("fired", "httpx.ReadTimeout raised from MockTransport handler")
        raise httpx.ReadTimeout("chaos: read timed out")

    with _llm_mock_transport(_handler, handle):
        yield


@contextlib.contextmanager
def _llm_malformed_stream(handle: ChaosFault) -> Iterator[None]:
    """截断 SSE：内容帧后直接 EOF（无 finish_reason 帧、无 [DONE]）。"""
    import json as _json

    import httpx

    body = str(
        handle.params.get(
            "body",
            "data: "
            + _json.dumps(
                {"choices": [{"delta": {"content": "半截"}, "finish_reason": None}]}
            )
            + "\n\n",
        )
    )

    def _handler(request):
        handle.record("fired", f"truncated SSE body ({len(body)} bytes, no [DONE])")
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    with _llm_mock_transport(_handler, handle):
        yield


# ── 故障注册表（稳定 ID；增删必须同步再生注册表文档）──────────────────────

@contextlib.contextmanager
def _storage_transient_fail(handle: ChaosFault) -> Iterator[None]:
    """制品账本存储后端瞬时写失败：前 fail_times 次 store/overwrite 抛
    OSError（模拟 Redis/磁盘抖动），之后透传恢复。"""
    import asyncio

    from app.services.session_data import session_data_manager as sdm

    fail_times = int(handle.params.get("fail_times", 1))
    state = {"calls": 0}
    handle.state = state
    real_store = sdm.store
    real_overwrite = sdm.overwrite
    lock = asyncio.Lock()

    async def _flaky_store(session_id, data, prefix="data"):
        async with lock:
            state["calls"] += 1
            n = state["calls"]
        if n <= fail_times:
            handle.record("fired", f"store call #{n} transient OSError")
            raise OSError(f"[chaos] transient storage failure #{n}")
        return await real_store(session_id, data, prefix=prefix)

    async def _flaky_overwrite(session_id, ref_id, data):
        async with lock:
            state["calls"] += 1
            n = state["calls"]
        if n <= fail_times:
            handle.record("fired", f"overwrite call #{n} transient OSError")
            raise OSError(f"[chaos] transient storage failure #{n}")
        return await real_overwrite(session_id, ref_id, data)

    with unittest.mock.patch.object(sdm, "store", _flaky_store),             unittest.mock.patch.object(sdm, "overwrite", _flaky_overwrite):
        yield


# ── Quality V3（Epic 10 W13）跨系统 fault ──────────────────────────────

@contextlib.contextmanager

def _worker_loss(handle: ChaosFault) -> Iterator[None]:
    """worker 心跳丢失：find_stale 被强制以 stale_after_s=0 运行（等价于
    worker 心跳已停更久），sweep 把 running/cancelling 收敛到终态。"""
    from app.services.jobs.store import DurableJobStore

    real_find_stale = DurableJobStore.find_stale

    async def _hyper_stale_find(db, *, stale_after_s=None, limit=100):
        handle.record("fired", "find_stale forced stale_after_s=0")
        return await real_find_stale(db, stale_after_s=0, limit=limit)

    DurableJobStore.find_stale = staticmethod(_hyper_stale_find)
    try:
        yield
    finally:
        DurableJobStore.find_stale = staticmethod(real_find_stale)


@contextlib.contextmanager

def _cancel_storm(handle: ChaosFault) -> Iterator[None]:
    """取消风暴：编排 N 个并发 cancel()（Event 屏障同步），句柄暴露
    token 与 task 列表；测试断言恰好一次胜出转移 + 状态机无污染。
    纯编排注入（不 patch 生产）——接缝是 CancellationToken.cancel 的
    幂等/CAS 语义本身。"""
    import asyncio

    barrier = asyncio.Barrier(int(handle.params.get("concurrency", 8)))
    handle.barrier = barrier
    handle.results = []
    handle.record("armed", f"cancel storm: {barrier.parties} 路并发 cancel 待屏障放行")
    yield


@contextlib.contextmanager

def _stale_revision_cas(handle: ChaosFault) -> Iterator[None]:
    """stale revision CAS：编排同一 job 的两次并发转移，后者携带已被
    胜出转移作废的 expected 状态集——store.transition 必须诚实拒绝。
    纯编排注入（接缝 = transition 的 expected CAS 语义）。"""

    handle.results = []
    handle.record("armed", "stale revision CAS: 确定性交错两路 transition")
    yield


@contextlib.contextmanager

def _db_transient_sequence(handle: ChaosFault) -> Iterator[None]:
    """DB 瞬时故障序列：前 fail_times 次 DurableJobStore.create 抛
    SQLAlchemy 类型化异常（DB 连接抖动），之后透传恢复。"""
    from sqlalchemy import exc as sa_exc

    from app.services.jobs.store import DurableJobStore

    fail_times = int(handle.params.get("fail_times", 1))
    state = {"calls": 0}
    handle.state = state
    real_create = DurableJobStore.create

    async def _flaky_create(db, **kwargs):
        state["calls"] += 1
        n = state["calls"]
        if n <= fail_times:
            handle.record("fired", f"create call #{n} transient DB failure")
            raise sa_exc.OperationalError(
                "CREATE", {}, Exception("[chaos] transient db failure"))
        return await real_create(db, **kwargs)

    DurableJobStore.create = staticmethod(_flaky_create)
    try:
        yield
    finally:
        DurableJobStore.create = staticmethod(real_create)


_FAULT_LIST = [
    FaultSpec(
        fault_id="CACHE_CAP_SHRINK",
        subsystem="CACHE",
        description="制品缓存字节上限骤减，LRU 驱逐路径在压力下运行",
        attack="unittest.mock.patch 补丁 MAX_ARTIFACT_BYTES 常量（补丁超时/容量常量接缝）",
        expected="最老条目（.tif+.meta 成对）被驱逐，最新条目存活，缓存总字节回到上限内",
        injection_point="app/lib/artifact_cache.py:191 _evict_if_needed（审计 05 #1）",
        factory=_cache_cap_shrink,
    ),
    FaultSpec(
        fault_id="CACHE_COPY_FAIL",
        subsystem="CACHE",
        description="publish 的原子改名 os.replace 中途失败（ENOSPC）",
        attack="app.lib.artifact_cache.os 替换为仅 replace 抛 OSError 的透传代理（monkeypatch 接缝）",
        expected="tmp 临时件被清理、无半截发布可见（get_artifact=miss）、调用方拿到直出 fallback 路径",
        injection_point="app/lib/artifact_cache.py:165 publish_artifact（审计 05 #2）",
        factory=_cache_copy_fail,
    ),
    FaultSpec(
        fault_id="CACHE_META_WRITE_FAIL",
        subsystem="CACHE",
        description=".meta sidecar 在 .tif 发布后写失败",
        attack="_meta_path 补丁指向必然打不开的黑洞目录（monkeypatch 接缝）",
        expected="publish 不崩溃照常返回；读取侧诚实 miss（.meta 缺失不可见，孤儿清扫兜底）",
        injection_point="app/lib/artifact_cache.py:176-185 publish meta 写入（审计 05 #3）",
        factory=_cache_meta_write_fail,
    ),
    FaultSpec(
        fault_id="CACHE_META_CORRUPT",
        subsystem="CACHE",
        description=".meta sidecar 内容损坏（坏 JSON 字节）",
        attack="直接覆写 meta 文件为垃圾字节（字节级损坏接缝）",
        expected="get_artifact 诚实 miss（类型化吞掉 JSONDecodeError），绝不崩溃、绝不返回无 meta 的 .tif",
        injection_point="app/lib/artifact_cache.py:123-126 get_artifact（审计 05 #3）",
        factory=_cache_meta_corrupt,
    ),
    FaultSpec(
        fault_id="LOCK_RENEW_ERROR",
        subsystem="LOCK",
        description="分布式锁续约 eval 连续抛连接异常（Redis 抖动/持续错误）",
        attack="ChaosLockClient.renew_fail_times 显式次数 + 补丁 _RENEW_INTERVAL_S 常量",
        expected="瞬时抖动（预算内）被容忍、锁不丢；连续失败覆盖整个 TTL 窗口后 lost=True 且停止续约（fail_on_lost 调用方在退出时拿到 LockLostError）",
        injection_point="app/services/distributed_lock.py:239 _renew_loop except（审计 05 #4）",
        factory=_lock_renew_error,
    ),
    FaultSpec(
        fault_id="LOCK_ACQUIRE_DEGRADE",
        subsystem="LOCK",
        description="锁获取相位 Redis SET NX 抛连接错误",
        attack="ChaosLockClient.acquire_error（确定性假 Redis 接缝）",
        expected="fail_on_degraded=False 时透明降级 in-process（mode=degraded 可观测）；=True 时抛 LockDegradedError",
        injection_point="app/services/distributed_lock.py:185-197 __aenter__（审计 05 #5）",
        factory=_lock_acquire_degrade,
    ),
    FaultSpec(
        fault_id="REGISTRY_DEGRADED_EXCLUSION_DROP",
        subsystem="REGISTRY",
        description="artifact_registry 关键段拿到的 session lock 因 Redis 不可达降级 —— 跨 Pod 互斥被静默放弃",
        attack="补丁 SessionLockRegistry._get_client 返回 SET 必失败的假客户端 + USE_REDIS=true save/restore",
        expected="操作照常成功（fail_on_degraded=False 的文档化取舍）；降级可观测（lock.mode=='degraded'）；无崩溃",
        injection_point="app/services/artifact_registry.py:444,552,592 fail_on_degraded=False（审计 05 #5）",
        factory=_registry_degraded,
    ),
    FaultSpec(
        fault_id="INGEST_DUP_RACE",
        subsystem="INGEST",
        description="两路并发摄入同时通过 dedup 检查（check-then-act 竞态窗）",
        attack="Event 屏障包装 _find_duplicate：wait_for 个检查全部通过后才放行注册（asyncio 接缝）",
        expected="现状钉死：竞态窗内两个 ref 都注册成功（重复 ref 文档化为残余风险，见审计 #6）",
        injection_point="app/services/data_ingest/pipeline.py:157-166 dedup（审计 05 #6）",
        factory=_ingest_dup_race,
    ),
    FaultSpec(
        fault_id="INGEST_REGISTER_FAIL",
        subsystem="INGEST",
        description="并发摄入中一路的制品注册被拒（register 返回 None）",
        attack="计数包装 register_artifact：第 fail_after+1 次起返回 None（monkeypatch 接缝）",
        expected="失败方 ref 被补偿删除（无孤儿）、error_code=REGISTER_FAILED_ROLLED_BACK、成功方账本恰好一条",
        injection_point="app/services/data_ingest/pipeline.py:204-227 register+rollback（审计 05 #6）",
        factory=_ingest_register_fail,
    ),
    FaultSpec(
        fault_id="CANCEL_MID_WRITE",
        subsystem="CANCEL",
        description="制品分块写入中途到达取消信号",
        attack="CURRENT_TOKEN contextvar 绑定 CancellationToken，写入循环 chunk 边界触发 cancel()（生产 checkpoint 接缝）",
        expected="下一个 checkpoint 抛 OperationCancelled；无部分制品被晋升（缓存 miss、临时件被丢弃）",
        injection_point="app/lib/cancellation.py:163 checkpoint + cancellable（审计 05 §3.7）",
        factory=_cancel_mid_write,
    ),
    FaultSpec(
        fault_id="LLM_TIMEOUT",
        subsystem="LLM",
        description="LLM 读取相位超时（provider 挂起不响应）",
        attack="httpx.MockTransport handler 抛 ReadTimeout（transport 接缝，test_provider_contract_v2 同款）",
        expected="诚实抛错（不假成功）；读取超时不在连接相位重试白名单内，单次尝试即失败",
        injection_point="app/services/chat/llm_client.py:358-407 重试边界（审计 05 §3.1）",
        factory=_llm_timeout,
    ),
    FaultSpec(
        fault_id="LLM_MALFORMED_STREAM",
        subsystem="LLM",
        description="流式响应在 finish_reason/[DONE] 之前被截断",
        attack="MockTransport 返回只有内容帧的截断 SSE（transport 接缝）",
        expected="ProviderStreamTruncated 显式抛出，绝不把断流包装成 done 帧（防假成功）",
        injection_point="app/services/chat/llm_client.py:601-609 截断判定（审计 05 §3.1）",
        factory=_llm_malformed_stream,
    ),
    FaultSpec(
        fault_id="STORAGE_TRANSIENT_FAIL",
        subsystem="STORAGE",
        description="制品账本存储后端瞬时写失败（Redis/磁盘抖动）后恢复",
        attack="计数包装 session_data_manager.store/overwrite：前 fail_times 次抛 OSError（monkeypatch 接缝）",
        expected="调用方拿到类型化异常（不静默丢数据）；账本 alias 不前进（无半截提交）；恢复后重试成功",
        injection_point="app/services/artifact_registry.py:227-240 _save_records（Quality V2 W9）",
        factory=_storage_transient_fail,
    ),
    FaultSpec(
        fault_id="JOBS_WORKER_LOSS",
        subsystem="JOBS",
        description="worker 心跳丢失：running/cancelling job 心跳超时后无人续约",
        attack="包装 DurableJobStore.find_stale 强制 stale_after_s=0（monkeypatch 接缝；等价心跳停更），随后 sweep_stale 真实运行",
        expected="running → failed(stale)（可重试终态）；cancelling → cancelled（不给被取消任务开重跑后门）；重试转移 failed → queued 合法",
        injection_point="app/services/jobs/store.py:805-937 stale 清扫（Quality V3 W13）",
        factory=_worker_loss,
    ),
    FaultSpec(
        fault_id="CANCEL_STORM",
        subsystem="CANCEL",
        description="取消风暴：N 个并发请求同时取消同一 token",
        attack="asyncio.Barrier 编排 N 路并发 cancel()（纯编排注入；接缝 = CancellationToken.cancel 的幂等/CAS 语义）",
        expected="恰好一次返回 True（其余 False）；cancelled 状态与 cancelled_at 单调稳定；无异常泄漏",
        injection_point="app/lib/cancellation.py:71 cancel（Quality V3 W13）",
        factory=_cancel_storm,
    ),
    FaultSpec(
        fault_id="JOBS_STALE_REVISION_CAS",
        subsystem="JOBS",
        description="stale revision CAS：并发状态转移中失败方携带过期 expected",
        attack="确定性交错：胜者 transition 提交后，败者携带已作废的 expected 再 transition（纯编排注入）",
        expected="恰好一路成功；败者返回 False（诚实拒绝，不覆盖、不部分写）；终态 == 胜者目标",
        injection_point="app/services/jobs/store.py:384-445 transition CAS（Quality V3 W13）",
        factory=_stale_revision_cas,
    ),
    FaultSpec(
        fault_id="DB_TRANSIENT_SEQUENCE",
        subsystem="DB",
        description="DB 瞬时故障序列：job 创建路径前 N 次连接级失败",
        attack="包装 DurableJobStore.create，前 fail_times 次抛 sqlalchemy OperationalError（monkeypatch 接缝）",
        expected="类型化异常透传调用方（不静默吞）；无半截行落库；恢复后重试创建成功",
        injection_point="app/services/jobs/store.py:188-255 create（Quality V3 W13）",
        factory=_db_transient_sequence,
    ),
]

FAULTS: dict[str, FaultSpec] = {spec.fault_id: spec for spec in _FAULT_LIST}


def fault_catalog() -> list[dict]:
    """故障注册表导出（纯数据；供 gen_chaos_registry.py 渲染文档）。"""
    return [spec.as_dict() for spec in FAULTS.values()]
