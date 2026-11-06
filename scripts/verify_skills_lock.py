#!/usr/bin/env python3
"""skills-lock.json 完整性校验脚本（audit ISSUE-069，#1349）。

锁文件此前只有 per-skill 内容 sha256 账本，无任何校验入口——与 AGT-01
运行时零消费同根。本脚本提供手工/CI 校验路径：

    python scripts/verify_skills_lock.py [--skills-dir app/skills]
        [--lock skills-lock.json]

检查：
  1. 锁文件可解析、结构合法；
  2. runtime_skills 段登记的每个 .py 存在且 sha256 匹配（篡改检测）；
  3. skills_dir 顶层存在但未登记的 .py 按未批准清单列出（退出码 2 —
     这些文件在启用校验时会被 quarantine，不会执行）。

退出码：0 全部一致；1 哈希不符/结构错误；2 有未登记技能。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-dir", default="app/skills")
    parser.add_argument("--lock", default="skills-lock.json")
    parser.add_argument("--agent-skills-dir", default=".agents/skills")
    args = parser.parse_args()

    skills_dir = Path(args.skills_dir)
    lock_path = Path(args.lock)
    if not lock_path.is_file():
        print(f"[skills-lock] FAIL: missing lock file at {lock_path}")
        return 1

    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[skills-lock] FAIL: unreadable lock: {exc}")
        return 1

    if not isinstance(data, dict):
        print("[skills-lock] FAIL: lock must be an object")
        return 1
    runtime = data.get("runtime_skills")
    if not isinstance(runtime, dict):
        print("[skills-lock] FAIL: runtime_skills is not a dict")
        return 1

    rc = 0
    # Installer provenance hashes also need a consumer: verify instructions
    # checked into this repository, in addition to executable runtime skills.
    agent_dir = Path(args.agent_skills_dir)
    if agent_dir.is_dir():
        entries = data.get("skills", {})
        repository_entries = data.get("repository_skills", {})
        if not isinstance(entries, dict) or not isinstance(repository_entries, dict):
            print("[skills-lock] FAIL: invalid agent skill ledger")
            return 1
        expected_agent = {
            name: entry.get("computedHash") if isinstance(entry, dict) else None
            for name, entry in entries.items()
        }
        expected_agent.update(repository_entries)
        local = {path.parent.name: path for path in agent_dir.glob("*/SKILL.md")}
        for name in sorted(set(local) | set(expected_agent)):
            path = local.get(name)
            if path is None or expected_agent.get(name) != _sha256(path):
                print(f"[skills-lock] FAIL: agent skill {name} missing, unlisted or modified")
                rc = 1

    for filename, expected in sorted(runtime.items()):
        if not isinstance(expected, str) or Path(filename).name != filename:
            print(f"[skills-lock] FAIL: invalid runtime entry {filename}")
            rc = 1
            continue
        path = skills_dir / filename
        if not path.is_file():
            print(f"[skills-lock] FAIL: registered {filename} missing on disk")
            rc = 1
            continue
        actual = _sha256(path)
        if actual != expected:
            print(f"[skills-lock] FAIL: {filename} sha256 mismatch "
                  f"(lock={expected[:12]}… disk={actual[:12]}…)")
            rc = 1
        else:
            print(f"[skills-lock] ok: {filename}")

    if skills_dir.is_dir():
        unlisted = [
            p.name for p in sorted(skills_dir.glob("*.py"))
            if not p.name.startswith("__") and p.name not in runtime
        ]
        if unlisted:
            print(f"[skills-lock] unapproved (quarantined at load): "
                  f"{', '.join(unlisted)}")
            rc = max(rc, 2)

    return rc


if __name__ == "__main__":
    sys.exit(main())
