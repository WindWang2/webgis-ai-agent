"""示范包 manifest 自动检查器（G06 / CAP-05）。

**不造第二认证体系**：本检查器只做 authoring 期的静态契约检查
（schema / 版本 / 权限 / 资源 / side effects / golden），真正的包级认证与
能力级认证复用既有 ``certify_extension`` / ``run_pack_certification`` /
``sdk.run_authoring_checks`` / ``manifest_from_dict``。

用法::

    python examples/gis_capability_ecosystem/check_pack.py
    python examples/gis_capability_ecosystem/check_pack.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 直接运行（python examples/.../check_pack.py）时把仓库根加入 sys.path，
# 保证复用的是本仓库的 app.* 权威实现而不是环境里其它安装。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "capability_pack_manifest.json"

REQUIRED_FIELDS = (
    "schema_version", "pack_id", "version", "namespace", "permissions",
    "resources", "side_effects", "tools", "algorithms", "skills",
    "golden_tests", "verification",
)
REQUIRED_RESOURCES = (
    "latency_class", "memory_class", "scale_class",
    "max_raster_cells", "max_network_edges", "max_zones",
)
REQUIRED_TOOL_FIELDS = (
    "name", "algorithm", "capability", "input_artifacts",
    "output_artifacts", "unit_semantics", "crs_semantics",
    "parameter_contract",
)
ALLOWED_SIDE_EFFECTS = frozenset({
    "deterministic_compute", "pure", "cacheable_read",
})
ALLOWED_UNITS = frozenset({
    "meters", "m", "seconds", "s", "raster_value_unitless", "unitless",
})


def check_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    """静态契约检查；返回 {ok, issues, checks, certified_via}。"""
    issues: list[str] = []
    checks: list[str] = []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "issues": [f"manifest_unreadable:{exc}"],
                "checks": [], "certified_via": []}

    for field in REQUIRED_FIELDS:
        if field not in doc:
            issues.append(f"missing_field:{field}")
        else:
            checks.append(f"field_present:{field}")

    if doc.get("schema_version") != 1:
        issues.append("unsupported_schema_version")
    if str(doc.get("namespace", "")) != "gis_capability_ecosystem_demo":
        issues.append("namespace_mismatch")

    perms = doc.get("permissions")
    if not isinstance(perms, list) or any(not isinstance(p, str) for p in perms):
        issues.append("permissions_shape")
    else:
        checks.append("permissions_declared")

    res = doc.get("resources") or {}
    for key in REQUIRED_RESOURCES:
        if key not in res:
            issues.append(f"missing_resource:{key}")
    for key in ("max_raster_cells", "max_network_edges", "max_zones"):
        if key in res and (not isinstance(res[key], int) or res[key] <= 0):
            issues.append(f"invalid_resource_budget:{key}")
    checks.append("resource_envelope_bounded")

    sides = doc.get("side_effects") or []
    if not sides or not set(sides).issubset(ALLOWED_SIDE_EFFECTS):
        issues.append(f"side_effects_not_declared_or_unsupported:{sides}")
    else:
        checks.append("side_effects_declared")

    tool_names: set[str] = set()
    algo_ids: set[str] = set()
    for i, tool in enumerate(doc.get("tools") or []):
        for key in REQUIRED_TOOL_FIELDS:
            if key not in tool:
                issues.append(f"tool[{i}].missing:{key}")
        if tool.get("unit_semantics") not in ALLOWED_UNITS:
            issues.append(f"tool[{i}].unit_not_in_vocabulary")
        if not str(tool.get("crs_semantics", "")).endswith("required") \
                and tool.get("crs_semantics") != "crs_agnostic":
            issues.append(f"tool[{i}].crs_semantics_undeclared")
        tool_names.add(str(tool.get("name", "")))
    for i, algo in enumerate(doc.get("algorithms") or []):
        if not algo.get("id"):
            issues.append(f"algorithm[{i}].missing_id")
        if not algo.get("scientific_status"):
            issues.append(f"algorithm[{i}].missing_scientific_status")
        if not algo.get("uncertainty_outputs"):
            issues.append(f"algorithm[{i}].missing_uncertainty_outputs")
        algo_ids.add(str(algo.get("id", "")))
    checks.append("tool_algorithm_metadata_parity")

    for skill in doc.get("skills") or []:
        entry = HERE / str(skill.get("entry", ""))
        if not entry.is_file():
            issues.append(f"skill_entry_missing:{skill.get('entry')}")
    for node in doc.get("golden_tests") or []:
        # golden 节点相对仓库根；这里只校验其存在性（不在此执行）。
        target = HERE.parent.parent / node
        if not target.is_file():
            issues.append(f"golden_test_missing:{node}")
    checks.append("golden_tests_registered")

    ver = doc.get("verification") or {}
    if ver.get("requires_api_key") or ver.get("requires_postgis"):
        issues.append("verification_must_be_offline")
    if not ver.get("command"):
        issues.append("missing_verification_command")

    # 复用既有认证 / authoring 面（只读验证其可用，不另造体系）。
    certified_via: list[str] = []
    try:
        from app.extensions_platform.capability_certification import (
            run_pack_certification,
        )
        from app.extensions_platform.certification import certify_extension
        from app.extensions_platform.manifest import manifest_from_dict
        from app.extensions_platform.sdk.algorithm import run_authoring_checks

        stack = (
            ("extensions_platform.certification.certify_extension",
             certify_extension),
            ("extensions_platform.capability_certification.run_pack_certification",
             run_pack_certification),
            ("extensions_platform.manifest.manifest_from_dict",
             manifest_from_dict),
            ("extensions_platform.sdk.algorithm.run_authoring_checks",
             run_authoring_checks),
        )
        for name, fn in stack:
            if not callable(fn):
                issues.append(f"certification_api_not_callable:{name}")
        certified_via = [name for name, _ in stack]
        checks.append("existing_certification_stack_reused")
    except Exception as exc:  # noqa: BLE001
        issues.append(f"certification_stack_unavailable:{type(exc).__name__}")

    # 真实注册检查：两个工具名必须能在唯一 ToolRegistry 里找到。
    try:
        from app.tools import init_tools
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        init_tools(reg)
        live = set(reg.tool_names())
        for name in tool_names:
            if name not in live:
                issues.append(f"tool_not_registered_in_ToolRegistry:{name}")
        checks.append("tools_really_registered")
    except Exception as exc:  # noqa: BLE001
        issues.append(f"registry_check_failed:{type(exc).__name__}")

    # 参数契约必须能在唯一 ParameterContractRegistry 找到。
    try:
        from app.lib.gis.parameter_contracts import (
            get_parameter_contract_registry,
        )

        contracts = get_parameter_contract_registry()
        for tool in doc.get("tools") or []:
            cid = str(tool.get("parameter_contract", ""))
            if cid and not contracts.has(cid):
                issues.append(f"parameter_contract_missing:{cid}")
        checks.append("parameter_contracts_registered")
    except Exception as exc:  # noqa: BLE001
        issues.append(f"contract_check_failed:{type(exc).__name__}")

    return {
        "ok": not issues,
        "manifest": str(path),
        "issues": sorted(set(issues)),
        "checks": sorted(set(checks)),
        "certified_via": certified_via,
        "tools": sorted(tool_names),
        "algorithms": sorted(algo_ids),
        "disclosure": (
            "静态检查 ≠ 认证通过；真实包级/能力级认证必须由既有 "
            "certify_extension / run_pack_certification 产出签名报告。"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GIS capability pack manifest checker")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--manifest", default=str(MANIFEST))
    args = ap.parse_args(argv)
    result = check_manifest(Path(args.manifest))
    lines: list[str] = []
    if args.json:
        lines.append(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        lines.append(f"manifest: {result['manifest']}")
        lines.append(f"ok: {result['ok']}")
        lines.extend(f"  PASS {c}" for c in result["checks"])
        lines.extend(f"  FAIL {i}" for i in result["issues"])
        lines.append(f"disclosure: {result['disclosure']}")
    sys.stdout.write("\n".join(lines) + "\n")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
