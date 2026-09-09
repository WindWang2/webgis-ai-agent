"""认证 harness（ADR-0105 V2 / Wave 11）。

对单个扩展执行确定性检查套件，产出结构化认证报告（CLI ``certify`` 消费）：

- 契约：manifest 有效性、api 兼容、权限词表一致性；
- 供应链：签名状态、SBOM secret 扫描；
- 依赖：版本约束可满足；
- 行为：lifecycle smoke —— 真实激活 → 健康检查 → 停用（in-process 与
  worker 各按其执行模型；worker 模式即真实子进程 smoke）。

报告确定性：检查项固定顺序、无时间戳、无随机源。``certified`` = 全部
检查无 fail（warning 不阻断，但如实呈现）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .diagnostics import DiagnosticCode, has_errors
from .host import ExtensionHost, ExtensionState
from .signing import verify_pack_signature


def certify_extension(host: ExtensionHost, extension_id: str) -> dict[str, Any]:
    """对已发现扩展执行认证套件；未知 id 返回单检查失败报告。"""
    record = host.get_record(extension_id)
    if record is None:
        return {
            "extension_id": extension_id,
            "certified": False,
            "checks": [
                {
                    "check": "discovered",
                    "status": "fail",
                    "detail": f"unknown extension {extension_id!r}",
                }
            ],
        }
    checks: list[dict[str, Any]] = []

    def add(check: str, ok: bool, detail: str, *, warn_only: bool = False) -> None:
        checks.append(
            {
                "check": check,
                "status": ("warn" if warn_only else ("pass" if ok else "fail")),
                "detail": detail,
            }
        )

    # 1) 发现期契约诊断（manifest/版本/权限/依赖/入口/签名一次看全）。
    baseline_errors = [d for d in record.diagnostics if d.severity.value == "error"]
    add(
        "manifest_contract",
        not baseline_errors,
        "; ".join(d.message for d in baseline_errors) or "no error diagnostics",
    )
    # 2) api 兼容（显式复核，独立于发现期诊断的可读性）。
    from .api_version import check_extension_api_compatibility

    api = check_extension_api_compatibility(record.manifest.api_version)
    add("api_compatible", api.compatible, api.reason or f"api_version {record.manifest.api_version}")
    # 3) 依赖版本约束。
    constraint_errors = [
        d
        for d in record.diagnostics
        if d.code
        in (DiagnosticCode.DEPENDENCY_MISSING, DiagnosticCode.DEPENDENCY_CONSTRAINT_INVALID)
        and d.severity.value == "error"
    ]
    add(
        "dependency_constraints",
        not constraint_errors,
        "; ".join(d.message for d in constraint_errors) or "all constraints satisfied",
    )
    # 4) 信任 + 签名状态（信息性呈现；篡改/无效签名在发现期已隔离）。
    #    V3：trust store 启用时含 revocation/retired 判定（吊销 = fail）。
    signature = verify_pack_signature(
        record.path,
        host._policy.trusted_publishers,
        trust_store=host._policy.trust_store,
        package_id=record.manifest.id,
        version=record.manifest.version,
    )
    add(
        "signature",
        signature.status not in ("invalid", "tampered", "revoked"),
        f"{signature.status}"
        + (f" (publisher={signature.publisher})" if signature.publisher else "")
        + (f": {signature.detail}" if signature.detail else ""),
    )
    # 5) SBOM secret 扫描。
    from .discovery import compute_fingerprint
    from .sbom import build_sbom

    fingerprint, fp_diag = compute_fingerprint(record.path)
    if fingerprint is None:
        add("sbom_secret_scan", False, fp_diag.message if fp_diag else "fingerprint unavailable")
    else:
        sbom = build_sbom(record.path, record.manifest, fingerprint)
        scan = sbom["secret_scan"]
        add(
            "sbom_secret_scan",
            scan["clean"],
            "clean" if scan["clean"] else f"findings: {scan['findings']}",
        )
    # V3：package_layout —— 包内条目形状（symlink/hardlink/设备/越界路径
    # 在发现面之外的显式复核；stat 元数据断言，Mi-5）。
    add("package_layout", *_check_package_layout(record.path))
    # V3：resource_budget_declared —— 预算字段在平台硬上界内（manifest
    # 解析已强制；这里复核"已声明且有限"，作为认证证据留痕）。
    add("resource_budget_declared", *_check_resource_budget(record.manifest))
    # V3：protocol_compat —— worker + streaming 声明但线协议能力缺失 → fail
    # （manifest 层已按 api 门控；本检查复核协议面声明一致性）。
    add("protocol_compat", *_check_protocol_compat(record.manifest))
    # V3：provider_conformance —— worker 数据 provider 的 7 方法形状 smoke
    # （仅记录申报的方法面与 mixin；真实往返由激活期 RPC 完成）。
    add("provider_conformance", *_check_provider_conformance(record.manifest))
    # 6) 执行模式描述（worker 结构性约束在 manifest 层已 fail closed）。
    mode = record.manifest.execution.mode if record.manifest.execution else "in_process"
    add("execution_mode", True, mode)
    # 7) lifecycle smoke：真实激活 → 健康 → 停用（ACTIVE 记录只做健康，
    #    不扰动运维状态）。
    if record.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED):
        report = host.health(extension_id)
        add("lifecycle_smoke", report.get("status") != "unhealthy", f"health={report.get('status')}")
    elif record.state is ExtensionState.COMPATIBLE:
        activate_diags = host.activate(extension_id)
        after_state = host.get_record(extension_id)
        activated = (
            after_state is not None
            and after_state.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED)
        )
        health_status = host.health(extension_id).get("status") if activated else "skipped"
        add(
            "lifecycle_smoke",
            activated and health_status != "unhealthy",
            f"activate={'ok' if activated else 'failed'}"
            f" ({'; '.join(d.message for d in activate_diags if d.severity.value == 'error')}); "
            f"health={health_status}",
        )
        deactivate_diags = host.deactivate(extension_id)
        add(
            "deactivate_clean",
            not has_errors(deactivate_diags),
            "; ".join(d.message for d in deactivate_diags) or "projections rolled back",
        )
    else:
        add(
            "lifecycle_smoke",
            False,
            f"extension is {record.state.value}; enable() or re-discover first",
        )
    certified = all(c["status"] != "fail" for c in checks)
    return {
        "extension_id": extension_id,
        "certified": certified,
        "trust": record.trust.value,
        "execution_mode": mode,
        "checks": checks,
    }


def _check_package_layout(pack_dir) -> tuple[bool, str]:
    """包目录条目形状断言（发现期指纹之外的显式安全复核）。"""
    import os

    symlinks = []
    suspicious = []
    for root, dirs, names in os.walk(pack_dir):
        # 指向目录的 symlink 出现在 dirs（os.walk 不跟进也不入 names）——
        # 必须显式检出，否则「symlink escape 到包外目录」漏检。
        for d in list(dirs):
            if (Path(root) / d).is_symlink():
                symlinks.append(str((Path(root) / d).relative_to(pack_dir)))
        dirs[:] = [d for d in dirs if d != "__pycache__" and not (Path(root) / d).is_symlink()]
        for name in names:
            p = Path(root) / name
            if p.is_symlink():
                symlinks.append(str(p.relative_to(pack_dir)))
                continue
            if not p.is_file():
                suspicious.append(str(p.relative_to(pack_dir)))
    if symlinks:
        return False, f"package contains symlinks {symlinks} (rejected)"
    if suspicious:
        return False, f"package contains non-regular entries {suspicious}"
    return True, "regular files only, no symlinks"


def _check_resource_budget(manifest) -> tuple[bool, str]:
    """执行预算已声明且在平台上界内（解析期已强制；此处认证留痕）。"""
    execution = manifest.execution
    if execution is None:
        return True, "in_process (no worker budget declared)"
    from .manifest import (
        MAX_CALL_TIMEOUT_S,
        MAX_STARTUP_TIMEOUT_S,
        MAX_WORKER_CPU_SECONDS,
        MAX_WORKER_MEMORY_MB,
        MAX_WORKER_OUTPUT_BYTES,
    )

    within = (
        execution.startup_timeout_s <= MAX_STARTUP_TIMEOUT_S
        and execution.call_timeout_s <= MAX_CALL_TIMEOUT_S
        and execution.max_memory_mb <= MAX_WORKER_MEMORY_MB
        and execution.max_cpu_seconds <= MAX_WORKER_CPU_SECONDS
        and execution.max_output_bytes <= MAX_WORKER_OUTPUT_BYTES
        and execution.max_stream_events >= 1
        and execution.stream_window >= 1
    )
    return (
        bool(within),
        (
            f"budgets within platform caps (mem={execution.max_memory_mb}MiB, "
            f"call={execution.call_timeout_s}s, out={execution.max_output_bytes}B, "
            f"events={execution.max_stream_events})"
        )
        if within
        else "execution budget exceeds platform caps",
    )


def _check_protocol_compat(manifest) -> tuple[bool, str]:
    """worker streaming 声明与协议能力的兼容复核（离线一致性检查）。"""
    if not manifest.is_worker_mode:
        return True, "in_process"
    from .api_version import meets_api_floor

    streaming = [m.id for m in manifest.model_providers if "streaming" in m.capabilities]
    if streaming and not meets_api_floor(manifest.api_version, (1, 2, 0)):
        return False, (
            f"worker model providers {streaming} declare streaming but api_version "
            f"{manifest.api_version!r} predates protocol v3 support"
        )
    if streaming:
        return True, f"streaming providers {streaming} under protocol v3 (api >= 1.2.0)"
    return True, "no streaming declarations"


def _check_provider_conformance(manifest) -> tuple[bool, str]:
    """worker 数据 provider 申报形状检查（真实往返由激活期 RPC 覆盖）。"""
    if not manifest.data_providers:
        return True, "no data providers"
    if not manifest.is_worker_mode:
        return True, f"{len(manifest.data_providers)} in_process provider(s)"
    return (
        True,
        f"{len(manifest.data_providers)} worker provider(s); 7-method RPC proxy "
        "projected at activation (integration verified there)",
    )
