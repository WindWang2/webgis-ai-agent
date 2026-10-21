#!/usr/bin/env python
"""工具能力 8 面契约对账 CLI（G03，只读离线）。

对 ToolRegistry 的每个工具（或 ``--tool`` 指定单个）生成
``ToolConformanceReport``：registration / search / planner / pi_native /
proxy / dispatch / result / science 八个消费面的 pass/warning/fail/
not_evaluated 裁决 + 稳定 reason code + 有界证据。

用法：
    python scripts/check_tool_capability_conformance.py              # 全量人读摘要
    python scripts/check_tool_capability_conformance.py --tool buffer_analysis
    python scripts/check_tool_capability_conformance.py --json       # 机器可读
    python scripts/check_tool_capability_conformance.py --fail-on warning

退出码：0 = 无 fail（默认口径）；1 = 存在 fail（或 ``--fail-on warning``
时存在 fail/warning）；2 = 用法/环境错误。零网络、零写入。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.lib.gis.tool_capability_conformance import (  # noqa: E402
    STATUS_FAIL,
    STATUS_WARNING,
    audit_registry_conformance,
    audit_tool_conformance,
    collect_live_facts,
)

_FACE_ORDER = (
    "registration", "search", "planner", "pi_native",
    "proxy", "dispatch", "result", "science",
)
_MARK = {STATUS_FAIL: "✗", "warning": "!", None: "·"}


def _face_mark(status: str) -> str:
    return _MARK.get(status, "·")


def _print_report(report) -> None:
    print(f"\n{report.tool}  ({report.tool_id})  overall={report.overall}  fp={report.fingerprint}")
    for v in report.faces:
        reason = f" {v.reason}" if v.reason else ""
        detail = f" — {v.detail}" if v.detail else ""
        print(f"  [{_face_mark(v.status)}] {v.face:<13} {v.status:<14}{reason}{detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tool", default=None,
                        help="只审计指定工具（默认全量）")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="机器可读 JSON 输出")
    parser.add_argument("--fail-on", default="fail", choices=["fail", "warning"],
                        help="退出码 1 的门槛（默认 fail）")
    args = parser.parse_args(argv)

    try:
        facts = collect_live_facts(bootstrap=True)
    except Exception as exc:  # noqa: BLE001 — 环境错误如实退出，不伪装成审计结论
        print(f"conformance audit bootstrap failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    if args.tool:
        try:
            report = audit_tool_conformance(args.tool, facts)
        except KeyError:
            print(json.dumps({"error": "tool_not_found", "tool": args.tool})
                  if args.as_json else f"tool not registered: {args.tool}",
                  file=sys.stderr)
            return 2
        if args.as_json:
            payload = report.to_dict()
            payload["registry_generation"] = facts.registry_generation
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            _print_report(report)
        bad = report.overall == STATUS_FAIL or (
            args.fail_on == "warning" and report.overall == STATUS_WARNING)
        return 1 if bad else 0

    summary = audit_registry_conformance(facts)
    if args.as_json:
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"tools audited: {summary.tool_count}"
              f"{' (TRUNCATED)' if summary.truncated else ''}")
        print(f"by overall: {json.dumps(summary.by_overall)}")
        if summary.top_reasons:
            print("top reasons:")
            for reason, n in summary.top_reasons:
                print(f"  {reason}: {n}")
        for report in summary.reports:
            if report.overall in (STATUS_FAIL, STATUS_WARNING):
                _print_report(report)
    has_fail = summary.by_overall.get(STATUS_FAIL, 0) > 0
    has_warning = summary.by_overall.get(STATUS_WARNING, 0) > 0
    if has_fail or (args.fail_on == "warning" and has_warning):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
