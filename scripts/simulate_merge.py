#!/usr/bin/env python
"""跨分支合并模拟 CLI（Quality V3 W8）。

用法：
    python scripts/simulate_merge.py --branches feat/a,feat/b
    python scripts/simulate_merge.py --branches feat/a,feat/b --stdout

流程：各分支生成 semantic integration manifest（live，基于 git diff vs
merge-base）→ compare_pair 全轴对比 → 报告写
``.agent-work/integration/merge-sim/<a>_vs_<b>.json|md``。

退出码：0 无阻塞冲突；1 有阻塞冲突（migration 撞号 / registry ID 撞号 /
single-writer 双改 / events 词表双改）；2 输入错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / ".agent-work/integration/merge-sim"


def render_md(report: dict) -> str:
    lines = [
        "# Merge Simulation Report", "",
        f"- branches: `{', '.join(report['branches'])}`（base `{report['base']}`）",
        f"- verdict: **{'BLOCKED' if not report['ok'] else 'OK'}**", "",
        "## checked", "",
    ]
    for axis in report["checked"]:
        mark = {"blocking": "RED", "advisory": "WARN", "clear": "ok"}.get(
            axis["severity"], axis["severity"])
        lines.append(f"- [{mark}] {axis['axis']}")
        for item in axis["items"]:
            lines.append(f"    - {json.dumps(item, ensure_ascii=False)}")
    lines += ["", "## unknown（本工具无法判定）", ""]
    for axis in report["unknown"]:
        lines.append(f"- {axis['axis']}")
        for item in axis["items"]:
            lines.append(f"    - {json.dumps(item, ensure_ascii=False)}")
    lines += ["", "## not_checked（声明上不检测）", ""]
    for axis in report["not_checked"]:
        lines.append(f"- {axis['axis']}: {axis['reason']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    from app.lib.integration.manifest import build_manifest
    from app.lib.integration.merge_sim import compare_pair

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branches", required=True,
                        help="逗号分隔的两个分支 ref（当前仅两两对比）")
    parser.add_argument("--base", default=None,
                        help="对比基线（默认各自与 origin/master 的 merge-base）")
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()

    branches = [b.strip() for b in args.branches.split(",") if b.strip()]
    if len(branches) != 2:
        print("拒绝：--branches 需要恰好两个分支", file=sys.stderr)
        return 2
    base = args.base or "origin/master"

    try:
        manifests = [build_manifest(b, base, REPO) for b in branches]
    except RuntimeError as exc:
        print(f"拒绝：{exc}", file=sys.stderr)
        return 2

    report = compare_pair(manifests[0], manifests[1], repo_root=REPO).as_dict()
    if args.stdout:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if report["ok"] else 1

    safe = [re.sub(r"[^A-Za-z0-9._\-]", "_", b) for b in branches]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{safe[0]}_vs_{safe[1]}"
    (OUT_DIR / f"{stem}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    (OUT_DIR / f"{stem}.md").write_text(render_md(report), encoding="utf-8")
    verdict = "BLOCKED" if not report["ok"] else "OK"
    print(f"merge simulation: **{verdict}** → .agent-work/integration/merge-sim/{stem}.md")
    for axis in report["checked"]:
        if axis["severity"] in ("blocking", "advisory"):
            print(f"  [{axis['severity']}] {axis['axis']} "
                  f"({len(axis['items'])} items)")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
