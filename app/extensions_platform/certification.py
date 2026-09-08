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
    signature = verify_pack_signature(record.path, host._policy.trusted_publishers)
    add(
        "signature",
        signature.status not in ("invalid", "tampered"),
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
            f"extension is {record.state.value}; re-discover before certification",
        )
    certified = all(c["status"] != "fail" for c in checks)
    return {
        "extension_id": extension_id,
        "certified": certified,
        "trust": record.trust.value,
        "execution_mode": mode,
        "checks": checks,
    }
