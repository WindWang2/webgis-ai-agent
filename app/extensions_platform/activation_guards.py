"""激活前置守卫（deep-review CP-02 / CP-10），从 host.py 抽出以控制其体量。

- :func:`isolation_policy_error`：隔离由**运维策略**决定，而非 manifest 作者。
  in_process 代码与宿主同进程（os.environ / settings / DB 全可达）。
- :func:`content_recheck`：内容复核必须在 ``exec_module`` **之前**（此前在
  ``activate()`` 之后，被篡改的代码早已执行，fail closed 形同虚设）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .diagnostics import DiagnosticCode, ExtensionDiagnostic
from .trust import TrustLevel

# "none"（直构 HostPolicy 的本地开发语义）| "untrusted"（local_untrusted 必须
# worker 模式）| "all"（除 trusted_builtin/core 外一律 worker）。
REQUIRE_WORKER_POLICIES = frozenset({"none", "untrusted", "all"})


def worker_required(policy: str, trust: TrustLevel) -> bool:
    """运维策略是否要求该信任级别的扩展以 worker 模式执行。"""
    if policy == "all":
        return trust not in (TrustLevel.TRUSTED_BUILTIN, TrustLevel.CORE)
    if policy == "untrusted":
        return trust is TrustLevel.LOCAL_UNTRUSTED
    return False


def isolation_policy_error(
    policy: str, trust: TrustLevel, is_worker_mode: bool, extension_id: str
) -> Optional[ExtensionDiagnostic]:
    if is_worker_mode or not worker_required(policy, trust):
        return None
    return ExtensionDiagnostic.error(
        DiagnosticCode.ISOLATION_UNAVAILABLE,
        f"operator policy EXTENSIONS_REQUIRE_WORKER_FOR={policy!r} refuses "
        f"in_process execution for {trust.value} extensions; the pack must "
        "declare execution.mode='worker' (or the operator must allowlist it)",
        extension_id=extension_id,
    )


def content_recheck(
    path: Path, discovered_fingerprint: Optional[str], trust: TrustLevel, extension_id: str
) -> tuple[Optional[str], Optional[ExtensionDiagnostic], Optional[ExtensionDiagnostic]]:
    """返回 ``(当前指纹, error, warning)``。

    指纹不可计算（symlink/超界）→ error（所有信任级别）；受信扩展内容变化
    → error；local_untrusted 内容变化 → warning（以新指纹/新模块命名空间加载）。
    """
    from .discovery import compute_fingerprint

    fingerprint, diag = compute_fingerprint(path)
    if diag is not None:
        return None, diag, None
    if fingerprint == discovered_fingerprint:
        return fingerprint, None, None
    if trust in (TrustLevel.TRUSTED_BUILTIN, TrustLevel.TRUSTED_EXTENSION):
        return fingerprint, ExtensionDiagnostic.error(
            DiagnosticCode.FINGERPRINT_CHANGED,
            "trusted extension content changed since discovery; "
            "re-discover before activation",
            extension_id=extension_id,
        ), None
    return fingerprint, None, ExtensionDiagnostic.warning(
        DiagnosticCode.FINGERPRINT_CHANGED,
        "extension content changed since discovery",
        extension_id=extension_id,
    )
