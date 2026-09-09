#!/usr/bin/env python
"""Frontend 行为证据聚合器（Quality V3 W14）。

扫描 frontend 测试文件中的 ``@behavior:<id>`` 标签（describe 标题级），
聚合为 ``docs/integration/frontend-behavior.json``（确定性、字节闸）：
行为 id → 测试文件 → 用例清单。前端行为面（map layer lifecycle /
MapSpec reconcile / export / collaboration）由此成为机器可读的发布证据，
而不是"有测试"的口头声明。

用法：
    python scripts/gen_frontend_behavior.py --check    # 字节一致（闸）
    python scripts/gen_frontend_behavior.py            # 再生成
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend"
OUT_PATH = REPO / "docs/integration/frontend-behavior.json"

_TAG_RE = re.compile(r"@behavior:([a-z0-9\-]+)")
_MAX_FILES = 4000
_MAX_TEST_FILES = 800


def scan() -> dict:
    """扫描 frontend 测试文件 → {behavior: [{file, tests: [...]}]}。"""
    behaviors: dict[str, list[dict]] = {}
    count = 0
    for path in sorted(FRONTEND.rglob("*.test.ts*")):
        if "node_modules" in path.parts or count >= _MAX_FILES:
            if count >= _MAX_FILES:
                print(f"警告：文件数达上限 {_MAX_FILES}，扫描提前终止"
                      f"（R2-NIT：不静默截断）", file=_sys.stderr)
            continue
        count += 1
        if len(behaviors) > _MAX_TEST_FILES:
            print(f"警告：测试文件数达上限 {_MAX_TEST_FILES}，扫描提前终止",
                  file=_sys.stderr)
            break
        rel = path.relative_to(REPO).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        current_tags: list[str] = []
        for line in text.splitlines():
            line_s = line.strip()
            m = _TAG_RE.search(line_s)
            if m:
                if m.group(1) not in current_tags:
                    current_tags.append(m.group(1))
            elif line_s.startswith("it(") or line_s.startswith("test("):
                # 只聚合显式 @behavior 打标的用例：清单是行为证据索引，
                # 不是全量测试目录（全量由 runner/CI 覆盖）
                if not current_tags:
                    continue
                title = _extract_title(line_s)
                if title:
                    for tag in current_tags:
                        behaviors.setdefault(tag, [])
                        entry = next(
                            (e for e in behaviors[tag]
                             if e["file"] == rel), None)
                        if entry is None:
                            entry = {"file": rel, "tests": []}
                            behaviors[tag].append(entry)
                        if len(entry["tests"]) < 200:
                            entry["tests"].append(title)
            elif line_s.startswith("describe("):
                # 标签作用域以文件为单位（简化但确定）
                continue
    return behaviors


def _extract_title(line: str) -> str:
    start = line.find("(")
    if start == -1:
        return ""
    rest = line[start + 1:].strip()
    if rest[:1] in ("'", '"', "`"):
        quote = rest[0]
        end = rest.find(quote, 1)
        if end != -1:
            return rest[1:end][:200]
    return ""


def build_state() -> dict:

    behaviors = scan()
    payload = json.dumps(behaviors, ensure_ascii=False, sort_keys=True,
                         indent=1)
    return {
        "behavior_count": len(behaviors),
        "content_hash": hashlib.sha256(
            payload.encode("utf-8")).hexdigest(),
        "behaviors": behaviors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    state = build_state()
    rendered = json.dumps(state, ensure_ascii=False, sort_keys=True,
                          indent=1) + "\n"
    if args.check:
        if not OUT_PATH.exists():
            print(f"FAIL: {OUT_PATH.relative_to(REPO)} 缺失")
            return 1
        current = OUT_PATH.read_text(encoding="utf-8")
        if current != rendered:
            print("FAIL: frontend-behavior.json 过期（@behavior 标签漂移）"
                  "—— 重跑 scripts/gen_frontend_behavior.py")
            return 1
        print(f"ok: {state['behavior_count']} behaviors")
        return 0
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(REPO)} "
          f"({state['behavior_count']} behaviors)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
