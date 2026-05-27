"""工具调用计时 — 每次 dispatch 一行 JSONL + 进程级聚合器 + digest 输出。

入口:
    record_tool_call(...)  # 在 registry.dispatch 包装里调用
    emit_digest()          # 在 FastAPI lifespan shutdown 时调用

文件: logs/tool_metrics.jsonl (10MB 轮转，5 备份)。
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# 路径可在测试中 monkeypatch 替换。
LOG_PATH = os.path.join("logs", "tool_metrics.jsonl")

_DIGEST_EVERY_N = 100

# 聚合器：tool_name → [count, total_ms, max_ms, hit_count, error_count]
_aggregator: dict[str, list[int]] = {}
_call_counter: int = 0
_lock = threading.Lock()


def _reset_for_tests() -> None:
    global _aggregator, _call_counter
    with _lock:
        _aggregator = {}
        _call_counter = 0


def _ensure_log_dir() -> None:
    d = os.path.dirname(LOG_PATH)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def _write_jsonl_line(line: str) -> None:
    _ensure_log_dir()
    # 简单追加；轮转留到后续用 RotatingFileHandler 接管。
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


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
    """落一行 JSONL + 更新聚合器。失败时仅 logger.warning，不抛。"""
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
    line = json.dumps(row, separators=(",", ":"))
    try:
        _write_jsonl_line(line)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[tool_metrics] write failed (dropping row): {type(e).__name__}: {e}")

    _update_aggregator(tool, duration_ms, cache_hit, error)


def _update_aggregator(tool: str, duration_ms: int, cache_hit: bool, error: Optional[str]) -> None:
    global _call_counter
    with _lock:
        slot = _aggregator.setdefault(tool, [0, 0, 0, 0, 0])
        # [count, total_ms, max_ms, hit_count, error_count]
        slot[0] += 1
        slot[1] += duration_ms
        if duration_ms > slot[2]:
            slot[2] = duration_ms
        if cache_hit:
            slot[3] += 1
        if error:
            slot[4] += 1
        _call_counter += 1
        should_digest = (_call_counter % _DIGEST_EVERY_N == 0)
    if should_digest:
        emit_digest()


def aggregator_snapshot() -> dict:
    """聚合器只读快照，便于测试 / dashboard."""
    with _lock:
        return {
            t: {
                "count": v[0],
                "total_ms": v[1],
                "max_ms": v[2],
                "hit_count": v[3],
                "error_count": v[4],
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
        # top 5 by max_ms (p99 proxy)
        top_max = sorted(
            _aggregator.items(), key=lambda kv: kv[1][2], reverse=True
        )[:5]
        errors = [(t, v[4]) for t, v in _aggregator.items() if v[4] > 0]

    cum_str = ",".join(f'("{t}",{v[1]},{v[0]},{v[3]})' for t, v in top_cum)
    max_str = ",".join(f'("{t}",{v[2]})' for t, v in top_max)
    err_str = ",".join(f'("{t}",{n})' for t, n in errors)
    logger.info(
        f"TOOL_METRICS_DIGEST n={n} top_cumulative=[{cum_str}] top_p99=[{max_str}] errors=[{err_str}]"
    )
