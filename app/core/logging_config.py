# ============================================================
# WebGIS AI Agent 应用日志配置（Python）
# ============================================================

import sys
import logging
import logging.handlers
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 创建日志目录
LOG_DIR = Path("./logs")
LOG_DIR.mkdir(exist_ok=True)

class RuntimeCorrelationFilter(logging.Filter):
    """从 RuntimeContext (ContextVar) 取关联字段注入每条 LogRecord。

    未绑定时字段置为占位符 "-"（便于 grep，且不清日志行），不抛。
    """

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        try:
            from app.lib.runtime.context import current_runtime_context
            ctx = current_runtime_context()
        except Exception:  # noqa: BLE001
            ctx = None
        record.request_id = getattr(ctx, "request_id", None) or "-" if ctx else "-"  # type: ignore[attr-defined]
        record.session_id = getattr(ctx, "session_id", None) or "-" if ctx else "-"  # type: ignore[attr-defined]
        record.turn_id = getattr(ctx, "turn_id", None) or "-" if ctx else "-"  # type: ignore[attr-defined]
        record.run_id = getattr(ctx, "run_id", None) or "-" if ctx else "-"  # type: ignore[attr-defined]
        return True


_RUNTIME_CORRELATION_FILTER = RuntimeCorrelationFilter()

# 定义日志格式（#691：带关联字段；未绑定时为 "-"，可按 turn_id/session_id 直接 grep）
LOG_FORMATTER = logging.Formatter(
    fmt="%(asctime)s [%(levelname)-8s] [req=%(request_id)s sess=%(session_id)s turn=%(turn_id)s run=%(run_id)s] %(name)-20s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

CONSOLE_FORMATTER = logging.Formatter(
    fmt="\033[36m%(asctime)s\033[0m [\033[1;%(levelname)sm%(levelname)s\033[0m] \033[33m%(name)-20s\033[0m [req=%(request_id)s sess=%(session_id)s turn=%(turn_id)s run=%(run_id)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# 单一共享文件 handler（所有 logger 共用，避免文件描述符膨胀）
_shared_file_handler: RotatingFileHandler | None = None


def _attach_correlation_filter(handler: logging.Handler) -> None:
    """Idempotently attach the runtime correlation filter to a handler."""
    if _RUNTIME_CORRELATION_FILTER not in handler.filters:
        handler.addFilter(_RUNTIME_CORRELATION_FILTER)


def _get_shared_file_handler(level: int = logging.INFO) -> RotatingFileHandler:
    global _shared_file_handler
    if _shared_file_handler is None:
        _shared_file_handler = RotatingFileHandler(
            filename=str(LOG_DIR / "app.log"),
            maxBytes=50 * 1024 * 1024,  # 50MB
            backupCount=14,
            encoding="utf-8",
        )
        _shared_file_handler.setFormatter(LOG_FORMATTER)
        _shared_file_handler.setLevel(level)
        _attach_correlation_filter(_shared_file_handler)
    return _shared_file_handler


def get_logger(name: str, level: str = "INFO"):
    """
    创建标准化的日志记录器

    Args:
        name: 日志记录器名称，通常使用 __name__
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    Returns:
        配置好的 Logger 实例
    """
    logger = logging.getLogger(name)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 将字符串级别转换为 logging 常量
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)

    # 控制台 Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(CONSOLE_FORMATTER)
    console_handler.setLevel(numeric_level)
    _attach_correlation_filter(console_handler)
    logger.addHandler(console_handler)

    # 文件 Handler - 所有 logger 共享同一个文件
    file_handler = _get_shared_file_handler(numeric_level)
    logger.addHandler(file_handler)
    # 也给 logger 本身加 filter，保证尚未走 handler 的 LogRecord 也有字段
    # （Formatter 在 handler 层调用，但某些第三方 handler 可能直接读 record 属性）
    if _RUNTIME_CORRELATION_FILTER not in logger.filters:
        logger.addFilter(_RUNTIME_CORRELATION_FILTER)

    return logger


# === 标准化的顶层日志器 ===

# 主应用日志
app_logger = get_logger("app", "INFO")

# 数据库日志（降低到 WARNING 减少噪音）
db_logger = get_logger("app.db", "WARNING")

# API 日志
api_logger = get_logger("app.api", "INFO")

# 任务队列日志
task_logger = get_logger("app.tasks", "INFO")

# Celery 日志
celery_logger = get_logger("celery", "INFO")


# === 快速配置接口 ===
def setup_logging_from_env():
    """
    根据环境变量配置日志级别

    可用环境变量:
    - LOG_LEVEL: DEBUG, INFO, WARNING, ERROR
    - ENABLE_FILE_LOGGING: true/false (默认为是)
    - MAX_LOG_SIZE_MB: 单个日志文件最大 MB 数
    - RETAIN_DAYS: 保留天数
    """
    import os

    env = os.environ.get("ENV", "development").lower()
    log_level = os.environ.get("LOG_LEVEL", "INFO" if env == "production" else "DEBUG")

    # 配置根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # 返回配置
    return {
        "env": env,
        "level": log_level,
        "debug_mode": env != "production"
    }
