#!/usr/bin/env python
"""工具描述符富化覆盖率闸（ADR-0103）。

语义：富化只许前进，不许回退。collect() 对 live tool registry 做只读
投影，统计非 PLANNED 可执行工具的字段富化覆盖率；gate() 消费报告，
0 = 通过、1 = 存在未达阈值的字段。阈值为基线棘轮（整数百分比下限），
任何批量回退即红；上调需后续 ADR 显式记录。

tests/unit/test_descriptor_coverage_gate.py import 本模块（GATE_THRESHOLDS /
collect / gate），因此本脚本必须入库（.gitignore 白名单）。

用法：
    python scripts/check_tool_descriptor_coverage.py     # 打印报告，回退即 exit 1
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GATE_FIELDS: tuple[str, ...] = (
    "side_effect",
    "tags",
    "latency_class",
    "memory_class",
    "capabilities",
)

# 2026-09 基线棘轮：279 个可执行工具实测 side_effect/tags=83%、
# capabilities=60%、latency/memory=100%。阈值钉在基线（100% 类留 5%
# 新工具余量），任何批量回退即红；上调需后续 ADR。
GATE_THRESHOLDS: Dict[str, int] = {
    "side_effect": 83,
    "tags": 83,
    "latency_class": 95,
    "memory_class": 95,
    "capabilities": 60,
}


def _build_registry() -> Any:
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    init_tools(registry)
    return registry


def collect() -> Dict[str, Any]:
    """返回富化覆盖率报告（gate 消费的形态）。"""
    registry = _build_registry()
    descriptors = registry.descriptors()
    executable = [d for d in descriptors.values() if d.status.value != "planned"]
    fields: Dict[str, Any] = {}
    all_pass = True
    for f in GATE_FIELDS:
        covered = 0
        missing: List[str] = []
        for d in executable:
            value = getattr(d, f, None)
            if f == "side_effect":
                ok = value is not None and value.value != "unclassified"
            else:
                ok = bool(value)
            if ok:
                covered += 1
            else:
                missing.append(d.name)
        total = len(executable)
        coverage = round(covered * 100 / total) if total else 0
        threshold = GATE_THRESHOLDS[f]
        passed = coverage >= threshold
        all_pass = all_pass and passed
        fields[f] = {
            "coverage": coverage,
            "threshold": threshold,
            "pass": passed,
            "missing_count": len(missing),
            "missing": sorted(missing)[:20],
        }
    return {
        "total": len(executable),
        "fields": fields,
        "pass": all_pass,
    }


def gate(report: Dict[str, Any]) -> int:
    """0 = 通过；1 = 存在未达阈值的富化字段（富化回退即红）。"""
    if not report.get("fields"):
        return 1
    return 0 if report.get("pass") else 1


def main() -> int:
    report = collect()
    print(f"executable tools: {report['total']}")
    for field_name, fd in report["fields"].items():
        mark = "PASS" if fd["pass"] else "FAIL"
        print(
            f"  {field_name:<14} {fd['coverage']:>3}%  "
            f"(threshold {fd['threshold']}%, missing {fd['missing_count']})  {mark}"
        )
    return gate(report)


if __name__ == "__main__":
    sys.exit(main())
