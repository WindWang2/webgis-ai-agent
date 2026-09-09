#!/usr/bin/env python
"""分支 Semantic Integration Manifest 生成器（Quality V3 W6）。

用法：
    python scripts/gen_integration_manifest.py --branch HEAD
    python scripts/gen_integration_manifest.py --branch feat/foo --base origin/master
    python scripts/gen_integration_manifest.py --branch HEAD --include-worktree

输出：``.agent-work/integration/manifests/<branch-sanitized>.json``
（gitignored 的分支工作产物；merge simulation 与 release readiness 的输入）。

退出码：0 成功；2 git/diff 失败（分支不存在等）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / ".agent-work/integration/manifests"


def main() -> int:
    from app.lib.integration.manifest import DEFAULT_BASE, build_manifest

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", default="HEAD")
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--include-worktree", action="store_true",
                        help="并入工作区未提交改动（仅 --branch HEAD 时有意义）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stdout", action="store_true",
                        help="打印 JSON 而不写文件")
    args = parser.parse_args()

    try:
        manifest = build_manifest(
            args.branch, args.base, REPO,
            include_worktree=args.include_worktree)
    except RuntimeError as exc:
        print(f"拒绝：{exc}", file=sys.stderr)
        return 2

    if args.stdout:
        print(manifest.to_json(), end="")
        return 0

    safe = re.sub(r"[^A-Za-z0-9._\-]", "_", args.branch)
    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / f"{safe}.json"
    out_path.write_text(manifest.to_json(), encoding="utf-8")
    print(f"manifest → {out_path.relative_to(REPO)}")
    print(f"  branch={manifest.branch} base={manifest.base} "
          f"files={len(manifest.files_changed)} risk={manifest.risk_level} "
          f"suites={','.join(manifest.required_suites)}")
    if manifest.risk_reasons:
        for reason in manifest.risk_reasons[:8]:
            print(f"  - {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
