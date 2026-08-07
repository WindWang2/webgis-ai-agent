"""工具调用计时 — JSONL writer（队列 + 后台线程）+ 进程级聚合器 + digest 输出。

入口:
    record_tool_call(...)  # 在 registry.dispatch 包装里调用
    emit_digest()          # 在 FastAPI lifespan shutdown 时调用

设计（ADR-0044）:
    record_tool_call 只做两件事: 内存聚合（锁保护）+ 把 JSONL 行放入有界队列。
    一个 daemon writer 线程批量落盘并执行真实的大小轮转（10MB × 5 备份）。
    事件循环上绝不发生文件 open/write。队列满时丢弃新行（背压），不阻塞调用方。

文件: logs/tool_metrics.jsonl (.1 ~ .5 为轮转备份)。
"""
import atexit
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from app.lib.harness.tool_call_event import ToolCallEvent

logger = logging.getLogger(__name__)

# 路径可在测试中 monkeypatch 替换。
LOG_PATH = os.path.join("logs", "tool_metrics.jsonl")

# ─── writer 线程参数 ─────────────────────────────────────────────
_MAX_QUEUE = 8192          # 有界队列: 满则丢行, 绝不阻塞事件循环
_MAX_LOG_BYTES = 10 * 1024 * 1024   # 10MB 轮转
_MAX_ROTATIONS = 5         # .1 ~ .5 备份
_FLUSH_BATCH = 512         # 批大小: 满批才落盘
_IDLE_FLUSH_S = 0.1        # 空闲时部分批的最长滞留

_DIGEST_EVERY_N = 100

# 聚合器：tool_name → [count, total_ms, max_ms, hit_count, error_count]
_aggregator: dict[str, list[int]] = {}
# 每工具 log2 时长直方图（有界: ~33 个 bin），用于真实 p50/p95/p99 估计。
_hist: dict[str, list[int]] = {}
_call_counter: int = 0
_lock = threading.Lock()

# ─── writer 线程 ─────────────────────────────────────────────────
_queue: "queue.Queue[Optional[str]]" = queue.Queue(maxsize=_MAX_QUEUE)
_STOP = object()
_writer_thread: Optional[threading.Thread] = None
_writer_started = False
_start_lock = threading.Lock()
# 尚未落盘的行数（入队 +1，writer 每批落盘 -N）。测试隔离用：_reset_for_tests
# 等待其归零，避免 in-flight batch 在 LOG_PATH 切换后写入下一个测试的文件。
#
# 跨线程记账：_pending_rows 由调用方线程（_enqueue +1）与 writer 线程
# （_flush_batch -N）共同修改。这里依赖 CPython GIL 让 int 的 += / -=
# 原子化（无锁安全）；改用 threading.Lock 会引入竞争点且收益为零，因为
# 该计数仅用于测试 quiescence 等待，非生产正确性路径。
_pending_rows = 0


def _ensure_writer() -> None:
    """Lazily start the daemon writer thread (first record call)."""
    global _writer_thread, _writer_started
    with _start_lock:
        if _writer_started:
            return
        _writer_started = True
        t = threading.Thread(target=_writer_loop, name="tool-metrics-writer", daemon=True)
        _writer_thread = t
        t.start()


def _flush_batch(batch: list[str]) -> None:
    """Flush one batch; the writer thread must never die on a bad batch."""
    global _pending_rows
    try:
        _append_batch(batch)
    except Exception as e:  # noqa: BLE001 — 单批失败不能杀死 writer 线程
        logger.exception(f"[tool_metrics] writer flush failed: {type(e).__name__}: {e}")
    finally:
        _pending_rows -= len(batch)


def _writer_loop() -> None:
    """Drain the queue in batches; flush partial batches after an idle gap."""
    batch: list[str] = []
    while True:
        try:
            line = _queue.get(timeout=_IDLE_FLUSH_S)
        except queue.Empty:
            if batch:
                _flush_batch(batch)
                batch = []
            continue
        if line is _STOP:
            if batch:
                _flush_batch(batch)
            return
        batch.append(line)
        if len(batch) >= _FLUSH_BATCH:
            _flush_batch(batch)
            batch = []


def _stop_writer() -> None:
    """Flush remaining rows at interpreter shutdown (atexit)."""
    try:
        _queue.put(_STOP, timeout=1.0)
    except queue.Full:
        pass  # writer 会在进程退出时随 daemon 消亡
    if _writer_thread is not None:
        _writer_thread.join(timeout=1.0)


atexit.register(_stop_writer)


def _ensure_log_dir() -> None:
    d = os.path.dirname(LOG_PATH)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def _rotate_if_needed() -> None:
    """Real size-based rotation: current → .1 → ... → .5 (oldest dropped)."""
    try:
        size = os.path.getsize(LOG_PATH)
    except OSError:
        return  # 还没有日志文件
    if size < _MAX_LOG_BYTES:
        return
    for i in range(_MAX_ROTATIONS, 1, -1):
        src = f"{LOG_PATH}.{i - 1}"
        if os.path.exists(src):
            os.replace(src, f"{LOG_PATH}.{i}")
    os.replace(LOG_PATH, f"{LOG_PATH}.1")


def _append_batch(lines: list[str]) -> None:
    """Append a batch with a single open; rotation + failures are contained.

    Callers (writer loop) wrap this in ``_flush_batch`` so a failure here can
    never kill the writer thread or wedge the pending-rows counter.
    """
    _rotate_if_needed()
    _ensure_log_dir()
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("".join(lines))


def _enqueue(line: str) -> None:
    global _pending_rows
    _ensure_writer()
    try:
        _queue.put_nowait(line)
        _pending_rows += 1
    except queue.Full:
        # 背压: 丢弃新行, 绝不阻塞事件循环。
        logger.warning("[tool_metrics] queue full — dropping row")


def _wait_idle(timeout: float = 5.0) -> None:
    """Wait until every enqueued row has been flushed by the writer thread.

    Tests switch LOG_PATH between tests; an in-flight writer batch would be
    appended to the *new* path. Fixtures must call this BEFORE switching the
    path (then _reset_for_tests), so the batch lands on the old path.
    """
    deadline = time.monotonic() + timeout
    while _pending_rows > 0 and time.monotonic() < deadline:
        time.sleep(0.01)


def _reset_for_tests() -> None:
    global _aggregator, _hist, _call_counter, _pending_rows
    with _lock:
        _aggregator = {}
        _hist = {}
        _call_counter = 0
    # 先让 writer 把已出队的 batch 落盘（旧 LOG_PATH），再清空队列。
    _wait_idle()
    while True:
        try:
            _queue.get_nowait()
            _pending_rows -= 1
        except queue.Empty:
            break


def record_tool_call(
    *,
    tool: str,
    arg_bytes: int,
    result_bytes: int,
    duration_ms: int,
    cache_hit: bool,
    error: Optional[str],
    session_id: Optional[str],
) -> None:
    """落一行 JSONL（入队，异步落盘）+ 更新聚合器。失败不抛。"""
    row = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "tool": tool,
        "session_id": session_id,
        "arg_bytes": arg_bytes,
        "result_bytes": result_bytes,
        "duration_ms": duration_ms,
        "cache_hit": cache_hit,
        "error": error,
    }
    line = json.dumps(row, separators=(",", ":")) + "\n"
    try:
        _enqueue(line)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[tool_metrics] enqueue failed (dropping row): {type(e).__name__}: {e}")

    _update_aggregator(tool, duration_ms, cache_hit, error, result_bytes=result_bytes)


def record_event(event: ToolCallEvent) -> None:
    """Record a tool call event from the unified telemetry model."""
    record_tool_call(
        tool=event.tool_name,
        arg_bytes=event.arg_bytes,
        result_bytes=event.result_bytes,
        duration_ms=event.duration_ms,
        cache_hit=event.cache_hit,
        error=event.error_msg if event.is_error else None,
        session_id=event.session_id,
    )


def _bin_index(duration_ms: int) -> int:
    """log2 分桶: bit_length 即 floor(log2)+1，最大约 32 个 bin（0.5ms ~ 数小时）。"""
    return max(1, duration_ms.bit_length())


def _percentiles(counts: list[int], total: int) -> dict[str, float]:
    """从 log2 直方图估计 p50/p90/p95/p99（bin 中点插值），样本不足时返回 0。"""
    if total <= 0:
        return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}
    out = {}
    for label, q in (("p50", 0.50), ("p90", 0.90), ("p95", 0.95), ("p99", 0.99)):
        target = q * total
        acc = 0
        est = 0.0
        for i, c in enumerate(counts):
            acc += c
            if acc >= target:
                # bin i 覆盖 [2^(i-1), 2^i) ms；取中点作为估计值。
                est = 2 ** (i - 1) * 1.5
                break
        out[label] = round(est, 1)
    return out


def _update_aggregator(tool: str, duration_ms: int, cache_hit: bool, error: Optional[str],
                       result_bytes: int = 0) -> None:
    global _call_counter
    with _lock:
        slot = _aggregator.setdefault(tool, [0, 0, 0, 0, 0, 0])
        # [count, total_ms, max_ms, hit_count, error_count, total_result_bytes]
        slot[0] += 1
        slot[1] += duration_ms
        if duration_ms > slot[2]:
            slot[2] = duration_ms
        if cache_hit:
            slot[3] += 1
        if error:
            slot[4] += 1
        slot[5] += result_bytes
        bins = _hist.setdefault(tool, [0] * 33)
        bins[_bin_index(duration_ms)] += 1
        _call_counter += 1
        should_digest = (_call_counter % _DIGEST_EVERY_N == 0)
    if should_digest:
        emit_digest()


def aggregator_snapshot() -> dict:
    """聚合器只读快照。

    含真实 p50/p90/p95/p99 估计（log2 直方图，非原始事件）与 max_ms（独立
    字段，勿与 p99 混淆 -- 后者是 bin 中点估计，max 是观测到的最大值）；
    result_bytes 为累计值，count 即调用数，二者相除得平均响应字节数。
    """
    with _lock:
        return {
            t: {
                "count": v[0],
                "total_ms": v[1],
                "max_ms": v[2],
                "hit_count": v[3],
                "error_count": v[4],
                "total_result_bytes": v[5],
                **_percentiles(_hist.get(t, []), v[0]),
            }
            for t, v in _aggregator.items()
        }


def emit_digest() -> None:
    """输出 TOOL_METRICS_DIGEST 一行总结。空聚合器时不输出。"""
    with _lock:
        if not _aggregator:
            return
        n = _call_counter
        # top 5 by cumulative ms
        top_cum = sorted(
            _aggregator.items(), key=lambda kv: kv[1][1], reverse=True
        )[:5]
        # top 5 by max_ms（真实 max，不是 p99）
        top_max = sorted(
            _aggregator.items(), key=lambda kv: kv[1][2], reverse=True
        )[:5]
        errors = [(t, v[4]) for t, v in _aggregator.items() if v[4] > 0]

    def _fmt_stats(tool: str, slot: list[int]) -> str:
        p = _percentiles(_hist.get(tool, []), slot[0])
        return (f'("{tool}",count={slot[0]},total_ms={slot[1]},'
                f'p50={p["p50"]},p90={p["p90"]},p95={p["p95"]},p99={p["p99"]},'
                f'max={slot[2]},hits={slot[3]},errors={slot[4]},result_bytes={slot[5]})')

    cum_str = ",".join(_fmt_stats(t, v) for t, v in top_cum)
    max_str = ",".join(f'("{t}",{v[2]})' for t, v in top_max)
    err_str = ",".join(f'("{t}",{n})' for t, n in errors)
    logger.info(
        f"TOOL_METRICS_DIGEST n={n} top_cumulative=[{cum_str}] top_max=[{max_str}] errors=[{err_str}]"
    )
