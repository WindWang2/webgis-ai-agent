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

    # 聚合器更新留到 Task 6
