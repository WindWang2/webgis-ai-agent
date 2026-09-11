"""执行策略审计（ADR-0101 Wave 6, §25）。

ToolExecutionPolicy（INLINE/ASYNC/THREAD/CELERY，ADR-0043）的正确性此前
只有两条注册期规则：async+THREAD/CELERY 自动路由 ASYNC（#374）、cost 枚举
校验。仍存的漂移：

- **sync 函数声明 ASYNC**：_execute_tool 会兜底走线程，但元数据持续说谎
  （metrics 的 requested=ASYNC / actual=THREAD）， observability 失真；
- **heavy cost + INLINE**：INLINE 契约是 <5ms 超轻量（loop 上直接执行，
  挂死无法抢占，见 registry 注释）；heavy 先验与之矛盾；
- **INLINE 缺 timeout 元数据**：INLINE 无法被 asyncio.timeout 抢占 —— 这是
  既有约束，审计只提示「INLINE 工具必须短到可信」。

本模块提供：
- ``audit_registration(...)``：注册期单工具检查（register() 调用；warning
  级 —— 不阻断启动，避免重蹈「模块加载整体失败」的静默降级）；
- ``audit_registry_policies(registry)``：全库扫描（测试/CI 门 —— live
  registry 上 **error 级**发现必须为零，由契约测试钉住）。
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from app.tools.registry import ToolExecutionPolicy, ToolRegistry

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class PolicyFinding:
    tool: str
    severity: Severity
    code: str
    detail: str

    def as_dict(self) -> dict:
        return {"tool": self.tool, "severity": self.severity.value,
                "code": self.code, "detail": self.detail}


def audit_registration(
    name: str,
    func,
    policy: ToolExecutionPolicy,
    cost: str,
    timeout: Optional[float],
) -> List[PolicyFinding]:
    """注册期检查（warning 级留痕，不阻断）。"""
    findings: List[PolicyFinding] = []
    is_async = inspect.iscoroutinefunction(func)

    if policy is ToolExecutionPolicy.ASYNC and not is_async:
        findings.append(PolicyFinding(
            tool=name, severity=Severity.WARNING, code="sync_declared_async",
            detail="同步函数声明了 ASYNC 策略：运行时兜底走线程，但策略元数据失真"
                   "（metrics requested=ASYNC/actual=THREAD）。请改为 THREAD 或实现为 async def。",
        ))
    if policy is ToolExecutionPolicy.INLINE and cost == "heavy":
        findings.append(PolicyFinding(
            tool=name, severity=Severity.ERROR, code="heavy_inline",
            detail="heavy 工具声明 INLINE：INLINE 直接在事件循环执行且无法被"
                   " asyncio.timeout 抢占 —— 挂死会阻塞整个进程。改 THREAD/CELERY 或降 cost。",
        ))
    if policy is ToolExecutionPolicy.INLINE and timeout:
        findings.append(PolicyFinding(
            tool=name, severity=Severity.INFO, code="inline_timeout_ineffective",
            detail="INLINE 工具声明了 timeout 元数据：INLINE 无法被预算抢占，"
                   "该声明是无效自欺（保留仅为兼容）。",
        ))
    if cost == "heavy" and timeout is None:
        findings.append(PolicyFinding(
            tool=name, severity=Severity.INFO, code="heavy_no_explicit_timeout",
            detail="heavy 工具未声明 timeout：将使用全局默认（TOOL_TIMEOUT_S）。"
                   "重工具建议显式声明预算。",
        ))
    return findings


def audit_registry_policies(registry: ToolRegistry) -> List[PolicyFinding]:
    """全库策略扫描。测试/CI 门使用：error 级发现必须为零。"""
    findings: List[PolicyFinding] = []
    for name in registry.list_tools():
        meta = registry.metadata(name)
        func = registry._tools.get(name)
        if func is None:
            continue
        policy = meta.get("execution_policy")
        if not isinstance(policy, ToolExecutionPolicy):
            continue
        findings.extend(audit_registration(
            name, func, policy,
            cost=str(meta.get("cost", "light")),
            timeout=meta.get("timeout"),
        ))
        # #1218（audit3 A-4）：INLINE 契约 <5ms 与自声明 latency_class=slow
        # 矛盾 —— 此前交叉校验只覆盖 cost×INLINE 一对（execute_plan 以
        # cost=light 漏过并被默认 300s 预算截断）。
        if (
            policy is ToolExecutionPolicy.INLINE
            and str(meta.get("latency_class", "")).lower() == "slow"
        ):
            findings.append(PolicyFinding(
                tool=name, severity=Severity.WARNING, code="slow_inline",
                detail="INLINE 工具自声明 latency_class=slow：违反 INLINE <5ms "
                       "契约，且无显式 timeout 时整计划落入默认工具预算。"
                       "改 ASYNC/THREAD 并声明显式预算。",
            ))
    return findings


def error_findings(findings: List[PolicyFinding]) -> List[PolicyFinding]:
    return [f for f in findings if f.severity is Severity.ERROR]
