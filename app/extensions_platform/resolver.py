"""依赖解析器（ADR-0105 V2 / Wave 8）。

职责（全部确定性、无 I/O）：
- **约束校验**：manifest 依赖声明的版本约束（``version_constraints`` 语法）
  对照已发现实例的实际版本；不满足 → typed 错误（fail closed，进入
  INCOMPATIBLE，不参与激活）；
- **确定性激活序**：Kahn 拓扑 + id 字典序 tie-break（与 host.activate_all
  的既有规则一致，收敛到一处）；
- **升级预检**：给定新版本，检查全部依赖者的约束是否仍然满足；存在
  冲突 → typed DEPENDENCY_CONFLICT（升级被拒，旧版继续运行）。

诚实边界：同一 extension id 在一次发现中只有一个实例（discovery 按 id
去重，后者隔离），因此「多版本共存 + SAT 求解」不在此层——跨根的多副本
由 discovery 的 ID_COLLISION 隔离，升级冲突由 check_upgrade_conflicts 在
升级入口把关。回滚 = 运维恢复旧 pack 目录 + ``reload(allow_downgrade=True)``
（宿主不保存版本副本，不虚假承诺自动回滚）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .diagnostics import DiagnosticCode, ExtensionDiagnostic
from .version_constraints import satisfied, validate_constraint_syntax


@dataclass(frozen=True)
class DepView:
    """解析视角下的最小依赖声明。"""

    id: str
    required: bool
    version_constraint: Optional[str]
    feature_flag: Optional[str] = None


@dataclass(frozen=True)
class RecordView:
    """解析视角下的最小扩展记录（manifest 投影，解耦 host 类型）。"""

    id: str
    version: str
    state: str  # ExtensionState.value（仅用于过滤；解析本身不依赖）
    deps: tuple[DepView, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ResolutionResult:
    ordered: tuple[str, ...] = ()
    diagnostics: tuple[ExtensionDiagnostic, ...] = ()


def constraint_diagnostics_for(
    record: RecordView,
    records: dict[str, RecordView],
) -> list[ExtensionDiagnostic]:
    """逐依赖校验版本约束。required 且不满足 → error；optional → warning。"""
    diagnostics: list[ExtensionDiagnostic] = []
    for dep in record.deps:
        if dep.version_constraint is None:
            continue
        err = validate_constraint_syntax(dep.version_constraint)
        if err is not None:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.DEPENDENCY_CONSTRAINT_INVALID,
                    f"dependency {dep.id!r} constraint {dep.version_constraint!r}: {err}",
                    extension_id=record.id,
                )
            )
            continue
        target = records.get(dep.id)
        if target is None:
            continue  # 缺失依赖由 host 的既有 DEPENDENCY_MISSING 路径报告
        if not satisfied(target.version, dep.version_constraint):
            message = (
                f"dependency {dep.id!r} version {target.version} does not satisfy "
                f"constraint {dep.version_constraint!r}"
            )
            if dep.required:
                diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.DEPENDENCY_MISSING,
                        message,
                        extension_id=record.id,
                    )
                )
            else:
                diagnostics.append(
                    ExtensionDiagnostic.warning(
                        DiagnosticCode.OPTIONAL_DEPENDENCY_ABSENT,
                        message + " (optional dependency will degrade)",
                        extension_id=record.id,
                    )
                )
    return diagnostics


def resolve_activation_plan(
    records: dict[str, RecordView],
    eligible: set[str],
) -> ResolutionResult:
    """eligible（compatible）集合内的确定性拓扑序。

    约束校验由调用方（host.validate_extension）完成；这里只做排序。
    环成员不进入 ordered（由 host 依赖环诊断处理）。
    """
    indegree = {eid: 0 for eid in eligible}
    dependents: dict[str, list[str]] = {eid: [] for eid in eligible}
    for eid in eligible:
        record = records[eid]
        for dep in record.deps:
            if not dep.required:
                continue
            if dep.id in indegree:
                indegree[eid] += 1
                dependents[dep.id].append(eid)
    ready = sorted(eid for eid, degree in indegree.items() if degree == 0)
    ordered: list[str] = []
    while ready:
        eid = ready.pop(0)
        ordered.append(eid)
        for dependent in sorted(dependents[eid]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
        # 字典序 tie-break（Round-2 NOTE：与 V1 activate_all 的排序语义一致）。
        ready.sort()
    return ResolutionResult(ordered=tuple(ordered))


def check_upgrade_conflicts(
    extension_id: str,
    new_version: str,
    records: dict[str, RecordView],
) -> list[ExtensionDiagnostic]:
    """升级预检：全部把 extension_id 声明为带约束依赖的扩展（无论其当前
    状态——升级影响所有未来激活）是否仍满足约束。"""
    diagnostics: list[ExtensionDiagnostic] = []
    for other_id in sorted(records):
        other = records[other_id]
        for dep in other.deps:
            if dep.id != extension_id or dep.version_constraint is None:
                continue
            if not satisfied(new_version, dep.version_constraint):
                diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.DEPENDENCY_CONFLICT,
                        f"upgrading {extension_id!r} to {new_version} violates "
                        f"{other_id!r} constraint {dep.version_constraint!r}",
                        extension_id=extension_id,
                    )
                )
    return diagnostics
