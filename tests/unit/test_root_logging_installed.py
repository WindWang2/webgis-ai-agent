"""#1686：root logger 必须挂 stream handler —— 否则 app.* 的 INFO 记录被
``logging.lastResort``（WARNING 级 _StderrHandler）整体丢弃。

修复前全仓库没有任何地方配置 logging：``app/core/**`` / ``app/services/**``
里数十处 ``logging.getLogger(__name__)`` 的记录沿 app.* → root 向上传播，
而 root 无 handler；uvicorn 默认 LOGGING_CONFIG 只配 uvicorn /
uvicorn.error / uvicorn.access 三个 logger，不含 root。后果：生产排障只剩
uvorn access log 与 WARNING 以上异常，compose/k8s 里的 LOG_LEVEL=INFO 与
LOG_FORMAT=json 全是死配置，请求级关联字段从不出现。

本文件覆盖：
1. ``app/main.py::_install_root_logging`` —— root 无 handler 时挂
   StreamHandler + RuntimeCorrelationFilter；LOG_FORMAT=json 切
   JsonFormatter；LOG_LEVEL 生效（默认 INFO、可覆盖、非法值回落）；
   幂等（root 已有 handler 时不重复挂）。
2. 生产形态端到端（子进程）：先按 uvicorn 默认 LOGGING_CONFIG dictConfig
   （uvicorn 在 load_app 之前就这么做，且其配置不含 root），再 import
   app.main —— 此后 app.* 的 INFO 记录必须真的出现在 stderr。
3. ``app/core/logging_config`` import 零副作用（不建目录、不动 root），
   以及只读 FS 下的降级（无文件 handler，控制台照常）。
"""
import contextlib
import logging
import logging.handlers
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.main import _install_root_logging

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_MARKER = "PROBE_1686_INFO"


@contextlib.contextmanager
def _bare_root():
    """在测试体内把 root 临时还原到「无 handler」的生产前状态。

    pytest 的 logging 插件会在 call 阶段开始时往 root 挂 LogCaptureHandler
    （caplog 的载体）—— 这也正是生产进程里 root 无 handler、而测试进程里
    root 有 handler 因而 _install_root_logging 自动跳过的原因。要单测安装
    逻辑，必须在测试体内、调用前清除，退出时原样恢复（不影响 caplog）。
    """
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    root.handlers.clear()
    try:
        yield root
    finally:
        root.handlers.clear()
        root.handlers.extend(saved_handlers)
        root.setLevel(saved_level)


# ── 1. _install_root_logging 行为 ────────────────────────────────────────────


def test_installs_stream_handler_when_root_is_bare():
    with _bare_root() as root:
        _install_root_logging()
        assert root.handlers, "root 仍无 handler —— app.* 的 INFO 会被丢弃"
        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        # 关联字段过滤器必须挂（req/sess/turn/run/proj 进每条记录）
        from app.core.logging_config import _RUNTIME_CORRELATION_FILTER

        assert _RUNTIME_CORRELATION_FILTER in handler.filters


def test_log_level_env_is_honored(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    with _bare_root() as root:
        _install_root_logging()
        assert root.level == logging.DEBUG


def test_log_level_defaults_to_info(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    with _bare_root() as root:
        _install_root_logging()
        assert root.level == logging.INFO


def test_unparseable_log_level_falls_back_to_info(monkeypatch):
    """日志配置不应该有否决进程启动的权力（#1686）。"""
    monkeypatch.setenv("LOG_LEVEL", "NOT_A_LEVEL")
    with _bare_root() as root:
        _install_root_logging()
        assert root.level == logging.INFO


def test_json_format_switches_formatter(monkeypatch):
    from app.core.logging_config import JsonFormatter

    monkeypatch.setenv("LOG_FORMAT", "json")
    with _bare_root() as root:
        _install_root_logging()
        assert isinstance(root.handlers[0].formatter, JsonFormatter)


def test_text_format_is_the_default(monkeypatch):
    from app.core.logging_config import LOG_FORMATTER

    monkeypatch.delenv("LOG_FORMAT", raising=False)
    with _bare_root() as root:
        _install_root_logging()
        assert root.handlers[0].formatter is LOG_FORMATTER


def test_install_is_idempotent():
    with _bare_root() as root:
        _install_root_logging()
        first = list(root.handlers)
        _install_root_logging()
        assert list(root.handlers) == first, "重复调用不得叠加 handler"


def test_install_respects_existing_handlers():
    """root 已有 handler（pytest logging 插件 / 嵌入式宿主）时不重复挂接 ——
    测试进程的捕获语义因此不受本修复影响。"""
    with _bare_root() as root:
        root.addHandler(logging.NullHandler())
        _install_root_logging()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0], logging.NullHandler)


# ── 2. 生产形态端到端（uvicorn dictConfig + import app.main） ────────────────


def _run_probe(*, log_format: str | None = None) -> subprocess.CompletedProcess:
    script = textwrap.dedent(
        """
        import logging.config
        import sys

        from uvicorn.config import LOGGING_CONFIG

        # uvicorn 在 load_app（import app.main）之前就这么配置；其 LOGGING_CONFIG
        # 不含 root 且 disable_existing_loggers=False。
        logging.config.dictConfig(LOGGING_CONFIG)
        import app.main  # noqa: F401  — root bootstrap 在 import 期执行

        logging.getLogger("app.core.auth").info(%r)
        """
    ) % (PROBE_MARKER,)
    env = None
    if log_format is not None:
        import os

        env = {**os.environ, "LOG_FORMAT": log_format}
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=True,
        env=env,
    )


def test_app_info_records_reach_stderr_in_production_shape():
    """issue #1686 的验收：修复前该探针输出 0 行（INFO 被 lastResort 丢弃）。"""
    result = _run_probe()
    assert PROBE_MARKER in result.stderr, (
        "app.* 的 INFO 记录未到达 stderr —— root handler 仍未挂接\n"
        f"stdout: {result.stdout[-500:]}\nstderr: {result.stderr[-500:]}"
    )
    # 关联字段过滤器随 handler 生效（文本格式的 req=/sess= 段）
    assert "req=" in result.stderr and "sess=" in result.stderr


def test_json_log_format_emits_structured_lines():
    result = _run_probe(log_format="json")
    assert '"level": "INFO"' in result.stderr, (
        f"LOG_FORMAT=json 未产出结构化行:\n{result.stderr[-500:]}"
    )
    assert '"logger": "app.core.auth"' in result.stderr


# ── 3. logging_config import 纯度与只读降级 ─────────────────────────────────


def test_logging_config_import_has_no_filesystem_side_effects(tmp_path):
    """import app.core.logging_config 不得建目录 / 挂 root handler —— 只读
    rootfs（k8s readOnlyRootFilesystem）下 import 即炸的回归护栏。"""
    script = textwrap.dedent(
        """
        import logging
        import pathlib

        before = list(logging.getLogger().handlers)
        import app.core.logging_config as lc  # noqa: F401

        assert list(logging.getLogger().handlers) == before, "import 挂了 root handler"
        assert not list(pathlib.Path(".").iterdir()), "import 创建了文件/目录"
        print("PURE_OK", lc.LOG_DIR)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=True,
        env={**__import__("os").environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    assert "PURE_OK" in result.stdout, result.stderr[-500:]


def test_file_logging_degrades_when_log_dir_unwritable(monkeypatch, tmp_path):
    from app.core import logging_config as lc

    blocker = tmp_path / "regular_file"
    blocker.write_text("x", encoding="utf-8")
    # 指向「文件下的子路径」→ mkdir 抛 NotADirectoryError（OSError 子类）
    monkeypatch.setattr(lc, "LOG_DIR", blocker / "sub")
    monkeypatch.setattr(lc, "_shared_file_handler", None)
    monkeypatch.setattr(lc, "_file_handler_disabled", False)

    assert lc._ensure_log_dir() is False
    assert lc._get_shared_file_handler() is None
    # 降级不影响控制台输出：get_logger 仍返回可用 logger
    logger = lc.get_logger("probe.readonly_fs", "INFO")
    assert logger.handlers
    assert not any(
        isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers
    )


def test_module_level_loggers_are_lazy_but_available():
    """#1686：五个「标准化顶层日志器」改为惰性（import 零副作用），API 不变。"""
    from app.core import logging_config as lc

    assert "app_logger" not in vars(lc), "import 期不得实体化具名 logger"
    assert lc.app_logger.name == "app"
    assert lc.db_logger.name == "app.db"
    assert lc.celery_logger.name == "celery"
    with pytest.raises(AttributeError):
        lc.definitely_not_a_logger
