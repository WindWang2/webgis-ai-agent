#!/usr/bin/env python
"""ADR 编号分配器（Quality V3 W4）。

用法：
    python scripts/allocate_adr.py <slug> [--dry-run]

行为：
- next = max(编号)+1；文件名 ``NNNN-<slug>.md``；
- 校验目标编号未被占用（含非标准命名文件）；
- 打印新 watermark（分配即新水位）与推进提醒——分配后必须把
  ``docs/integration/ownership.json.adr_watermark`` 推进到该编号，否则
  与并发分支撞号时 preflight 无法区分新旧（C-2）；
- 撞号不可静态消除（并发窗口），本命令 + preflight + merge-sim 组合
  把撞号从"静默"变"分配时可见 + 合并前红"。

退出码：0 可分配；2 拒绝（slug 非法 / 编号已占用）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="ADR slug（NNNN-<slug>.md）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not _SLUG_RE.match(args.slug):
        print(f"拒绝：slug {args.slug!r} 非法（需 ^[a-z0-9][a-z0-9\\-]*$）")
        return 2

    from app.lib.integration import adr
    from app.lib.integration.ownership import load_document

    nxt, warnings = adr.next_number(REPO)
    for w in warnings:
        print(f"警告: {w}")
    target = adr.ADR_DIR
    if (REPO / target / f"{nxt:04d}-{args.slug}.md").exists():
        print(f"拒绝：{nxt:04d}-{args.slug}.md 已存在")
        return 2

    doc = load_document(REPO)
    print(f"分配成功：{target}/{nxt:04d}-{args.slug}.md")
    print(f"  标题行  : # ADR {nxt:04d} — <标题>")
    if nxt <= doc.adr_watermark:
        print(f"警告：目标编号 {nxt} ≤ 当前 watermark {doc.adr_watermark}，"
              "疑似与存量或并发分支撞号")
    print(f"  新 watermark: {nxt}（分配后推进 ownership.json 的 adr_watermark，"
          "随本分支提交）")
    print("  注意：rebase origin/master 后必须重跑本命令（并发分支可能已占用该号）。")
    if args.dry_run:
        print("（dry-run：未写任何文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
