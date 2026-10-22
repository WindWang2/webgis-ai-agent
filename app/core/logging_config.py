# ============================================================
# WebGIS AI Agent 应用日志配置（Python）
# ============================================================

import json
import os
import sys
import logging
import logging.handlers
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# 日志目录（#1686）：惰性创建 + 容错，替代 import 期的
# ``Path("./logs").mkdir(exist_ok=True)`` —— 后者有两个必修缺陷：
#   (a) 相对 CWD：不同启动目录落到不同位置，日志位置不可预期；
#   (b) k8s ``readOnlyRootFilesystem: true``（deploy/k8s/02-api-deployment.yaml）
#       且未挂 ./logs 时 import 期直接 OSError [Errno 30]，app 起不来。
# 默认路径改为环境无关的可写位置（k8s 把 /tmp 挂为 emptyDir；可用 LOG_DIR
# 覆盖）。真正创建推迟到首个文件 handler 需要时（_ensure_log_dir），失败即
# 降级为「无文件 handler、控制台照常」—— 日志配置永远不能成为进程起不来
# 的理由。
# nosec B108（CWE-377）：/tmp 默认值是上述 (a)/(b) 缺陷的刻意修正，非疏漏；
# 多租户敏感度由 LOG_DIR 环境变量覆盖承接（k8s emptyDir 即每 Pod 私有）。
LOG_DIR = Path(os.environ.get("LOG_DIR", "/tmp/webgis-logs"))  # nosec B108


def _ensure_log_dir() -> bool:
    """Best-effort 创建 LOG_DIR；只读 FS / 权限不足时返回 False 供降级。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False

class RuntimeCorrelationFilter(logging.Filter):
    """从 RuntimeContext (ContextVar) 取关联字段注入每条 LogRecord。

    未绑定时字段置为占位符 "-"（便于 grep，且不清日志行），不抛。
    """

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        try:
            from app.core.runtime_context import current_runtime_context
            ctx = current_runtime_context()
        except Exception:  # noqa: BLE001
            ctx = None
        record.request_id = getattr(ctx, "request_id", None) or "-" if ctx else "-"  # type: ignore[attr-defined]
        record.session_id = getattr(ctx, "session_id", None) or "-" if ctx else "-"  # type: ignore[attr-defined]
        record.turn_id = getattr(ctx, "turn_id", None) or "-" if ctx else "-"  # type: ignore[attr-defined]
        record.run_id = getattr(ctx, "run_id", None) or "-" if ctx else "-"  # type: ignore[attr-defined]
        # ADR-0245 Wave 6：活动项目进关联主干（session 域与 project 域的桥接
        # 已由 RuntimeContext.project_id 承载；日志面补齐同一字段）。
        record.project_id = getattr(ctx, "project_id", None) or "-" if ctx else "-"  # type: ignore[attr-defined]
        # Platform V4（ADR-0131 D2）：trace 维度进 LogRecord（文本格式串不变，
        # JSON 格式化器与自定义 handler 消费；span 作用域内自动是当前 span）。
        record.trace_id = getattr(ctx, "trace_id", None) or "-" if ctx else "-"  # type: ignore[attr-defined]
        record.span_id = getattr(ctx, "span_id", None) or "-" if ctx else "-"  # type: ignore[attr-defined]
        return True


_RUNTIME_CORRELATION_FILTER = RuntimeCorrelationFilter()

# 定义日志格式（#691：带关联字段；未绑定时为 "-"，可按 turn_id/session_id 直接 grep）
LOG_FORMATTER = logging.Formatter(
    fmt="%(asctime)s [%(levelname)-8s] [req=%(request_id)s sess=%(session_id)s turn=%(turn_id)s run=%(run_id)s proj=%(project_id)s] %(name)-20s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

CONSOLE_FORMATTER = logging.Formatter(
    fmt="\033[36m%(asctime)s\033[0m [\033[1;%(levelname)sm%(levelname)s\033[0m] \033[33m%(name)-20s\033[0m [req=%(request_id)s sess=%(session_id)s turn=%(turn_id)s run=%(run_id)s proj=%(project_id)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


class JsonFormatter(logging.Formatter):
    """JSON 结构化日志（Platform V4，ADR-0131 D2；LOG_FORMAT=json 启用）。

    面向 log aggregation：关联字段（req/sess/turn/run/proj/trace/span）恒在
    （未绑定为 null——JSON 里显式 null 比 "-" 更可查询），异常带 exc_info。
    """

    _CORRELATION_FIELDS = (
        "request_id", "session_id", "turn_id", "run_id", "project_id",
        "trace_id", "span_id",
    )
    #: 标量 extra 并入时的键名拒绝词表（review R1-m5：防止调用方顺手
    #: extra={"authorization": ...} 之类被序列化进聚合日志）
    _DENIED_EXTRA_HINTS = ("key", "token", "secret", "password", "authorization",
                           "credential", "passwd", "cookie", "private")

    _STD_LOGRECORD_KEYS = frozenset({
        "levelname", "levelno", "name", "msg", "args", "exc_info", "exc_text",
        "stack_info", "lineno", "funcName", "created", "msecs",
        "relativeCreated", "thread", "threadName", "processName", "process",
        "taskName", "pathname", "filename", "module", "message",
        "request_id", "session_id", "turn_id", "run_id", "project_id",
        "trace_id", "span_id", "asctime",
    })

    def format(self, record: logging.LogRecord) -> str:
        # 秒与毫秒同源（record.created 的整数秒 + msecs），跨秒边一致（n5）
        payload: dict[str, object] = {
            "ts": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
            ) + ".%03dZ" % (record.msecs,),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field_name in self._CORRELATION_FIELDS:
            value = getattr(record, field_name, None)
            payload[field_name] = value if value not in (None, "-") else None
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in payload or key in self._STD_LOGRECORD_KEYS or key.startswith("_"):
                continue
            if any(h in key.lower() for h in self._DENIED_EXTRA_HINTS):
                payload[key] = "[REDACTED]"
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                payload[key] = value
        try:
            return json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):  # pragma: no cover — 防御非法值
            return json.dumps(
                {"message": repr(record.getMessage()), "level": record.levelname}
            )

# 单一共享文件 handler（所有 logger 共用，避免文件描述符膨胀）
_shared_file_handler: RotatingFileHandler | None = None
#: 文件日志降级闩（#1686）：目录不可写/建 handler 失败后不再重试 ——
#: 每请求重试只会在只读 FS 上反复抛 OSError。
_file_handler_disabled: bool = False


def _attach_correlation_filter(handler: logging.Handler) -> None:
    """Idempotently attach the runtime correlation filter to a handler."""
    if _RUNTIME_CORRELATION_FILTER not in handler.filters:
        handler.addFilter(_RUNTIME_CORRELATION_FILTER)


def _json_logging_enabled() -> bool:
    """LOG_FORMAT=json → 结构化输出（Platform V4；默认保持人类可读文本）。"""
    return os.environ.get("LOG_FORMAT", "").strip().lower() == "json"


def _get_shared_file_handler(level: int = logging.INFO) -> RotatingFileHandler | None:
    """共享 RotatingFileHandler；目录不可写时降级返回 None（#1686）。

    调用方（get_logger）必须容忍 None：只读 rootfs / 权限不足时控制台
    handler 照常工作，文件日志静默关闭（首次失败后 _file_handler_disabled
    闩住，不再每调用重试）。
    """
    global _shared_file_handler, _file_handler_disabled
    if _shared_file_handler is not None:
        return _shared_file_handler
    if _file_handler_disabled:
        return None
    if not _ensure_log_dir():
        _file_handler_disabled = True
        return None
    try:
        handler = RotatingFileHandler(
            filename=str(LOG_DIR / "app.log"),
            maxBytes=50 * 1024 * 1024,  # 50MB
            backupCount=14,
            encoding="utf-8",
        )
    except OSError:
        _file_handler_disabled = True
        return None
    handler.setFormatter(
        JsonFormatter() if _json_logging_enabled() else LOG_FORMATTER
    )
    handler.setLevel(level)
    _attach_correlation_filter(handler)
    _shared_file_handler = handler
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
    console_handler.setFormatter(
        JsonFormatter() if _json_logging_enabled() else CONSOLE_FORMATTER
    )
    console_handler.setLevel(numeric_level)
    _attach_correlation_filter(console_handler)
    logger.addHandler(console_handler)

    # 文件 Handler - 所有 logger 共享同一个文件（#1686：目录不可写时降级跳过）
    file_handler = _get_shared_file_handler(numeric_level)
    if file_handler is not None:
        logger.addHandler(file_handler)
    # 也给 logger 本身加 filter，保证尚未走 handler 的 LogRecord 也有字段
    # （Formatter 在 handler 层调用，但某些第三方 handler 可能直接读 record 属性）
    if _RUNTIME_CORRELATION_FILTER not in logger.filters:
        logger.addFilter(_RUNTIME_CORRELATION_FILTER)

    return logger


# === 标准化的顶层日志器（#1686：惰性创建） ===
#
# 此前 import 期即调用 get_logger() 建这五个具名 logger，两个必修问题：
#   (a) 触发 LOG_DIR 创建 + 文件 handler —— 只读 rootfs 下 import 即炸
#       （k8s CrashLoopBackOff），而生产进程根本不 import 本模块；
#   (b) 这些具名 logger 自带 handler，与 app/main.py 的 root bootstrap
#       （#1686）叠加会对 app.* 记录**双重输出**（具名 handler 一次 +
#       root handler 一次）。
# 改为模块级 __getattr__ 惰性创建：import 本模块零副作用，首次访问属性时
# 才建（对外 API 不变，仓内无消费方）。root 挂接后 app.* 记录统一经
# root handler 输出，不再需要这些具名 handler 兜底。
_LAZY_LOGGERS: dict[str, tuple[str, str]] = {
    "app_logger": ("app", "INFO"),
    "db_logger": ("app.db", "WARNING"),
    "api_logger": ("app.api", "INFO"),
    "task_logger": ("app.tasks", "INFO"),
    "celery_logger": ("celery", "INFO"),
}


def __getattr__(name: str) -> Any:
    spec = _LAZY_LOGGERS.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return get_logger(spec[0], spec[1])


# === 快速配置接口 ===
def setup_logging_from_env():
    """
    根据环境变量配置日志级别

    可用环境变量:
    - LOG_LEVEL: DEBUG, INFO, WARNING, ERROR
    - ENABLE_FILE_LOGGING: true/false (默认为是)
    - MAX_LOG_SIZE_MB: 单个日志文件最大 MB 数
    - RETAIN_DAYS: 保留天数

    #1686：生产启动路径（uvicorn app.main:app）的 root handler + LOG_LEVEL
    由 app/main.py 的 _install_root_logging 负责（本函数在生产零调用，
    保留给显式调用与测试）；两处的 LOG_LEVEL 语义一致（env 优先）。
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
