"""扩展权限模型（ADR-0104 / Wave 10）。

原则：
- manifest 声明 != 授权。声明是意图，授权是运维决定
  （``EXTENSION_PERMISSION_GRANTS``），默认最小权限（全无）。
- 权限失败必须 typed（:class:`ExtensionPermissionDenied`），不得静默降级。
- tool surface 继承扩展权限：扩展工具的调用被 SDK 包裹层在投影期绑定
  授权检查，核心 ToolRegistry 无需感知扩展存在。
- 子代理只能收窄：提供 :func:`narrow_grants` 求交集，不存在扩权路径。

V1 权限词表固定（新增须提升 EXTENSION API 主版本）。``filesystem_*`` 与
``external_process`` 在当前 trusted-code 边界下只能约束 SDK 提供的辅助
通道（如 provider HTTP），不能约束任意 Python 代码——文档必须如实声明。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError


class Permission:
    """权限常量集合（字符串枚举语义，词表封闭）。"""

    NETWORK = "network"
    FILESYSTEM_READ = "filesystem_read"
    FILESYSTEM_WRITE = "filesystem_write"
    PROJECT_ARTIFACT_READ = "project_artifact_read"
    PROJECT_ARTIFACT_WRITE = "project_artifact_write"
    EXTERNAL_PROCESS = "external_process"
    DATABASE = "database"
    MODEL_PROVIDER = "model_provider"
    DESTRUCTIVE_ACTION = "destructive_action"


ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        Permission.NETWORK,
        Permission.FILESYSTEM_READ,
        Permission.FILESYSTEM_WRITE,
        Permission.PROJECT_ARTIFACT_READ,
        Permission.PROJECT_ARTIFACT_WRITE,
        Permission.EXTERNAL_PROCESS,
        Permission.DATABASE,
        Permission.MODEL_PROVIDER,
        Permission.DESTRUCTIVE_ACTION,
    }
)

# local_untrusted 信任级别下默认拒绝、即使被授权也要求显式二次确认的
# 高危权限（host 激活期检查：声明了但信任级别不足 → typed diagnostic）。
HIGH_RISK_PERMISSIONS: frozenset[str] = frozenset(
    {
        Permission.FILESYSTEM_WRITE,
        Permission.EXTERNAL_PROCESS,
        Permission.DESTRUCTIVE_ACTION,
    }
)


class ExtensionPermissionDenied(ExtensionPlatformError):
    """权限未授予时的 typed 异常；tool 调用链可见、可测试。"""


@dataclass(frozen=True)
class PermissionGrantSet:
    """一个扩展的已授权集合（不可变；授权在 host 侧集中决策）。"""

    extension_id: str
    granted: frozenset[str] = frozenset()

    def allows(self, permission: str) -> bool:
        return permission in self.granted

    def require(self, permission: str) -> None:
        if permission not in self.granted:
            raise ExtensionPermissionDenied(
                ExtensionDiagnostic.error(
                    DiagnosticCode.PERMISSION_NOT_GRANTED,
                    f"extension {self.extension_id!r} lacks permission {permission!r}",
                    extension_id=self.extension_id,
                    permission=permission,
                )
            )

    def intersect(self, other: "PermissionGrantSet") -> "PermissionGrantSet":
        """子代理 / 嵌套上下文只能收窄权限。"""
        return PermissionGrantSet(
            extension_id=self.extension_id,
            granted=self.granted & other.granted,
        )


def parse_grants_config(raw: str) -> dict[str, frozenset[str]]:
    """解析运维授权配置。

    格式（宽松但确定性）：“id:perm1,perm2;id2:perm3”，空白忽略；未知
    权限词在解析期即报 ``PERMISSION_DECLARATION_INVALID``（fail closed）。
    """
    grants: dict[str, frozenset[str]] = {}
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        ext_id, _, perm_csv = chunk.partition(":")
        ext_id = ext_id.strip()
        if not ext_id:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.PERMISSION_DECLARATION_INVALID,
                    f"permission grant entry {chunk!r} lacks extension id",
                )
            )
        perms = frozenset(
            p.strip() for p in perm_csv.split(",") if p.strip()
        )
        unknown = perms - ALL_PERMISSIONS
        if unknown:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.PERMISSION_DECLARATION_INVALID,
                    f"unknown permissions for {ext_id!r}: {sorted(unknown)}",
                    extension_id=ext_id,
                )
            )
        existing = grants.get(ext_id, frozenset())
        grants[ext_id] = existing | perms
    return grants


def validate_declared_permissions(
    extension_id: str,
    declared: Iterable[str],
) -> list[ExtensionDiagnostic]:
    diagnostics: list[ExtensionDiagnostic] = []
    seen: set[str] = set()
    for perm in declared:
        if perm not in ALL_PERMISSIONS:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.PERMISSION_DECLARATION_INVALID,
                    f"unknown permission {perm!r}",
                    extension_id=extension_id,
                    permission=perm,
                )
            )
        if perm in seen:
            diagnostics.append(
                ExtensionDiagnostic.warning(
                    DiagnosticCode.PERMISSION_DECLARATION_INVALID,
                    f"duplicate permission declaration {perm!r}",
                    extension_id=extension_id,
                    permission=perm,
                )
            )
        seen.add(perm)
    return diagnostics


def grants_for(
    extension_id: str, grants: Mapping[str, frozenset[str]]
) -> PermissionGrantSet:
    return PermissionGrantSet(
        extension_id=extension_id, granted=frozenset(grants.get(extension_id, frozenset()))
    )
