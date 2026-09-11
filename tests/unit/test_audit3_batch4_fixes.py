"""full-master-audit-2026-09 Batch 4 回归测试。

- #1205（A-1）：TOOL_TIMEOUT 派发码分类为 transient_network（plan-mode
  livelock guard 不再把偶发超时判为确定性失败）。
- #1218（A-4）：execute_plan 注册 ASYNC + 显式 timeout；slow_inline 审计。
"""

from __future__ import annotations


class TestToolTimeoutClassification:
    def test_tool_timeout_code_is_transient(self) -> None:
        from app.services.planning.recovery import FailureClass, classify_error

        fc = classify_error(
            code="TOOL_TIMEOUT",
            error_type="TimeoutError",
            message="工具 kernel_density 执行超时（>300s），请缩小数据范围后重试",
            status="error",
        )
        assert fc is FailureClass.transient_network

    def test_exception_path_still_transient(self) -> None:
        from app.services.planning.recovery import FailureClass, classify_error

        fc = classify_error(exception=TimeoutError())
        assert fc is FailureClass.transient_network

    def test_validation_code_unchanged(self) -> None:
        from app.services.planning.recovery import FailureClass, classify_error

        fc = classify_error(code="VALIDATION_ERROR")
        assert fc is FailureClass.validation


class TestExecutePlanPolicy:
    def test_execute_plan_async_with_explicit_timeout(self) -> None:
        from app.tools.plan_mode import register_plan_mode_tools
        from app.tools.registry import ToolExecutionPolicy, ToolRegistry

        registry = ToolRegistry()
        register_plan_mode_tools(registry)
        meta = registry.metadata("execute_plan")
        assert meta.get("execution_policy") is ToolExecutionPolicy.ASYNC
        assert int(meta.get("timeout", 0)) >= 900

    def test_no_slow_inline_in_live_registry(self) -> None:
        """policy_audit 新增 slow_inline 后：活注册表 warning 钉零。"""
        from app.tools import init_tools
        from app.tools.policy_audit import audit_registry_policies
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        init_tools(reg)
        slow_inline = [
            f.as_dict() for f in audit_registry_policies(reg)
            if f.code == "slow_inline"
        ]
        assert slow_inline == [], f"slow INLINE registrations: {slow_inline}"
