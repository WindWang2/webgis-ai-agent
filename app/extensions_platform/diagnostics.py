"""扩展平台类型化诊断（ADR-0104）。

所有扩展生命周期失败都必须产出 typed diagnostic，而不是裸异常字符串：
- 稳定的 ``code`` 供 CLI / 测试 / 上层 UI 判定；
- ``severity`` 决定 fail-closed 边界（error 必阻断激活，warning 允许 degraded）；
- ``extension_id`` / ``detail`` 留痕定位。

诊断只描述事实，不做恢复决策；恢复策略（回滚 / 隔离 / 降级）由 host 依据
severity 执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class DiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DiagnosticCode(str, Enum):
    """稳定诊断码。新增只能追加，不得改写既有值（外部测试/脚本依赖）。"""

    # manifest 结构 / 校验
    MANIFEST_SCHEMA_UNSUPPORTED = "manifest_schema_unsupported"
    MANIFEST_INVALID = "manifest_invalid"
    MANIFEST_PARSE_FAILED = "manifest_parse_failed"
    NAMESPACE_INVALID = "namespace_invalid"
    NAMESPACE_RESERVED = "namespace_reserved"
    ID_COLLISION = "id_collision"
    # 版本兼容
    CORE_VERSION_INCOMPATIBLE = "core_version_incompatible"
    API_VERSION_INCOMPATIBLE = "api_version_incompatible"
    EXTENSION_TYPE_UNSUPPORTED = "extension_type_unsupported"
    # 依赖
    DEPENDENCY_MISSING = "dependency_missing"
    DEPENDENCY_CYCLE = "dependency_cycle"
    DEPENDENT_ACTIVE = "dependent_active"
    OPTIONAL_DEPENDENCY_ABSENT = "optional_dependency_absent"
    # 信任 / 权限
    TRUST_BLOCKED = "trust_blocked"
    PERMISSION_NOT_GRANTED = "permission_not_granted"
    PERMISSION_DECLARATION_INVALID = "permission_declaration_invalid"
    # 生命周期 / 投影
    ENTRY_POINT_MISSING = "entry_point_missing"
    ENTRY_POINT_FAILED = "entry_point_failed"
    REGISTRY_PROJECTION_FAILED = "registry_projection_failed"
    REGISTRY_PROJECTION_COLLISION = "registry_projection_collision"
    REGISTRY_ROLLBACK_INCOMPLETE = "registry_rollback_incomplete"
    UNDECLARED_REGISTRATION = "undeclared_registration"
    DECLARED_BUT_UNREGISTERED = "declared_but_unregistered"
    # 运行态
    HEALTH_CHECK_FAILED = "health_check_failed"
    HEALTH_UNHEALTHY = "health_unhealthy"
    DISCOVERY_LIMIT_EXCEEDED = "discovery_limit_exceeded"
    FINGERPRINT_CHANGED = "fingerprint_changed"
    FEATURE_FLAG_UNRESOLVED = "feature_flag_unresolved"
    EXTENSION_DISABLED = "extension_disabled"
    # ── V2（ADR-0105）：隔离 worker / 供应链 / 解析器 ──────────────────
    WORKER_MODE_INVALID = "worker_mode_invalid"
    WORKER_PROTOCOL_MISMATCH = "worker_protocol_mismatch"
    WORKER_STARTUP_TIMEOUT = "worker_startup_timeout"
    WORKER_CALL_TIMEOUT = "worker_call_timeout"
    WORKER_CRASHED = "worker_crashed"
    WORKER_RESTART_QUARANTINED = "worker_restart_quarantined"
    BROKER_DENIED = "broker_denied"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    RESOURCE_LIMIT_UNAVAILABLE = "resource_limit_unavailable"
    SIGNATURE_INVALID = "signature_invalid"
    PUBLISHER_UNTRUSTED = "publisher_untrusted"
    PACKAGE_TAMPERED = "package_tampered"
    DEPENDENCY_CONSTRAINT_INVALID = "dependency_constraint_invalid"
    DEPENDENCY_CONFLICT = "dependency_conflict"
    OPERATION_IN_FLIGHT = "operation_in_flight"


@dataclass(frozen=True)
class ExtensionDiagnostic:
    code: DiagnosticCode
    severity: DiagnosticSeverity
    message: str
    extension_id: Optional[str] = None
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message,
            "extension_id": self.extension_id,
            "context": dict(self.context),
        }

    @classmethod
    def error(
        cls, code: DiagnosticCode, message: str,
        extension_id: Optional[str] = None, **context: Any,
    ) -> "ExtensionDiagnostic":
        return cls(code, DiagnosticSeverity.ERROR, message, extension_id, context)

    @classmethod
    def warning(
        cls, code: DiagnosticCode, message: str,
        extension_id: Optional[str] = None, **context: Any,
    ) -> "ExtensionDiagnostic":
        return cls(code, DiagnosticSeverity.WARNING, message, extension_id, context)

    @classmethod
    def info(
        cls, code: DiagnosticCode, message: str,
        extension_id: Optional[str] = None, **context: Any,
    ) -> "ExtensionDiagnostic":
        return cls(code, DiagnosticSeverity.INFO, message, extension_id, context)


class ExtensionPlatformError(Exception):
    """携带 typed diagnostic 的平台异常（host 内部控制流 / CLI 呈现用）。"""

    def __init__(self, diagnostic: ExtensionDiagnostic):
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


def has_errors(diagnostics: list[ExtensionDiagnostic]) -> bool:
    return any(d.severity is DiagnosticSeverity.ERROR for d in diagnostics)


def max_severity(diagnostics: list[ExtensionDiagnostic]) -> Optional[DiagnosticSeverity]:
    if not diagnostics:
        return None
    order = [DiagnosticSeverity.INFO, DiagnosticSeverity.WARNING, DiagnosticSeverity.ERROR]
    return max((d.severity for d in diagnostics), key=order.index)
