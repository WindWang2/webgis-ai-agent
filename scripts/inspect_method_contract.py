#!/usr/bin/env python
"""inspect_method_contract —— MethodUseContract 只读检视/契约校验 CLI（G02）。

只读工具：不写任何文件、不改注册表。三种模式：

    python scripts/inspect_method_contract.py --algorithm interpolation.cokriging
    python scripts/inspect_method_contract.py --all --check
    python scripts/inspect_method_contract.py --algorithm stats.moran --json

退出码：0 = 无 finding 或仅展示；1 = --check 模式下存在结构 finding
（悬空引用/词表外单位/无能力绑定）—— 机读可判，供审计脚本/CI-local 消费。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.lib.gis.method_use_contract import MethodUseContract

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.lib.gis.method_use_contract import (  # noqa: E402
    build_method_use_contract,
    iter_method_use_contracts,
)


def _format_contract(c: MethodUseContract) -> str:
    lines = [
        f"algorithm: {c.algorithm_id}  (schema v{c.schema_version})",
        f"  capabilities:        {', '.join(c.capabilities) or '(none)'}",
        f"  family/status:       {c.algorithm_family} / {c.runtime_status}"
        f" / scientific={c.scientific_status or '-'}",
        f"  crs_class:           {c.crs_class or '(unrestricted)'}",
        f"  min_features:        {c.min_features if c.min_features is not None else '-'}",
        f"  preconditions:       {len(c.scientific_preconditions)}",
    ]
    for pid in c.scientific_preconditions:
        desc = c.precondition_descriptions.get(pid, "")
        lines.append(f"    - {pid}" + (f"  # {desc}" if desc else ""))
    if c.parameters:
        lines.append(f"  parameters ({c.parameter_contract_id or 'inline'}"
                     f" v{c.parameter_contract_version}):")
        for p in c.parameters:
            unit = f" [{p.unit}]" if p.unit else ""
            bounds = ""
            if p.minimum is not None or p.maximum is not None:
                bounds = f" ∈[{p.minimum}, {p.maximum}]"
            lines.append(
                f"    - {p.name}: {p.type}{unit}{bounds}"
                f"{' required' if p.required else ''}")
    else:
        lines.append("  parameters:          (no parameter contract)")
    if c.fallback_algorithms:
        fb = ", ".join(
            f"{f}({c.fallback_semantics.get(f, 'unclassified')})"
            for f in c.fallback_algorithms)
        lines.append(f"  fallback:            {fb}")
    if c.method_references:
        lines.append(f"  method_references:   {', '.join(c.method_references)}")
    if c.findings:
        lines.append("  findings:")
        for f in c.findings:
            lines.append(f"    ! {f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MethodUseContract read-only inspection (G02)")
    parser.add_argument("--algorithm", default="",
                        help="algorithm id to inspect (e.g. interpolation.cokriging)")
    parser.add_argument("--all", action="store_true",
                        help="iterate the whole registry")
    parser.add_argument("--json", action="store_true",
                        help="JSON output (machine-readable)")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if any structural finding exists")
    args = parser.parse_args(argv)

    if not args.algorithm and not args.all:
        parser.error("specify --algorithm <id> or --all")

    if args.all:
        contracts = list(iter_method_use_contracts())
    else:
        contracts = [build_method_use_contract(args.algorithm)]

    findings_total = sum(len(c.findings) for c in contracts)
    if args.json:
        payload = {
            "schema_version": contracts[0].schema_version if contracts else "",
            "count": len(contracts),
            "findings_total": findings_total,
            "contracts": [c.model_dump() for c in contracts],
        }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        for c in contracts:
            print(_format_contract(c))
            print()
        print(f"contracts: {len(contracts)}  findings: {findings_total}")

    if args.check and findings_total:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
