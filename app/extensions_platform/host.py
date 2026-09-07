"""扩展生命周期运行时（ADR-0104 / Wave 2, 10-12）。

状态机::

    discover → discovered ──(validate)──→ compatible ──(activate)──→ active
                  │            │                │   ↘ degraded（警告级诊断）
                  │            └→ incompatible  └→ failed（回滚后）
                  └→ quarantined（信任封锁 / id 碰撞）
    active/degraded ──(deactivate)──→ compatible ──(unload)──→ discovered
    reload = deactivate? → unload → 重读 manifest → activate?

不变量：
- 激活原子：任一投影失败 → 台账逆序回滚 → failed，registry 零残留；
- 卸载无僵尸：deactivate 回滚全部投影，unload 清除 sys.modules 句柄；
- reload 幂等：两次 activate→deactivate→activate 后 registry 内容一致
  （conformance corpus 钉死）；
- 发现/激活有界：目录数、manifest 大小、声明条目数均有硬上界；
- 信任决定生死：BLOCKED → quarantined，永不 import；
- 依赖：required 缺失或成环 → failed（typed）；optional 缺失 → degraded。

线程模型：host 方法不做内部加锁——激活只发生在启动 lifespan（单线程）
或 CLI（单进程）。多线程并发激活不在 V1 契约内（文档明示）。
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .api_version import (
    CORE_API_VERSION,
    check_core_version_window,
    check_extension_api_compatibility,
)
from .context import ExtensionContext
from .diagnostics import (
    DiagnosticCode,
    DiagnosticSeverity,
    ExtensionDiagnostic,
    ExtensionPlatformError,
    has_errors,
)
from .discovery import DiscoveryResult, discover_extensions
from .ledger import ProjectionLedger
from .manifest import GisExtensionManifest
from .permissions import (
    HIGH_RISK_PERMISSIONS,
    grants_for,
    validate_declared_permissions,
)
from .trust import TrustLevel, resolve_trust

logger = logging.getLogger(__name__)

MODULE_PREFIX = "webgis_ext_"


class ExtensionState(str, Enum):
    DISCOVERED = "discovered"
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    DISABLED = "disabled"
    LOADING = "loading"
    ACTIVE = "active"
    DEGRADED = "degraded"
    FAILED = "failed"
    QUARANTINED = "quarantined"


@dataclass
class ExtensionRecord:
    manifest: GisExtensionManifest
    path: Path
    fingerprint: Optional[str]
    state: ExtensionState = ExtensionState.DISCOVERED
    trust: TrustLevel = TrustLevel.LOCAL_UNTRUSTED
    diagnostics: list[ExtensionDiagnostic] = field(default_factory=list)
    module: Any = None
    context: Optional[ExtensionContext] = None
    ledger: Optional[ProjectionLedger] = None
    fingerprint_at_activation: Optional[str] = None

    @property
    def extension_id(self) -> str:
        return self.manifest.id


@dataclass(frozen=True)
class HostPolicy:
    """host 运行策略（由设置解析；测试可手工构造）。"""

    roots: tuple[Path, ...] = ()
    allow: frozenset[str] = frozenset()
    block: frozenset[str] = frozenset()
    builtin_ids: frozenset[str] = frozenset()
    grants: dict[str, frozenset[str]] = field(default_factory=dict)
    feature_flags: dict[str, bool] = field(default_factory=dict)
    extension_settings: dict[str, dict[str, Any]] = field(default_factory=dict)


class ExtensionHost:
    def __init__(self, tool_registry: Any, policy: HostPolicy) -> None:
        self._tool_registry = tool_registry
        self._policy = policy
        self._records: dict[str, ExtensionRecord] = {}

    # ── 构造 ─────────────────────────────────────────────────────────
    @classmethod
    def from_settings(cls, tool_registry: Any) -> "ExtensionHost":
        from .settings_bridge import host_policy_from_settings

        return cls(tool_registry=tool_registry, policy=host_policy_from_settings())

    # ── discover / validate ──────────────────────────────────────────
    def discover(self) -> list[ExtensionDiagnostic]:
        result: DiscoveryResult = discover_extensions(list(self._policy.roots))
        diagnostics: list[ExtensionDiagnostic] = list(result.diagnostics)
        for failure in result.failures:
            diagnostics.extend(failure.diagnostics)
        for discovered in result.extensions:
            diagnostics.extend(discovered.diagnostics)
            trust = resolve_trust(
                discovered.extension_id,
                allowlist=self._policy.allow,
                blocklist=self._policy.block,
                builtin_ids=self._policy.builtin_ids,
            )
            record = ExtensionRecord(
                manifest=discovered.manifest,
                path=discovered.path,
                fingerprint=discovered.fingerprint,
                trust=trust,
                diagnostics=list(discovered.diagnostics),
            )
            if trust is TrustLevel.BLOCKED:
                record.state = ExtensionState.QUARANTINED
                record.diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.TRUST_BLOCKED,
                        "extension is blocked by operator policy (EXTENSIONS_BLOCK)",
                        extension_id=discovered.extension_id,
                    )
                )
            else:
                record.state = ExtensionState.DISCOVERED
            self._records[record.extension_id] = record
        for record in self._records.values():
            self.validate_extension(record.extension_id)
        return diagnostics

    def validate_extension(self, extension_id: str) -> list[ExtensionDiagnostic]:
        record = self._records.get(extension_id)
        if record is None:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"unknown extension {extension_id!r}"
                )
            ]
        diagnostics = list(record.diagnostics)
        if record.state is ExtensionState.QUARANTINED:
            return diagnostics
        # API / 核心版本兼容（Wave 12，纯函数判定）。
        api_check = check_extension_api_compatibility(record.manifest.api_version)
        if not api_check.compatible:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.API_VERSION_INCOMPATIBLE,
                    api_check.reason or "api_version incompatible",
                    extension_id=extension_id,
                )
            )
        window_check = check_core_version_window(
            record.manifest.minimum_core_version, record.manifest.maximum_core_version
        )
        if not window_check.compatible:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.CORE_VERSION_INCOMPATIBLE,
                    window_check.reason or "core version window violated",
                    extension_id=extension_id,
                )
            )
        # 权限声明合法性。
        diagnostics.extend(validate_declared_permissions(extension_id, record.manifest.permissions))
        # 信任级别 vs 高危权限。
        if record.trust is TrustLevel.LOCAL_UNTRUSTED:
            risky = sorted(set(record.manifest.permissions) & HIGH_RISK_PERMISSIONS)
            if risky:
                diagnostics.append(
                    ExtensionDiagnostic.warning(
                        DiagnosticCode.TRUST_BLOCKED,
                        f"local_untrusted extension declares high-risk permissions {risky}; "
                        "grants still required at runtime (no automatic elevation)",
                        extension_id=extension_id,
                    )
                )
        # 依赖可解析 + 环检测（在 discovered 集合内）。
        diagnostics.extend(self._check_dependencies(record))
        # 入口文件存在（不 import——import 只发生在 activate）。
        if self._resolve_entry_path(record) is None:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.ENTRY_POINT_MISSING,
                    f"entry_point {record.manifest.entry_point!r} not found under {str(record.path)!r}",
                    extension_id=extension_id,
                )
            )
        record.diagnostics = diagnostics
        if record.state is ExtensionState.DISCOVERED:
            record.state = (
                ExtensionState.INCOMPATIBLE
                if has_errors(diagnostics)
                else ExtensionState.COMPATIBLE
            )
        elif record.state is ExtensionState.COMPATIBLE and has_errors(diagnostics):
            record.state = ExtensionState.INCOMPATIBLE
        return diagnostics

    def _check_dependencies(self, record: ExtensionRecord) -> list[ExtensionDiagnostic]:
        diagnostics: list[ExtensionDiagnostic] = []
        flags = self._effective_flags(record.manifest)
        for dep in record.manifest.dependencies:
            if dep.feature_flag and not flags.get(dep.feature_flag, False):
                continue
            dep_record = self._records.get(dep.id)
            if dep_record is None or dep_record.state is ExtensionState.QUARANTINED:
                diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.DEPENDENCY_MISSING,
                        f"required dependency {dep.id!r} not discovered",
                        extension_id=record.extension_id,
                    )
                )
        for dep in record.manifest.optional_dependencies:
            if dep.id not in self._records:
                diagnostics.append(
                    ExtensionDiagnostic.warning(
                        DiagnosticCode.OPTIONAL_DEPENDENCY_ABSENT,
                        f"optional dependency {dep.id!r} absent (activation will degrade)",
                        extension_id=record.extension_id,
                    )
                )
        # 成环检测（仅 required 边，DFS 三色标记，确定性遍历序）。
        color: dict[str, int] = {}
        for start in sorted(self._records):
            if color.get(start):
                continue
            stack = [(start, iter(self._required_dep_ids(start)))]
            color[start] = 1
            path = [start]
            while stack:
                node, it = stack[-1]
                advanced = False
                for nxt in it:
                    if color.get(nxt) == 1:
                        cycle = path[path.index(nxt):] + [nxt]
                        diagnostics.append(
                            ExtensionDiagnostic.error(
                                DiagnosticCode.DEPENDENCY_CYCLE,
                                "dependency cycle: " + " -> ".join(cycle),
                                extension_id=cycle[0],
                            )
                        )
                    elif not color.get(nxt):
                        color[nxt] = 1
                        path.append(nxt)
                        stack.append((nxt, iter(self._required_dep_ids(nxt))))
                        advanced = True
                        break
                if not advanced:
                    color[node] = 2
                    stack.pop()
                    if path and path[-1] == node:
                        path.pop()
        return diagnostics

    def _required_dep_ids(self, extension_id: str) -> list[str]:
        record = self._records.get(extension_id)
        if record is None:
            return []
        flags = self._effective_flags(record.manifest)
        return sorted(
            dep.id
            for dep in record.manifest.dependencies
            if not (dep.feature_flag and not flags.get(dep.feature_flag, False))
        )

    def _effective_flags(self, manifest: GisExtensionManifest) -> dict[str, bool]:
        flags = dict(manifest.feature_flags)
        flags.update(self._policy.feature_flags.get(manifest.id, {}))
        return flags

    # ── entry point 解析 ─────────────────────────────────────────────
    def _resolve_entry_path(self, record: ExtensionRecord) -> Optional[Path]:
        entry = record.manifest.entry_point.strip()
        if not entry or entry == "__init__":
            if (record.path / "__init__.py").is_file():
                return record.path / "__init__.py"
            return None
        candidate = record.path / f"{entry}.py"
        if candidate.is_file():
            return candidate
        package_init = record.path / entry / "__init__.py"
        if package_init.is_file():
            return package_init
        return None

    def _module_name(self, record: ExtensionRecord) -> str:
        # 指纹参与模块名：内容变化必然获得全新命名空间，杜绝「文件已改、
        # sys.modules 还挂着旧代码」的陈旧模块风险（unload/reload 按公共
        # 前缀清理所有代次）。
        fingerprint = record.fingerprint or "unknown"
        return f"{MODULE_PREFIX}{record.manifest.namespace}_{record.manifest.name}_{fingerprint[:12]}"

    def _load_entry_module(self, record: ExtensionRecord) -> Any:
        module_name = self._module_name(record)
        if module_name in sys.modules:
            return sys.modules[module_name]
        entry_path = self._resolve_entry_path(record)
        if entry_path is None:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.ENTRY_POINT_MISSING,
                    f"entry_point {record.manifest.entry_point!r} not found",
                    extension_id=record.extension_id,
                )
            )
        is_package = entry_path.name == "__init__.py"
        spec = importlib.util.spec_from_file_location(
            module_name,
            entry_path,
            submodule_search_locations=[str(record.path)] if is_package else None,
        )
        if spec is None or spec.loader is None:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.ENTRY_POINT_FAILED,
                    f"cannot build import spec for {str(entry_path)!r}",
                    extension_id=record.extension_id,
                )
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        return module

    def _purge_modules(self, record: ExtensionRecord) -> None:
        # 清理该扩展所有代次的模块（指纹后缀不同但公共前缀一致）。
        prefix = f"{MODULE_PREFIX}{record.manifest.namespace}_{record.manifest.name}"
        for name in list(sys.modules):
            if name == prefix or name.startswith(prefix + "_") or name.startswith(prefix + "."):
                sys.modules.pop(name, None)
        record.module = None

    # ── activate ─────────────────────────────────────────────────────
    def activate(self, extension_id: str) -> list[ExtensionDiagnostic]:
        record = self._records.get(extension_id)
        if record is None:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"unknown extension {extension_id!r}"
                )
            ]
        if record.state is ExtensionState.QUARANTINED:
            return list(record.diagnostics)
        if record.state is ExtensionState.ACTIVE or record.state is ExtensionState.DEGRADED:
            return []  # 幂等：重复 activate 是 no-op
        if record.state is ExtensionState.FAILED or record.state is ExtensionState.INCOMPATIBLE:
            # 失败后允许重试（代码可能已修复）：清掉上一轮诊断再重验，
            # 否则陈旧 error 会把重试永久钉死在 incompatible。
            record.state = ExtensionState.DISCOVERED
            record.diagnostics = []
            self.validate_extension(extension_id)
        if record.state is ExtensionState.INCOMPATIBLE:
            return list(record.diagnostics)
        if record.state is ExtensionState.DISCOVERED:
            self.validate_extension(extension_id)
            if record.state is ExtensionState.INCOMPATIBLE:
                return list(record.diagnostics)
        assert record.state is ExtensionState.COMPATIBLE, record.state

        flags = self._effective_flags(record.manifest)
        unresolved = [f for f, v in record.manifest.feature_flags.items() if f not in flags]
        warnings: list[ExtensionDiagnostic] = []
        for flag in unresolved:
            warnings.append(
                ExtensionDiagnostic.warning(
                    DiagnosticCode.FEATURE_FLAG_UNRESOLVED,
                    f"feature flag {flag!r} has no host override; using manifest default",
                    extension_id=extension_id,
                )
            )
        # required 依赖必须已激活。
        for dep in record.manifest.dependencies:
            if dep.feature_flag and not flags.get(dep.feature_flag, False):
                continue
            dep_record = self._records.get(dep.id)
            if dep_record is None or dep_record.state not in (
                ExtensionState.ACTIVE, ExtensionState.DEGRADED
            ):
                record.state = ExtensionState.FAILED
                record.diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.DEPENDENCY_MISSING,
                        f"dependency {dep.id!r} is not active (activate it first "
                        "or use activate_all)",
                        extension_id=extension_id,
                    )
                )
                return list(record.diagnostics)
        # optional 依赖缺席 → 激活继续，但 degraded。
        optional_absent = False
        for dep in record.manifest.optional_dependencies:
            dep_record = self._records.get(dep.id)
            if dep_record is None or dep_record.state not in (
                ExtensionState.ACTIVE, ExtensionState.DEGRADED
            ):
                optional_absent = True
                warnings.append(
                    ExtensionDiagnostic.warning(
                        DiagnosticCode.OPTIONAL_DEPENDENCY_ABSENT,
                        f"optional dependency {dep.id!r} absent; degraded activation",
                        extension_id=extension_id,
                    )
                )

        record.state = ExtensionState.LOADING
        ledger = ProjectionLedger(extension_id=extension_id)
        grants = grants_for(extension_id, self._policy.grants)
        context = ExtensionContext(
            manifest=record.manifest,
            trust=record.trust,
            grants=grants,
            settings=dict(self._policy.extension_settings.get(extension_id, {})),
            tool_registry=self._tool_registry,
            ledger=ledger,
        )
        try:
            module = self._load_entry_module(record)
            activate_fn = getattr(module, "activate", None)
            if not callable(activate_fn):
                raise ExtensionPlatformError(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.ENTRY_POINT_MISSING,
                        "entry module does not define activate(ctx)",
                        extension_id=extension_id,
                    )
                )
            activate_fn(context)
        except ExtensionPlatformError as exc:
            return self._fail_activation(record, ledger, [exc.diagnostic], warnings)
        except Exception as exc:  # noqa: BLE001 - 扩展代码任意异常都不得拖垮宿主
            return self._fail_activation(
                record,
                ledger,
                [
                    ExtensionDiagnostic.error(
                        DiagnosticCode.ENTRY_POINT_FAILED,
                        f"activate() raised {type(exc).__name__}: {exc}",
                        extension_id=extension_id,
                    )
                ],
                warnings,
            )
        # 声明 ↔ 实际核对（undeclared = error；declared but missing = warning）。
        reconciliation = self._reconcile_declarations(record, context)
        hard_errors = [d for d in reconciliation if d.severity is DiagnosticSeverity.ERROR]
        soft_warnings = [d for d in reconciliation if d.severity is DiagnosticSeverity.WARNING]
        if hard_errors:
            return self._fail_activation(record, ledger, hard_errors, warnings)
        # 健康门：激活后立即跑一次 health，unhealthy → 回滚。
        health_report = self._run_health(record, module)
        if health_report.get("status") == "unhealthy":
            return self._fail_activation(
                record,
                ledger,
                [
                    ExtensionDiagnostic.error(
                        DiagnosticCode.HEALTH_UNHEALTHY,
                        f"post-activation health check unhealthy: {health_report.get('messages')}",
                        extension_id=extension_id,
                    )
                ],
                warnings,
            )
        if health_report.get("status") == "degraded":
            warnings.append(
                ExtensionDiagnostic.warning(
                    DiagnosticCode.HEALTH_CHECK_FAILED,
                    f"health check degraded: {health_report.get('messages')}",
                    extension_id=extension_id,
                )
            )
        record.module = module
        record.context = context
        record.ledger = ledger
        fingerprint, fp_diag = _refingerprint(record)
        if fp_diag is not None:
            warnings.append(fp_diag)
        elif fingerprint != record.fingerprint:
            warnings.append(
                ExtensionDiagnostic.warning(
                    DiagnosticCode.FINGERPRINT_CHANGED,
                    "extension content changed since discovery",
                    extension_id=extension_id,
                )
            )
            record.fingerprint = fingerprint
        record.fingerprint_at_activation = fingerprint
        record.diagnostics = warnings + soft_warnings
        record.state = (
            ExtensionState.DEGRADED
            if (warnings or soft_warnings or optional_absent)
            else ExtensionState.ACTIVE
        )
        logger.info("extension %s activated (state=%s)", extension_id, record.state.value)
        return list(record.diagnostics)

    def _reconcile_declarations(
        self, record: ExtensionRecord, context: ExtensionContext
    ) -> list[ExtensionDiagnostic]:
        diagnostics: list[ExtensionDiagnostic] = []
        registered = context.registered_ids()
        declared_tools = {record.manifest.namespaced_tool_name(t.name) for t in record.manifest.tools}
        for projected in sorted(declared_tools - registered.get("tool", set())):
            diagnostics.append(
                ExtensionDiagnostic.warning(
                    DiagnosticCode.DECLARED_BUT_UNREGISTERED,
                    f"declared tool {projected!r} was not registered (flag-gated?)",
                    extension_id=record.extension_id,
                )
            )
        for kind, declared in (
            ("algorithm", {record.manifest.namespaced_algorithm_id(a.id) for a in record.manifest.algorithms}),
            ("data_provider", {record.manifest.namespaced_source_type(p.source_type) for p in record.manifest.data_providers}),
        ):
            for projected in sorted(declared - registered.get(kind, set())):
                diagnostics.append(
                    ExtensionDiagnostic.warning(
                        DiagnosticCode.DECLARED_BUT_UNREGISTERED,
                        f"declared {kind} {projected!r} was not registered (flag-gated?)",
                        extension_id=record.extension_id,
                    )
                )
        return diagnostics

    def _fail_activation(
        self,
        record: ExtensionRecord,
        ledger: ProjectionLedger,
        errors: list[ExtensionDiagnostic],
        warnings: list[ExtensionDiagnostic],
    ) -> list[ExtensionDiagnostic]:
        rollback_diagnostics = ledger.rollback()
        record.state = ExtensionState.FAILED
        record.diagnostics = warnings + errors + rollback_diagnostics
        logger.warning(
            "extension %s activation failed: %s",
            record.extension_id,
            [e.message for e in errors],
        )
        return list(record.diagnostics)

    # ── deactivate / unload / reload ─────────────────────────────────
    def deactivate(self, extension_id: str) -> list[ExtensionDiagnostic]:
        record = self._records.get(extension_id)
        if record is None:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"unknown extension {extension_id!r}"
                )
            ]
        if record.state not in (ExtensionState.ACTIVE, ExtensionState.DEGRADED):
            return [
                ExtensionDiagnostic.warning(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"extension {extension_id!r} is {record.state.value}, not active",
                    extension_id=extension_id,
                )
            ]
        diagnostics: list[ExtensionDiagnostic] = []
        if record.module is not None:
            deactivate_fn = getattr(record.module, "deactivate", None)
            if callable(deactivate_fn):
                try:
                    deactivate_fn(record.context)
                except Exception as exc:  # noqa: BLE001 - 停用钩子失败不阻断回滚
                    diagnostics.append(
                        ExtensionDiagnostic.warning(
                            DiagnosticCode.ENTRY_POINT_FAILED,
                            f"deactivate() raised {type(exc).__name__}: {exc}",
                            extension_id=extension_id,
                        )
                    )
        assert record.ledger is not None
        diagnostics.extend(record.ledger.rollback())
        record.context = None
        record.ledger = None
        record.state = ExtensionState.COMPATIBLE
        logger.info("extension %s deactivated", extension_id)
        return diagnostics

    def unload(self, extension_id: str) -> list[ExtensionDiagnostic]:
        record = self._records.get(extension_id)
        if record is None:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"unknown extension {extension_id!r}"
                )
            ]
        if record.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED, ExtensionState.LOADING):
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"extension {extension_id!r} must be deactivated before unload",
                    extension_id=extension_id,
                )
            ]
        self._purge_modules(record)
        record.state = ExtensionState.DISCOVERED
        return []

    def reload(self, extension_id: str, activate: bool = True) -> list[ExtensionDiagnostic]:
        record = self._records.get(extension_id)
        if record is None:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"unknown extension {extension_id!r}"
                )
            ]
        diagnostics: list[ExtensionDiagnostic] = []
        was_active = record.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED)
        if was_active:
            diagnostics.extend(self.deactivate(extension_id))
        diagnostics.extend(self.unload(extension_id))
        manifest, err = _reread_manifest(record.path)
        if manifest is None:
            record.state = ExtensionState.FAILED
            record.diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"reload failed: {err}",
                    extension_id=extension_id,
                )
            )
            return list(record.diagnostics)
        record.manifest = manifest
        fingerprint, fp_diag = _refingerprint(record)
        if fp_diag is None:
            if record.fingerprint is not None and fingerprint != record.fingerprint:
                diagnostics.append(
                    ExtensionDiagnostic.warning(
                        DiagnosticCode.FINGERPRINT_CHANGED,
                        "extension content changed since last load",
                        extension_id=extension_id,
                    )
                )
            record.fingerprint = fingerprint
        record.state = ExtensionState.DISCOVERED
        record.diagnostics = []
        diagnostics.extend(self.validate_extension(extension_id))
        if activate and record.state is ExtensionState.COMPATIBLE:
            diagnostics.extend(self.activate(extension_id))
        return diagnostics

    # ── 批量激活（topo 序）────────────────────────────────────────────
    def activate_all(self) -> dict[str, list[ExtensionDiagnostic]]:
        results: dict[str, list[ExtensionDiagnostic]] = {}
        # Kahn 拓扑：节点 = compatible 扩展，边 = required 依赖。
        candidates = sorted(
            eid for eid, r in self._records.items() if r.state is ExtensionState.COMPATIBLE
        )
        indegree = {eid: 0 for eid in candidates}
        dependents: dict[str, list[str]] = {eid: [] for eid in candidates}
        for eid in candidates:
            for dep in self._required_dep_ids(eid):
                if dep in indegree:
                    indegree[eid] += 1
                    dependents[dep].append(eid)
        ready = sorted(eid for eid, d in indegree.items() if d == 0)
        ordered: list[str] = []
        while ready:
            eid = ready.pop(0)
            ordered.append(eid)
            for dependent in sorted(dependents[eid]):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
            ready.sort()
        # 环内成员不在 ordered 中（validate 阶段已诊断），跳过即可。
        for eid in ordered:
            results[eid] = self.activate(eid)
        return results

    # ── health ───────────────────────────────────────────────────────
    def health(self, extension_id: str) -> dict[str, Any]:
        record = self._records.get(extension_id)
        if record is None:
            return {"status": "unknown", "messages": [f"unknown extension {extension_id!r}"]}
        if record.state not in (ExtensionState.ACTIVE, ExtensionState.DEGRADED):
            return {
                "status": "unhealthy",
                "messages": [f"extension is {record.state.value}"],
            }
        report = self._run_health(record, record.module)
        report["state"] = record.state.value
        return report

    def _run_health(self, record: ExtensionRecord, module: Any) -> dict[str, Any]:
        entry = record.manifest.diagnostics_entry
        if not entry:
            return {"status": "healthy", "messages": []}
        module_name, _, fn_name = entry.partition(":")
        if not fn_name:
            fn_name = module_name
            owner = module
        else:
            owner = getattr(module, module_name, None)
        health_fn = getattr(owner, fn_name, None) if owner is not None else None
        if not callable(health_fn):
            return {
                "status": "degraded",
                "messages": [f"diagnostics entry {entry!r} not resolvable"],
            }
        try:
            result = health_fn()
        except Exception as exc:  # noqa: BLE001 - 健康检查失败 ≠ 宿主失败
            return {
                "status": "degraded",
                "messages": [f"health check raised {type(exc).__name__}: {exc}"],
            }
        if isinstance(result, dict) and "status" in result:
            return result
        status = getattr(result, "status", None)
        if status:
            return {
                "status": str(status),
                "messages": list(getattr(result, "messages", []) or []),
            }
        return {"status": "healthy", "messages": []}

    # ── 内省（CLI / 诊断）─────────────────────────────────────────────
    def status_report(self) -> dict[str, Any]:
        extensions = []
        for eid in sorted(self._records):
            record = self._records[eid]
            extensions.append(
                {
                    "id": eid,
                    "namespace": record.manifest.namespace,
                    "version": record.manifest.version,
                    "api_version": record.manifest.api_version,
                    "state": record.state.value,
                    "trust": record.trust.value,
                    "declared_types": sorted(record.manifest.declared_type_set()),
                    "permissions": sorted(record.manifest.permissions),
                    "granted_permissions": sorted(
                        grants_for(eid, self._policy.grants).granted
                    ),
                    "entry_point": record.manifest.entry_point,
                    "host_api_version": CORE_API_VERSION,
                    "diagnostics": [d.to_dict() for d in record.diagnostics],
                }
            )
        return {
            "host_api_version": CORE_API_VERSION,
            "extensions": extensions,
        }

    def get_record(self, extension_id: str) -> Optional[ExtensionRecord]:
        return self._records.get(extension_id)

    def extension_ids(self) -> list[str]:
        return sorted(self._records)

    def reset(self) -> None:
        """测试专用：回滚一切并清空索引（不做 sys.modules 清理之外的事）。"""
        for eid in list(self._records):
            record = self._records[eid]
            if record.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED):
                self.deactivate(eid)
            self._purge_modules(record)
        self._records.clear()


# 单例管理（main lifespan 注入 tool registry 后构建；测试可自建 host）。
_host: Optional[ExtensionHost] = None


def configure_extension_host(host: ExtensionHost) -> ExtensionHost:
    global _host
    _host = host
    return host


def get_extension_host() -> Optional[ExtensionHost]:
    return _host


def _refingerprint(record: ExtensionRecord) -> tuple[Optional[str], Optional[ExtensionDiagnostic]]:
    from .discovery import compute_fingerprint

    return compute_fingerprint(record.path)


def _reread_manifest(path: Path) -> tuple[Optional[GisExtensionManifest], Optional[str]]:
    from .discovery import MANIFEST_FILENAME, _parse_manifest_file

    manifest, _diags, _ = _parse_manifest_file(path / MANIFEST_FILENAME)
    if manifest is None:
        return None, "manifest re-read failed"
    return manifest, None
