#!/usr/bin/env python
"""工具描述符富化覆盖率闸（ADR-0103）。

ADR-0104 起实现收敛到 ``app/lib/quality/manifest.py``（QualityManifest
编译器的闸子集）；本脚本只是薄壳，保持历史 CLI / 测试 import 面：
``GATE_THRESHOLDS`` / ``collect()`` / ``gate()``。

用法：
    python scripts/check_tool_descriptor_coverage.py     # 打印报告，回退即 exit 1
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.lib.quality.manifest import GATE_THRESHOLDS, collect, gate  # noqa: E402

__all__ = ["GATE_THRESHOLDS", "collect", "gate"]


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
