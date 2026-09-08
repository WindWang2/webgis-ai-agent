"""扩展生命周期运行时（ADR-0104 / Wave 2, 10-12）。

状态机::

    discover → discovered ──(validate)──→ compatible ──(activate)──→ active
                  │            │                │   ↘ degraded（警告级诊断）
                  │            └→ incompatible  └→ failed（回滚后）
                  └→ quarantined（信任封锁 / id 碰撞 / 签名无效或篡改）
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

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .api_version import (
    CORE_API_VERSION,
    check_core_version_window,
    check_extension_api_compatibility,
    parse_version,
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
# MODULE_PREFIX 由 loader.py 单一维护；此处保留 re-export 兼容旧引用。
from .loader import MODULE_PREFIX  # noqa: F401
from .manifest import GisExtensionManifest
from .permissions import (
    HIGH_RISK_PERMISSIONS,
    grants_for,
    validate_declared_permissions,
)
from . import resolver
from .signing import (
    STATUS_INVALID,
    STATUS_MISSING,
    STATUS_SIGNED_UNTRUSTED,
    STATUS_SIGNED_VERIFIED,
    STATUS_TAMPERED,
    SignatureStatus,
    verify_pack_signature,
)
from .trust import TrustLevel, resolve_trust

logger = logging.getLogger(__name__)


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
    # Round-1 审计 M2：激活本扩展时实际依赖的（required 且已激活的）扩展
    # id。用于停用前置检查——依赖被停用而依赖者仍激活会留下悬空引用。
    satisfied_dependencies: frozenset[str] = frozenset()
    # Round-1 审计 minor14：发现期诊断基线。validate_extension 由此重算，
    # 保证重复校验幂等、失败后的重试不被陈旧 error 永久锁死。
    baseline_diagnostics: tuple[ExtensionDiagnostic, ...] = ()
    # ── V2（ADR-0105）：worker 隔离执行 ──────────────────────────────
    # worker 模式下非 None；in-process 模式恒为 None。
    worker: Any = None
    worker_crash_count: int = 0

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
    # Round-1 审计 F3：local_untrusted 扩展默认仅可 inspect/validate。
    # 生产（settings 桥）默认 False——必须 EXTENSIONS_ALLOW 点名或显式
    # 打开 EXTENSIONS_ACTIVATE_UNTRUSTED；进程内直构 HostPolicy 的测试
    # 缺省 True（本地开发语义）。
    allow_local_untrusted_activation: bool = True
    # ── V2（ADR-0105）────────────────────────────────────────────────
    # 按扩展 id 供给的凭据（供给即授权；值不进入任何状态/日志面）。
    secrets: dict[str, dict[str, str]] = field(default_factory=dict)
    # worker broker 出网 allowlist：ext_id → host 集合（"*" = 全部放行）。
    network_allow: dict[str, frozenset[str]] = field(default_factory=dict)
    # worker broker artifact 根目录（空 = 拒绝全部 artifact 操作）。
    artifact_roots: tuple[Path, ...] = ()
    # 受信发布者：key_id → HMAC 密钥文件路径。
    trusted_publishers: dict[str, Path] = field(default_factory=dict)
    # 验签通过且发布者受信 → 提权 trusted_extension。
    trust_signed: bool = False
    # 未签名包显式开发模式（大声告警；不改变权限语义）。
    allow_unsigned_dev: bool = False
    # worker 连续崩溃达到该值 → quarantine。
    max_worker_crashes: int = 2


class ExtensionHost:
    def __init__(self, tool_registry: Any, policy: HostPolicy) -> None:
        self._tool_registry = tool_registry
        self._policy = policy
        self._records: dict[str, ExtensionRecord] = {}
        # V2：按扩展 id 的 broker 审计环（bounded；CLI/status 消费）。
        self._broker_audit: dict[str, Any] = {}
        # V2（Wave 9）：投影变化钩子（main lifespan 接权威视图刷新器）。
        self._projection_hook: Any = None

    # ── 构造 ─────────────────────────────────────────────────────────
    @classmethod
    def from_settings(cls, tool_registry: Any) -> "ExtensionHost":
        from .settings_bridge import host_policy_from_settings

        return cls(tool_registry=tool_registry, policy=host_policy_from_settings())

    def set_projection_change_hook(self, hook: Any) -> None:
        """V2：注册投影变化回调（extension_id, event）。

        event ∈ {"activate", "deactivate", "rollback", "failed"}；回调异常
        被吞并告警——刷新失败绝不把生命周期操作变成宿主故障。
        """
        self._projection_hook = hook

    def _notify_projection_change(self, extension_id: str, event: str) -> None:
        if self._projection_hook is None:
            return
        try:
            self._projection_hook(extension_id, event)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "projection-change hook failed after %s.%s: %s",
                extension_id, event, exc,
            )

    # ── discover / validate ──────────────────────────────────────────
    def discover(self) -> list[ExtensionDiagnostic]:
        result: DiscoveryResult = discover_extensions(list(self._policy.roots))
        diagnostics: list[ExtensionDiagnostic] = list(result.diagnostics)
        for failure in result.failures:
            diagnostics.extend(failure.diagnostics)
        for discovered in result.extensions:
            diagnostics.extend(discovered.diagnostics)
            existing = self._records.get(discovered.extension_id)
            if existing is not None and existing.state in (
                ExtensionState.ACTIVE,
                ExtensionState.DEGRADED,
                ExtensionState.LOADING,
                ExtensionState.DISABLED,
            ):
                # Round-1 审计 M3：对已激活扩展重复 discover 一律保留现记录
                # ——覆盖成 DISCOVERED 会让台账孤儿化（投影无法回滚 → 永久
                # 僵尸）。内容变化只告警，走 reload 才会真正换血。
                if discovered.fingerprint and discovered.fingerprint != existing.fingerprint:
                    diagnostics.append(
                        ExtensionDiagnostic.warning(
                            DiagnosticCode.FINGERPRINT_CHANGED,
                            f"active extension {discovered.extension_id!r} changed on "
                            "disk; use reload() to apply",
                            extension_id=discovered.extension_id,
                        )
                    )
                continue
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
                baseline_diagnostics=tuple(discovered.diagnostics),
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
                # Wave 6：验签信任流（BLOCKED 路径不验签——隔离语义已定，
                # 签名无从改变生死）。签名裁决属发现期事实，并入基线，
                # 否则末尾 validate_extension 重算诊断时会把它抹掉。
                record.diagnostics.extend(
                    self._apply_signature_verdict(
                        record,
                        verify_pack_signature(
                            discovered.path, self._policy.trusted_publishers
                        ),
                    )
                )
                record.baseline_diagnostics = tuple(record.diagnostics)
            self._records[record.extension_id] = record
        for record in self._records.values():
            self.validate_extension(record.extension_id)
        return diagnostics

    def _apply_signature_verdict(
        self, record: ExtensionRecord, status: SignatureStatus
    ) -> list[ExtensionDiagnostic]:
        """Wave 6：按验签裁决执行信任流（BLOCKED 路径不进入本方法）。

        - tampered / invalid → QUARANTINED（即使 allowlist 点名也不放行：
          内容被篡改或签名损坏的包必须重签后重新发现；既有 QUARANTINED
          状态机保证永不 import、永不激活）；
        - signed_verified + trust_signed → local_untrusted 提权
          trusted_extension（已有信任级别保持不变，只升不降），info 留痕；
        - signed_untrusted / missing → 依 trust_signed / allow_unsigned_dev
          大声告警；缺省策略下 missing 不产出任何诊断（V1 行为逐字节保持）。
        """
        extension_id = record.extension_id
        if status.status == STATUS_TAMPERED:
            record.state = ExtensionState.QUARANTINED
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.PACKAGE_TAMPERED,
                    "pack content changed after signing (fingerprint mismatch vs "
                    f"signature.json, publisher {status.publisher!r}); quarantined "
                    "even if allowlisted — re-sign and re-discover",
                    extension_id=extension_id,
                )
            ]
        if status.status == STATUS_INVALID:
            record.state = ExtensionState.QUARANTINED
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.SIGNATURE_INVALID,
                    f"signature.json is invalid ({status.detail}); quarantined — "
                    "fix or remove the signature and re-discover",
                    extension_id=extension_id,
                )
            ]
        diagnostics: list[ExtensionDiagnostic] = []
        if status.status == STATUS_SIGNED_VERIFIED:
            if self._policy.trust_signed:
                if record.trust is TrustLevel.LOCAL_UNTRUSTED:
                    record.trust = TrustLevel.TRUSTED_EXTENSION
                diagnostics.append(
                    ExtensionDiagnostic.info(
                        DiagnosticCode.SIGNATURE_VERIFIED,
                        f"signature verified for publisher {status.publisher!r}; "
                        "trust elevated by EXTENSIONS_TRUST_SIGNED",
                        extension_id=extension_id,
                    )
                )
        elif status.status == STATUS_SIGNED_UNTRUSTED:
            if self._policy.trust_signed:
                diagnostics.append(
                    ExtensionDiagnostic.warning(
                        DiagnosticCode.PUBLISHER_UNTRUSTED,
                        "signature present but publisher key unknown; falls back "
                        "to operator trust config",
                        extension_id=extension_id,
                    )
                )
        elif status.status == STATUS_MISSING:
            if self._policy.trust_signed:
                diagnostics.append(
                    ExtensionDiagnostic.warning(
                        DiagnosticCode.SIGNATURE_INVALID,
                        "unsigned pack under EXTENSIONS_TRUST_SIGNED policy; stays "
                        "local_untrusted unless explicitly allowed",
                        extension_id=extension_id,
                    )
                )
            elif (
                self._policy.allow_unsigned_dev
                and record.trust is TrustLevel.LOCAL_UNTRUSTED
            ):
                diagnostics.append(
                    ExtensionDiagnostic.warning(
                        DiagnosticCode.SIGNATURE_INVALID,
                        "unsigned dev mode (EXTENSIONS_ALLOW_UNSIGNED_DEV=true); "
                        "pack stays local_untrusted",
                        extension_id=extension_id,
                    )
                )
        return diagnostics

    def validate_extension(self, extension_id: str) -> list[ExtensionDiagnostic]:
        record = self._records.get(extension_id)
        if record is None:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"unknown extension {extension_id!r}"
                )
            ]
        # Round-1 审计 minor14：从发现期基线重算（幂等），此前每次调用
        # 在旧诊断上追加，重复校验会把陈旧 error 钉死在 incompatible。
        diagnostics = list(record.baseline_diagnostics)
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
        # V2（ADR-0105 Wave 8）：依赖版本约束校验（required 不满足 → error）。
        diagnostics.extend(
            resolver.constraint_diagnostics_for(
                _record_view(record),
                {eid: _record_view(rec) for eid, rec in self._records.items()},
            )
        )
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
        # Round-1 审计 M5：诊断只附加到【本记录所在】的环——validate_extension
        # 逐记录调用本方法，无条件附加全图环会把无关扩展连坐成 INCOMPATIBLE。
        for cycle in self._cycle_memberships(record.extension_id):
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.DEPENDENCY_CYCLE,
                    "dependency cycle: " + " -> ".join(cycle),
                    extension_id=record.extension_id,
                )
            )
        return diagnostics

    def _detect_dependency_cycles(self) -> list[list[str]]:
        """返回全部 required 依赖环（每个环一条，成员按遍历序）。"""
        color: dict[str, int] = {}
        cycles: list[list[str]] = []
        seen_cycles: set[tuple[str, ...]] = set()
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
                        key = tuple(sorted(set(cycle)))
                        if key not in seen_cycles:
                            seen_cycles.add(key)
                            cycles.append(cycle)
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
        return cycles

    def _cycle_memberships(self, extension_id: str) -> list[list[str]]:
        return [c for c in self._detect_dependency_cycles() if extension_id in c]

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

    # ── entry point 解析（共享实现见 loader.py；in-process 与 worker 同规则）──
    def _resolve_entry_path(self, record: ExtensionRecord) -> Optional[Path]:
        from .loader import resolve_entry_path

        return resolve_entry_path(record.path, record.manifest.entry_point)

    def _module_name(self, record: ExtensionRecord) -> str:
        # 指纹参与模块名：内容变化必然获得全新命名空间，杜绝「文件已改、
        # sys.modules 还挂着旧代码」的陈旧模块风险（unload/reload 按公共
        # 前缀清理所有代次）。
        from .loader import module_name_for

        return module_name_for(
            record.manifest.namespace, record.manifest.name, record.fingerprint
        )

    def _load_entry_module(self, record: ExtensionRecord) -> Any:
        from .loader import load_entry_module

        return load_entry_module(
            record.path,
            record.manifest.namespace,
            record.manifest.name,
            record.manifest.entry_point,
            record.fingerprint,
            extension_id=record.extension_id,
        )

    def _purge_modules(self, record: ExtensionRecord) -> None:
        from .loader import purge_modules

        purge_modules(
            record.manifest.namespace,
            record.manifest.name,
            record.fingerprint_at_activation,
            record.fingerprint,
        )
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
        if (
            record.trust is TrustLevel.LOCAL_UNTRUSTED
            and not self._policy.allow_local_untrusted_activation
        ):
            record.diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.TRUST_BLOCKED,
                    f"extension {extension_id!r} is local_untrusted; activation "
                    "requires EXTENSIONS_ALLOW allowlist or "
                    "EXTENSIONS_ACTIVATE_UNTRUSTED=true",
                    extension_id=extension_id,
                )
            )
            return list(record.diagnostics)
        if record.state is ExtensionState.DISABLED:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.EXTENSION_DISABLED,
                    f"extension {extension_id!r} is disabled by operator (enable first)",
                    extension_id=extension_id,
                )
            ]
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
        host_overridden = set(self._policy.feature_flags.get(extension_id, {}))
        unresolved = [
            f for f in record.manifest.feature_flags if f not in host_overridden
        ]
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
        if record.manifest.is_worker_mode:
            try:
                return self._activate_worker(record, warnings)
            except ExtensionPlatformError as exc:
                return self._fail_worker_activation(record, warnings, exc.diagnostic)
            except Exception as exc:  # noqa: BLE001 - spawn/pipe OSError 等兜底
                return self._fail_worker_activation(
                    record,
                    warnings,
                    ExtensionDiagnostic.error(
                        DiagnosticCode.ENTRY_POINT_FAILED,
                        f"worker startup failed: {type(exc).__name__}: {exc}",
                        extension_id=extension_id,
                    ),
                )
        ledger = ProjectionLedger(extension_id=extension_id)
        grants = grants_for(extension_id, self._policy.grants)
        context = ExtensionContext(
            manifest=record.manifest,
            trust=record.trust,
            grants=grants,
            settings=dict(self._policy.extension_settings.get(extension_id, {})),
            tool_registry=self._tool_registry,
            ledger=ledger,
            secrets=dict(self._policy.secrets.get(extension_id, {})),
        )
        context._module_dir = record.path
        try:
            module = self._load_entry_module(record)
            # 兄弟模块命名空间（load_sibling 用）必须在 activate 之前就绪。
            context._entry_module_name = module.__name__
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
        record.satisfied_dependencies = frozenset(
            dep.id
            for dep in record.manifest.dependencies
            if not (dep.feature_flag and not flags.get(dep.feature_flag, False))
        )
        fingerprint, fp_diag = _refingerprint(record)
        if fp_diag is not None:
            warnings.append(fp_diag)
        elif fingerprint != record.fingerprint:
            if record.trust in (TrustLevel.TRUSTED_BUILTIN, TrustLevel.TRUSTED_EXTENSION):
                # Round-1 审计 F5：受信扩展内容在发现后被改动 → 拒绝激活
                # （fail closed）；local_untrusted 保留告警（内容不受信，
                # 告警仅为审计留痕）。
                return self._fail_activation(
                    record,
                    ledger,
                    [
                        ExtensionDiagnostic.error(
                            DiagnosticCode.FINGERPRINT_CHANGED,
                            "trusted extension content changed since discovery; "
                            "re-discover before activation",
                            extension_id=extension_id,
                        )
                    ],
                    warnings,
                )
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
        self._notify_projection_change(extension_id, "activate")
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
            # Round-1 审计 minor9：cartography / workflow 声明节同样对账。
            ("cartography", {
                f"{record.manifest.namespace}_{c.id}"
                for c in record.manifest.cartography_items
            }),
        ):
            for projected in sorted(declared - registered.get(kind, set())):
                diagnostics.append(
                    ExtensionDiagnostic.warning(
                        DiagnosticCode.DECLARED_BUT_UNREGISTERED,
                        f"declared {kind} {projected!r} was not registered (flag-gated?)",
                        extension_id=record.extension_id,
                    )
                )
        # workflow pack 级对账：recipe 以命名空间为前缀（pack 名不进
        # recipe id），故按命名空间核对——声明了 pack 却零 recipe 投影
        # 才是「声明未注册」。
        # V2：model provider 工具投影对账（声明了却零投影 → warning）。
        declared_mp_tools = {
            record.manifest.namespaced_model_provider_tool(m.id)
            for m in record.manifest.model_providers
        }
        for projected in sorted(declared_mp_tools - registered.get("tool", set())):
            diagnostics.append(
                ExtensionDiagnostic.warning(
                    DiagnosticCode.DECLARED_BUT_UNREGISTERED,
                    f"declared model provider tool {projected!r} was not registered "
                    "(flag-gated?)",
                    extension_id=record.extension_id,
                )
            )
        declared_packs = {record.manifest.namespaced_tool_name(w.pack_id) for w in record.manifest.workflow_packs}
        registered_recipes = registered.get("workflow_recipe", set())
        ns_recipe_prefix = record.manifest.namespace + "_"
        if declared_packs and not any(
            rid.startswith(ns_recipe_prefix) for rid in registered_recipes
        ):
                diagnostics.append(
                    ExtensionDiagnostic.warning(
                        DiagnosticCode.DECLARED_BUT_UNREGISTERED,
                        "declared workflow packs registered no recipes (flag-gated?)",
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
        self._notify_projection_change(record.extension_id, "rollback")
        return list(record.diagnostics)

    # ── V2：worker 隔离执行（ADR-0105）────────────────────────────────
    def _activate_worker(
        self, record: ExtensionRecord, warnings: list[ExtensionDiagnostic]
    ) -> list[ExtensionDiagnostic]:
        """worker 模式激活：spawn 隔离进程，宿主只投影 proxy 工具。

        与 in-process 同样的原子性：任何失败 → 已注册 proxy 逆序回滚 →
        FAILED；worker 进程保证被回收。声明对账沿用同一规则（undeclared
        = error；declared but missing = warning）。
        """
        from .ledger import ProjectionLedger
        from .worker.client import WorkerProcess

        manifest = record.manifest
        execution = manifest.execution
        assert execution is not None
        ledger = ProjectionLedger(extension_id=record.extension_id)
        worker = WorkerProcess(
            pack_dir=record.path,
            extension_id=manifest.id,
            namespace=manifest.namespace,
            name=manifest.name,
            fingerprint=record.fingerprint or "",
            grants=sorted(self._policy.grants.get(manifest.id, frozenset())),
            settings=dict(self._policy.extension_settings.get(manifest.id, {})),
            startup_timeout_s=execution.startup_timeout_s,
            call_timeout_s=execution.call_timeout_s,
            max_memory_mb=execution.max_memory_mb,
            max_cpu_seconds=execution.max_cpu_seconds,
            broker_handler=self._make_broker_handler(record.extension_id),
        )
        try:
            worker.start()
        except ExtensionPlatformError as exc:
            record.worker_crash_count += 1
            return self._fail_worker_activation(record, warnings, exc.diagnostic)
        # 平台不支持的资源强制 → typed 降级告警（不虚假承诺沙箱能力）。
        for message in worker.resource_warnings:
            warnings.append(
                ExtensionDiagnostic.warning(
                    DiagnosticCode.RESOURCE_LIMIT_UNAVAILABLE,
                    message,
                    extension_id=record.extension_id,
                )
            )
        # 声明对账：worker 握手申报 vs manifest 声明（undeclared = error）。
        declared = {manifest.namespaced_tool_name(t.name) for t in manifest.tools}
        declared |= {manifest.namespaced_model_provider_tool(m.id) for m in manifest.model_providers}
        offered = {str(t.get("name")) for t in worker.tools}
        undeclared = sorted(offered - declared)
        if undeclared:
            worker.shutdown()
            return self._fail_worker_activation(
                record,
                warnings,
                ExtensionDiagnostic.error(
                    DiagnosticCode.UNDECLARED_REGISTRATION,
                    f"worker offered undeclared tools {undeclared} "
                    "(declaration and handshake must match; fail closed)",
                    extension_id=record.extension_id,
                ),
            )
        missing = sorted(declared - offered)
        if missing:
            warnings.append(
                ExtensionDiagnostic.warning(
                    DiagnosticCode.DECLARED_BUT_UNREGISTERED,
                    f"declared tools {missing} were not offered by the worker "
                    "(flag-gated?)",
                    extension_id=record.extension_id,
                )
            )
        # proxy 投影（经台账，保证失败逆序回滚）。
        for tool in worker.tools:
            projected = str(tool.get("name"))
            kwargs = dict(tool.get("kwargs") or {})
            description = str(tool.get("description") or "")
            proxy = self._make_worker_proxy(record, worker, projected, execution.call_timeout_s)
            try:
                self._tool_registry.register(projected, description, proxy, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 归一为投影失败
                worker.shutdown()
                return self._fail_worker_activation(
                    record,
                    warnings + ledger.rollback(),
                    ExtensionDiagnostic.error(
                        DiagnosticCode.REGISTRY_PROJECTION_FAILED,
                        f"worker tool {projected!r} rejected by ToolRegistry: {exc}",
                        extension_id=record.extension_id,
                    ),
                )
            ledger.record("tool", projected, lambda n=projected: self._tool_registry.unregister(n))
            logger.info("extension %s projected worker tool %s", record.extension_id, projected)
        # 健康门：带超时 RPC（worker 模式不再有 unbounded sync health）。
        try:
            health_report = worker.health()
        except ExtensionPlatformError as exc:
            worker.shutdown()
            return self._fail_worker_activation(
                record, warnings + ledger.rollback(), exc.diagnostic
            )
        if health_report.get("status") == "unhealthy":
            worker.shutdown()
            return self._fail_worker_activation(
                record,
                warnings + ledger.rollback(),
                ExtensionDiagnostic.error(
                    DiagnosticCode.HEALTH_UNHEALTHY,
                    f"post-activation worker health unhealthy: {health_report.get('messages')}",
                    extension_id=record.extension_id,
                ),
            )
        if health_report.get("status") == "degraded":
            warnings.append(
                ExtensionDiagnostic.warning(
                    DiagnosticCode.HEALTH_CHECK_FAILED,
                    f"worker health degraded: {health_report.get('messages')}",
                    extension_id=record.extension_id,
                )
            )
        record.worker = worker
        record.ledger = ledger
        effective_flags = self._effective_flags(manifest)
        record.satisfied_dependencies = frozenset(
            dep.id
            for dep in manifest.dependencies
            if not (
                dep.feature_flag and not effective_flags.get(dep.feature_flag, False)
            )
        )
        record.fingerprint_at_activation = record.fingerprint
        record.diagnostics = warnings
        record.state = ExtensionState.DEGRADED if warnings else ExtensionState.ACTIVE
        logger.info(
            "extension %s activated in worker mode (state=%s, pid=%s)",
            record.extension_id, record.state.value, worker.pid,
        )
        self._notify_projection_change(record.extension_id, "activate")
        return list(record.diagnostics)

    def _fail_worker_activation(
        self,
        record: ExtensionRecord,
        diagnostics: list[ExtensionDiagnostic],
        error: ExtensionDiagnostic,
    ) -> list[ExtensionDiagnostic]:
        """worker 激活失败：无台账可回（proxy 注册前失败或已回滚）。"""
        record.state = ExtensionState.FAILED
        record.diagnostics = list(diagnostics) + [error]
        logger.warning(
            "extension %s worker activation failed: %s", record.extension_id, error.message
        )
        self._notify_projection_change(record.extension_id, "failed")
        return list(record.diagnostics)

    # ── V2：model provider 调用面（流式仅 in-process；worker 单帧）────
    def invoke_model_provider(
        self,
        projected_tool: str,
        request: dict[str, Any] | None = None,
        *,
        stream: bool = False,
    ) -> Any:
        """直接调用已投影的扩展 model provider。

        in-process：``stream=True`` 返回原始事件迭代器（协作式取消 =
        提前 close）；``stream=False`` 返回聚合结果。
        worker：仅聚合单帧；``stream=True`` → typed 拒绝。
        """
        for eid in sorted(self._records):
            record = self._records[eid]
            if record.state not in (
                ExtensionState.ACTIVE, ExtensionState.DEGRADED
            ):
                continue
            # worker 模式：spec 不在宿主进程（record.context is None）；
            # 经 worker call 单帧往返。
            if record.worker is not None:
                for provider in record.manifest.model_providers:
                    if record.manifest.namespaced_model_provider_tool(provider.id) != projected_tool:
                        continue
                    if stream:
                        raise ExtensionPlatformError(
                            ExtensionDiagnostic.error(
                                DiagnosticCode.WORKER_MODE_INVALID,
                                f"model provider {projected_tool!r} runs in a "
                                "worker; streaming is unavailable (single-frame RPC)",
                                extension_id=eid,
                            )
                        )
                    try:
                        return record.worker.call(
                            projected_tool, {"request": dict(request or {})},
                            timeout=record.manifest.execution.call_timeout_s
                            if record.manifest.execution else 30.0,
                        )
                    except ExtensionPlatformError as exc:
                        if exc.diagnostic.code in (
                            DiagnosticCode.WORKER_CRASHED,
                            DiagnosticCode.WORKER_CALL_TIMEOUT,
                        ):
                            self._on_worker_death(record, exc.diagnostic)
                        raise
            ctx = record.context
            if ctx is None:
                continue
            specs = ctx.model_provider_specs()
            for provider_id, spec in specs.items():
                if record.manifest.namespaced_model_provider_tool(provider_id) != projected_tool:
                    continue
                if stream:
                    if record.worker is not None:
                        raise ExtensionPlatformError(
                            ExtensionDiagnostic.error(
                                DiagnosticCode.WORKER_MODE_INVALID,
                                f"model provider {projected_tool!r} runs in a "
                                "worker; streaming is unavailable (single-frame RPC)",
                                extension_id=eid,
                            )
                        )
                    return spec.invoke_fn(dict(request or {}), ctx)
                from .sdk.model import aggregate_stream_events

                return aggregate_stream_events(
                    spec.invoke_fn(dict(request or {}), ctx)
                )
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.DECLARED_BUT_UNREGISTERED,
                f"no active model provider projects {projected_tool!r}",
            )
        )

    def model_provider_inventory(self) -> list[dict[str, Any]]:
        """声明级清单（status/CLI 消费；派生自 manifest，无第二事实源）。"""
        inventory: list[dict[str, Any]] = []
        for eid in sorted(self._records):
            record = self._records[eid]
            for provider in record.manifest.model_providers:
                projected = record.manifest.namespaced_model_provider_tool(provider.id)
                inventory.append(
                    {
                        "extension_id": eid,
                        "provider_id": provider.id,
                        "tool": projected,
                        "capabilities": sorted(provider.capabilities),
                        "credentials_ref": provider.credentials_ref,
                        "state": record.state.value,
                        "registered": self._tool_registry.has(projected),
                        "execution": (
                            record.manifest.execution.mode
                            if record.manifest.execution
                            else "in_process"
                        ),
                    }
                )
        return inventory

    def _make_broker_handler(self, extension_id: str) -> Any:
        """为一次 worker 激活构造 broker 分派器（默认 deny；审计入环）。"""
        from .broker import BrokerAuditLog, CapabilityBroker

        audit = self._broker_audit.setdefault(extension_id, BrokerAuditLog())
        broker = CapabilityBroker(
            extension_id=extension_id,
            grants=grants_for(extension_id, self._policy.grants),
            network_allow=self._policy.network_allow.get(extension_id, frozenset()),
            secrets=self._policy.secrets.get(extension_id, {}),
            artifact_roots=self._policy.artifact_roots,
            audit=audit,
        )
        return broker.handle

    def broker_audit(self, extension_id: str) -> list[dict[str, Any]]:
        audit = self._broker_audit.get(extension_id)
        if audit is None:
            return []
        return audit.snapshot()

    def _make_worker_proxy(
        self, record: ExtensionRecord, worker: Any, projected: str, call_timeout_s: float
    ) -> Any:
        """生成宿主侧工具代理：转发到 worker，崩溃/超时触发隔离语义。"""
        host = self

        def _worker_proxy(**kwargs: Any) -> Any:
            try:
                return worker.call(projected, kwargs, timeout=call_timeout_s)
            except ExtensionPlatformError as exc:
                if exc.diagnostic.code in (
                    DiagnosticCode.WORKER_CRASHED,
                    DiagnosticCode.WORKER_CALL_TIMEOUT,
                ):
                    host._on_worker_death(record, exc.diagnostic)
                raise

        _worker_proxy.__name__ = projected
        _worker_proxy.__qualname__ = projected
        return _worker_proxy

    def _on_worker_death(
        self, record: ExtensionRecord, diagnostic: ExtensionDiagnostic
    ) -> None:
        """worker 崩溃/超时：回滚投影 → COMPATIBLE（可重新激活）；
        连续崩溃达到上限 → QUARANTINED（运维介入）。"""
        if record.worker is not None:
            record.worker.kill()
            record.worker = None
        record.worker_crash_count += 1
        logger.warning(
            "extension %s worker died: %s (crashes=%d/%d)",
            record.extension_id,
            diagnostic.message,
            record.worker_crash_count,
            self._policy.max_worker_crashes,
        )
        if record.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED):
            self.deactivate(record.extension_id)
        if record.worker_crash_count >= self._policy.max_worker_crashes:
            record.state = ExtensionState.QUARANTINED
            record.diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.WORKER_RESTART_QUARANTINED,
                    f"worker crashed {record.worker_crash_count} times; quarantined "
                    "(re-discover or reset the extension to retry)",
                    extension_id=record.extension_id,
                )
            )

    # ── disable / enable（ADR-0104 Wave 2：运维开关，非失败态）────────
    def disable(self, extension_id: str) -> list[ExtensionDiagnostic]:
        """运维显式停用：active 的先停用回滚，之后拒绝再激活。"""
        record = self._records.get(extension_id)
        if record is None:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"unknown extension {extension_id!r}"
                )
            ]
        diagnostics: list[ExtensionDiagnostic] = []
        if record.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED):
            diagnostics.extend(self.deactivate(extension_id))
            # Round-1 MINOR-2：deactivate 被拒（in-flight/依赖者活跃）时
            # 不得带病置 DISABLED（投影仍在，DISABLED 语义失真）。
            if any(d.severity is DiagnosticSeverity.ERROR for d in diagnostics):
                return diagnostics
        if record.state in (ExtensionState.QUARANTINED,):
            return [
                ExtensionDiagnostic.warning(
                    DiagnosticCode.TRUST_BLOCKED,
                    f"extension {extension_id!r} is quarantined; disable is a no-op",
                    extension_id=extension_id,
                )
            ]
        record.state = ExtensionState.DISABLED
        return diagnostics

    def enable(self, extension_id: str) -> list[ExtensionDiagnostic]:
        """解除 DISABLED，回到 compatible（重新校验）。"""
        record = self._records.get(extension_id)
        if record is None:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"unknown extension {extension_id!r}"
                )
            ]
        if record.state is not ExtensionState.DISABLED:
            return [
                ExtensionDiagnostic.warning(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"extension {extension_id!r} is {record.state.value}, not disabled",
                    extension_id=extension_id,
                )
            ]
        record.state = ExtensionState.DISCOVERED
        record.diagnostics = []
        return self.validate_extension(extension_id)

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
        # Round-1 审计 M2：先于回滚检查反向依赖——否则依赖者的工具/算法
        # 会引用已被回滚的条目（悬空引用零诊断）。
        active_dependents = sorted(
            eid for eid, rec in self._records.items()
            if rec.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED)
            and extension_id in rec.satisfied_dependencies
        )
        if active_dependents:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.DEPENDENT_ACTIVE,
                    f"cannot deactivate {extension_id!r}: active dependents "
                    f"{active_dependents} (deactivate them first)",
                    extension_id=extension_id,
                )
            ]
        # V2：worker 模式 —— 调用进行中拒绝停用（in-flight 语义），否则
        # 优雅关停 worker 进程（投影回滚仍在下方台账路径执行）。
        if record.worker is not None:
            if record.worker.in_flight:
                return [
                    ExtensionDiagnostic.error(
                        DiagnosticCode.OPERATION_IN_FLIGHT,
                        f"cannot deactivate {extension_id!r}: a worker call is "
                        "in flight (retry after it completes)",
                        extension_id=extension_id,
                    )
                ]
            record.worker.shutdown()
            record.worker = None
            # Round-1 MINOR-1：优雅停用清零崩溃计数（「连续」= 跨越一次
            # 干净关停才中断；崩溃路径 worker 已死、不清零，quarantine 可达）。
            record.worker_crash_count = 0
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
        self._notify_projection_change(extension_id, "deactivate")
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
        if record.worker is not None:
            # 双保险：deactivate 正常路径已关停；异常路径兜底 kill。
            record.worker.kill()
            record.worker = None
        self._purge_modules(record)
        record.state = ExtensionState.DISCOVERED
        return []

    def reload(
        self,
        extension_id: str,
        activate: bool = True,
        allow_downgrade: bool = False,
    ) -> list[ExtensionDiagnostic]:
        record = self._records.get(extension_id)
        if record is None:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"unknown extension {extension_id!r}"
                )
            ]
        diagnostics: list[ExtensionDiagnostic] = []
        if record.state is ExtensionState.DISABLED:
            # Round-1 审计 minor12：disable 后 reload 不得绕过运维开关。
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.EXTENSION_DISABLED,
                    f"extension {extension_id!r} is disabled; enable() before reload()",
                    extension_id=extension_id,
                )
            ]
        was_active = record.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED)
        if was_active:
            deactivate_diags = self.deactivate(extension_id)
            diagnostics.extend(deactivate_diags)
            # Round-2 审计 N-2：deactivate 被拒（如 DEPENDENT_ACTIVE）时
            # 立即中止——继续 reload 会让台账与状态索引失配，产生永久僵尸。
            if any(d.severity is DiagnosticSeverity.ERROR for d in deactivate_diags):
                record.diagnostics = list(record.diagnostics)
                return diagnostics
        diagnostics.extend(self.unload(extension_id))
        manifest, parse_diags = _reread_manifest(record.path)
        if manifest is None:
            record.state = ExtensionState.FAILED
            detail = "; ".join(d.message for d in parse_diags) or "manifest re-read failed"
            record.diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"reload failed: {detail}",
                    extension_id=extension_id,
                )
            )
            return list(record.diagnostics)
        if manifest.id != extension_id:
            # Round-1 审计 M1：reload 拒绝 id 漂移——记录键与 manifest.id
            # 必须一致，否则信任裁决/依赖图/状态索引全部失配。
            record.state = ExtensionState.FAILED
            record.diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"reload refused: manifest id changed {extension_id!r} -> "
                    f"{manifest.id!r} (re-discover instead)",
                    extension_id=extension_id,
                )
            )
            return list(record.diagnostics)
        # V2（Wave 8）：降级闸 —— 磁盘版本低于当前记录版本时拒绝，
        # 除非 allow_downgrade=True（回滚 = 运维恢复旧 pack + 显式降级）。
        new_v = parse_version(manifest.version)
        cur_v = parse_version(record.manifest.version)
        if new_v is not None and cur_v is not None and new_v < cur_v and not allow_downgrade:
            record.state = ExtensionState.FAILED
            record.diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"reload refused: version downgrade {record.manifest.version!r} -> "
                    f"{manifest.version!r} requires allow_downgrade=True",
                    extension_id=extension_id,
                )
            )
            return diagnostics + list(record.diagnostics)
        # Round-1 审计 M1：reload 必须重跑发现期信任门（blocklist 新增、
        # allowlist 撤销都可能发生在两次操作之间）。
        trust = resolve_trust(
            extension_id,
            allowlist=self._policy.allow,
            blocklist=self._policy.block,
            builtin_ids=self._policy.builtin_ids,
        )
        record.trust = trust
        record.manifest = manifest
        fingerprint, fp_diag = _refingerprint(record)
        if fp_diag is None:
            content_changed = (
                record.fingerprint is not None and fingerprint != record.fingerprint
            )
            if content_changed and record.trust in (
                TrustLevel.TRUSTED_BUILTIN, TrustLevel.TRUSTED_EXTENSION
            ):
                # Round-2 审计 N-3：受信扩展内容变更必须走重新发现（重新
                # 信任裁决），reload 不得把换血后的代码当作原包激活。
                record.state = ExtensionState.FAILED
                record.diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.FINGERPRINT_CHANGED,
                        "trusted extension content changed; re-discover before reload",
                        extension_id=extension_id,
                    )
                )
                return diagnostics + list(record.diagnostics)
            if content_changed:
                diagnostics.append(
                    ExtensionDiagnostic.warning(
                        DiagnosticCode.FINGERPRINT_CHANGED,
                        "extension content changed since last load",
                        extension_id=extension_id,
                    )
                )
            record.fingerprint = fingerprint
        if trust is TrustLevel.BLOCKED:
            record.state = ExtensionState.QUARANTINED
            record.diagnostics = [
                ExtensionDiagnostic.error(
                    DiagnosticCode.TRUST_BLOCKED,
                    "extension is blocked by operator policy (EXTENSIONS_BLOCK)",
                    extension_id=extension_id,
                )
            ]
            return diagnostics + list(record.diagnostics)
        record.state = ExtensionState.DISCOVERED
        record.diagnostics = []
        diagnostics.extend(self.validate_extension(extension_id))
        if activate and record.state is ExtensionState.COMPATIBLE:
            diagnostics.extend(self.activate(extension_id))
        return diagnostics

    def upgrade(
        self, extension_id: str, allow_downgrade: bool = False
    ) -> list[ExtensionDiagnostic]:
        """升级（V2 Wave 8）：磁盘 pack 已被替换为新版本后的安全换血。

        预检（不触碰当前运行状态）：
        - manifest 可重读且 id 不漂移；
        - 版本回归必须显式 ``allow_downgrade=True``（回滚语义）；
        - ``resolver.check_upgrade_conflicts``：任何依赖者的版本约束被
          新版本破坏 → DEPENDENCY_CONFLICT，升级被拒，旧版继续运行。
        通过后委托 reload（deactivate → unload → 重读 → activate）。
        """
        record = self._records.get(extension_id)
        if record is None:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"unknown extension {extension_id!r}"
                )
            ]
        if record.state is ExtensionState.DISABLED:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.EXTENSION_DISABLED,
                    f"extension {extension_id!r} is disabled; enable() before upgrade()",
                    extension_id=extension_id,
                )
            ]
        if record.state is ExtensionState.QUARANTINED:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.TRUST_BLOCKED,
                    f"extension {extension_id!r} is quarantined; re-discover instead",
                    extension_id=extension_id,
                )
            ]
        manifest, parse_diags = _reread_manifest(record.path)
        if manifest is None:
            detail = "; ".join(d.message for d in parse_diags) or "manifest re-read failed"
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"upgrade preflight failed: {detail}",
                    extension_id=extension_id,
                )
            ]
        if manifest.id != extension_id:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"upgrade refused: manifest id changed {extension_id!r} -> "
                    f"{manifest.id!r} (re-discover instead)",
                    extension_id=extension_id,
                )
            ]
        new_v = parse_version(manifest.version)
        cur_v = parse_version(record.manifest.version)
        if new_v is not None and cur_v is not None and new_v < cur_v and not allow_downgrade:
            return [
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"upgrade refused: {manifest.version!r} is older than active "
                    f"{record.manifest.version!r}; use allow_downgrade=True to roll back",
                    extension_id=extension_id,
                )
            ]
        views = {eid: _record_view(r) for eid, r in self._records.items()}
        conflicts = resolver.check_upgrade_conflicts(extension_id, manifest.version, views)
        if conflicts:
            return conflicts
        return self.reload(extension_id, activate=True, allow_downgrade=allow_downgrade)

    # ── 批量激活（topo 序）────────────────────────────────────────────
    def activate_all(self) -> dict[str, list[ExtensionDiagnostic]]:
        results: dict[str, list[ExtensionDiagnostic]] = {}
        # V2 Wave 8：激活序收敛到 resolver（Kahn 拓扑 + id tie-break，
        # 与 resolver.resolve_activation_plan 同一事实源）。
        views = {eid: _record_view(r) for eid, r in self._records.items()}
        eligible = {
            eid for eid, r in self._records.items() if r.state is ExtensionState.COMPATIBLE
        }
        plan = resolver.resolve_activation_plan(views, eligible)
        # 环内/无序成员不在 ordered 中（validate 阶段已诊断），跳过即可。
        for eid in plan.ordered:
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
        if record.worker is not None:
            # V2：worker 模式健康检查走带超时 RPC（无界 sync 健康检查的
            # limitation 在 worker 路径被消除；in-process 语义保持不变）。
            try:
                report = record.worker.health()
            except ExtensionPlatformError as exc:
                report = {"status": "unhealthy", "messages": [exc.diagnostic.message]}
            report["state"] = record.state.value
            return report
        report = self._run_health(record, record.module)
        report["state"] = record.state.value
        return report

    def _run_health(self, record: ExtensionRecord, module: Any) -> dict[str, Any]:
        from .loader import resolve_health_report

        return resolve_health_report(module, record.manifest.diagnostics_entry)

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
                    "execution": (
                        record.manifest.execution.mode if record.manifest.execution else "in_process"
                    ),
                    "worker_pid": record.worker.pid if record.worker is not None else None,
                    "worker_crash_count": record.worker_crash_count,
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
            if record.worker is not None:
                record.worker.kill()
                record.worker = None
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


def _record_view(record: ExtensionRecord) -> "resolver.RecordView":
    """ExtensionRecord → resolver.RecordView（解析层与 host 类型解耦）。"""
    from .manifest import DependencyDeclaration

    def _deps(deps: list[DependencyDeclaration], required: bool) -> tuple:
        return tuple(
            resolver.DepView(
                id=d.id,
                # optional_dependencies 节整体按 optional 语义处理（与
                # host._check_dependencies 的既有判定一致）。
                required=d.required and required,
                version_constraint=d.version,
                feature_flag=d.feature_flag,
            )
            for d in deps
        )

    return resolver.RecordView(
        id=record.extension_id,
        version=record.manifest.version,
        state=record.state.value,
        deps=_deps(record.manifest.dependencies, True)
        + _deps(record.manifest.optional_dependencies, False),
    )


def _refingerprint(record: ExtensionRecord) -> tuple[Optional[str], Optional[ExtensionDiagnostic]]:
    from .discovery import compute_fingerprint

    return compute_fingerprint(record.path)


def _reread_manifest(
    path: Path,
) -> tuple[Optional[GisExtensionManifest], list[ExtensionDiagnostic]]:
    from .discovery import MANIFEST_FILENAME, _parse_manifest_file

    manifest, diags, _ = _parse_manifest_file(path / MANIFEST_FILENAME)
    if manifest is None:
        return None, list(diags)
    return manifest, []
