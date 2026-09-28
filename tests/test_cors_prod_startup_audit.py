"""audit #1557：生产 + CORS 通配来源 + 凭证放行 的启动期大声审计守卫。

背景：app/main.py 的 CORSMiddleware 威胁模型注释接受 CORS_ORIGINS=["*"] +
allow_credentials=True，前提是"可信网关后方 + bearer-header 认证"——但该
前提只是口头契约。config 层 ``_validate_cors_origins`` 已在 Settings 构造期
fail-fast（production + ["*"]），不过 pydantic 默认 validate_assignment=
False，ENV / CORS_ORIGINS 构造后被运行时改写不会重跑 validator。本文件的
守卫在 lifespan 启动时读活 settings 兜底发 WARNING 审计日志（不 raise）。

分层覆盖：
- 纯函数 ``audit_cors_wildcard_with_credentials`` 判定矩阵（caplog）；
- lifespan 启动事件真实接线（沿用 tests/test_runtime_chaos_lifecycle.py
  的最小 monkeypatch 驱动模式：init_db / drain_background_tasks 打桩）；
- 守卫触发条件与 CORSMiddleware 的 allow_credentials 同源（AST 检查，
  沿用 tests/test_swagger_prod_disable.py 的源码断言风格）。
"""
import ast
import asyncio
import inspect
import logging

import pytest

from app.main import (
    CORS_AUDIT_LOG_CODE,
    audit_cors_wildcard_with_credentials,
)


def _records(caplog):
    return [r for r in caplog.records if CORS_AUDIT_LOG_CODE in r.getMessage()]


class TestGuardPredicateMatrix:
    """纯函数判定矩阵：不依赖 lifespan，覆盖全部布尔组合。"""

    def test_prod_wildcard_credentials_warns(self, caplog):
        caplog.set_level(logging.WARNING, logger="app.main")
        hit = audit_cors_wildcard_with_credentials(
            ["*"], is_production=True, allow_credentials=True,
        )
        assert hit is True
        records = _records(caplog)
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING

    def test_warning_includes_effective_origins(self, caplog):
        """effective CORS_ORIGINS 非机密，必须原样进审计日志。"""
        caplog.set_level(logging.WARNING, logger="app.main")
        origins = ["http://gateway.internal", "*"]
        audit_cors_wildcard_with_credentials(
            origins, is_production=True, allow_credentials=True,
        )
        (record,) = _records(caplog)
        assert str(origins) in record.getMessage()
        # 审计信息里必须给运维指路（显式 allow-list）。
        assert "allow-list" in record.getMessage()

    def test_whitespace_padded_star_still_caught(self, caplog):
        caplog.set_level(logging.WARNING, logger="app.main")
        assert audit_cors_wildcard_with_credentials(
            [" * "], is_production=True, allow_credentials=True,
        ) is True
        assert len(_records(caplog)) == 1

    def test_non_prod_wildcard_is_silent(self, caplog):
        caplog.set_level(logging.WARNING, logger="app.main")
        assert audit_cors_wildcard_with_credentials(
            ["*"], is_production=False, allow_credentials=True,
        ) is False
        assert _records(caplog) == []

    def test_prod_explicit_origins_are_silent(self, caplog):
        caplog.set_level(logging.WARNING, logger="app.main")
        assert audit_cors_wildcard_with_credentials(
            ["https://gis.example.com"],
            is_production=True, allow_credentials=True,
        ) is False
        assert _records(caplog) == []

    def test_prod_wildcard_without_credentials_is_silent(self, caplog):
        caplog.set_level(logging.WARNING, logger="app.main")
        assert audit_cors_wildcard_with_credentials(
            ["*"], is_production=True, allow_credentials=False,
        ) is False
        assert _records(caplog) == []

    def test_none_origins_is_silent(self, caplog):
        """运行期误把 CORS_ORIGINS 置 None 不应炸掉 lifespan。"""
        caplog.set_level(logging.WARNING, logger="app.main")
        assert audit_cors_wildcard_with_credentials(
            None, is_production=True, allow_credentials=True,
        ) is False
        assert _records(caplog) == []


class TestLifespanWiring:
    """启动事件真实接线：守卫必须在 lifespan 内被调用并读活 settings。"""

    def test_lifespan_calls_cors_audit_guard(self):
        from app import main as main_module

        source = inspect.getsource(main_module.lifespan)
        assert "audit_cors_wildcard_with_credentials(" in source
        # 必须读活的 settings 对象（构造期 validator 照不到运行期改写）。
        assert "settings.is_production()" in source
        assert "settings.CORS_ORIGINS" in source

    def test_middleware_credentials_use_shared_constant(self):
        """CORSMiddleware 的 allow_credentials 必须引用共享常量——中间件
        语义翻转时守卫触发条件同步翻转，不会漂移。"""
        from app import main as main_module

        tree = ast.parse(inspect.getsource(main_module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # 形态：app.add_middleware(CORSMiddleware, allow_credentials=...)
            # —— CORSMiddleware 是 add_middleware 的位置参数，不是 func 本身。
            is_add_middleware = (
                isinstance(func, ast.Attribute) and func.attr == "add_middleware"
            )
            if not is_add_middleware:
                continue
            cors_positional = any(
                isinstance(arg, ast.Name) and arg.id == "CORSMiddleware"
                for arg in node.args
            )
            if not cors_positional:
                continue
            for kw in node.keywords:
                if kw.arg == "allow_credentials":
                    assert isinstance(kw.value, ast.Name), (
                        "allow_credentials must reference "
                        "_CORS_ALLOW_CREDENTIALS (shared with the "
                        "cors-audit guard), not a literal"
                    )
                    assert kw.value.id == "_CORS_ALLOW_CREDENTIALS"
                    return
        pytest.fail("app.add_middleware(CORSMiddleware, ...) not found in app/main.py")


class TestStartupEventAudit:
    """真实 lifespan 启动事件：monkeypatch settings 后驱动 lifespan。

    模式取自 tests/test_runtime_chaos_lifecycle.py::_drive_lifespan_cycles
    （init_db / drain_background_tasks 打桩；conftest 已钉
    USE_NEW_AGENT=false，不拉起 Node 子进程）。
    """

    @staticmethod
    def _drive_lifespan(monkeypatch, env_value, cors_origins) -> None:
        import app.core.database as db_module
        import app.main as main_module

        monkeypatch.setattr(db_module, "init_db", lambda: None)

        async def fake_drain(timeout: float = 5.0):
            return None

        monkeypatch.setattr(main_module, "drain_background_tasks", fake_drain)
        # settings 是模块级单例（pydantic v2 默认可变、赋值不重跑
        # validator）——这正是本守卫要兜底的运行期改写通道。
        monkeypatch.setattr(main_module.settings, "ENV", env_value)
        monkeypatch.setattr(main_module.settings, "CORS_ORIGINS", cors_origins)
        # 本文件只测 CORS 审计，不测 GIS runtime manifest；manifest 严格
        # 校验挂掉（如同仓其它文件并行改动期的 capability 悬空）会让
        # lifespan 走不到 yield。用 validator 自带的降级开关（见
        # app/lib/gis/runtime_manifest.py 报错文案）隔离无关失败面。
        monkeypatch.setenv("GIS_MANIFEST_STRICT", "0")

        async def _drive():
            async with main_module.lifespan(main_module.app):
                pass

        asyncio.run(_drive())

    def test_prod_wildcard_warns_on_startup(self, monkeypatch, caplog):
        caplog.set_level(logging.WARNING, logger="app.main")
        self._drive_lifespan(monkeypatch, "production", ["*"])
        records = _records(caplog)
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert str(["*"]) in records[0].getMessage()

    def test_dev_wildcard_no_startup_warning(self, monkeypatch, caplog):
        caplog.set_level(logging.WARNING, logger="app.main")
        self._drive_lifespan(monkeypatch, "development", ["*"])
        assert _records(caplog) == []

    def test_prod_explicit_origins_no_startup_warning(self, monkeypatch, caplog):
        caplog.set_level(logging.WARNING, logger="app.main")
        self._drive_lifespan(
            monkeypatch, "production", ["https://gis.example.com"],
        )
        assert _records(caplog) == []
