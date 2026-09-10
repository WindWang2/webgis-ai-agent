#!/usr/bin/env python
"""Migration revision 分配器（Quality V3 W3）。

用法：
    python scripts/allocate_migration.py <slug> [--dry-run]

行为：
- 校验当前 graph 单 head（多 head → 拒绝，要求先合并/linearize）；
- next = max(NNNN)+1，文件名 ``NNNN_<slug>.py``；
- **不生成文件内容**（模板由 alembic revision --autogenerate 或人工写；
  本命令的职责是把"分配"从静默撞号变成显式声明）；
- 打印 base SHA、建议 down_revision、以及 rebase 提醒——allocator 降低
  撞号概率但不消除（10 worktree 无共享锁），必须与 preflight/merge-sim
  组合（架构 R2/m-5）；
- 分配后必须推进 ``docs/integration/ownership.json.migration_watermark``
  （preflight 红线提示），本命令打印提醒。

退出码：0 可分配（--dry-run 时同样）；2 拒绝（多 head / slug 非法）。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="revision 语义 slug（NNNN_<slug>.py）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not _SLUG_RE.match(args.slug):
        print(f"拒绝：slug {args.slug!r} 非法（需 ^[a-z0-9][a-z0-9_]*$）")
        return 2

    from app.lib.integration import migrations_coord
    from app.lib.integration.ownership import load_document

    graph = migrations_coord.scan(REPO)
    heads = graph.heads
    if len(heads) != 1:
        print(f"拒绝：当前 {len(heads)} 个 head，先 linearize: {heads}")
        return 2

    doc = load_document(REPO)
    seqs = [i.seq for i in graph.revisions if i.seq is not None]
    nxt = (max(seqs) + 1) if seqs else 1
    filename = f"{nxt:04d}_{args.slug}.py"
    existing = [i.file for i in graph.revisions if Path(i.file).name == filename]
    if existing:
        print(f"拒绝：{filename} 已存在（撞号）：{existing}")
        return 2

    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                          capture_output=True, text=True, timeout=10).stdout.strip()
    print(f"分配成功：{filename}")
    print(f"  base SHA       : {base}")
    print(f"  down_revision  : \"{heads[0]}\"")
    print(f"  revision       : \"{nxt:04d}_{args.slug}\"")
    print("  注意：")
    print("  - rebase origin/master 后必须重查（max+1 可能已被并发分支占用）；")
    if nxt > doc.migration_watermark:
        print(f"  - 同步推进 docs/integration/ownership.json 的 "
              f"migration_watermark: {doc.migration_watermark} → {nxt}，"
              "否则 preflight 红。")
    if args.dry_run:
        print("（dry-run：未写任何文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
