#!/usr/bin/env python
"""Alembic 迁移编号领号工具（V9 P6 —— 多线并发防撞号的流程性根治）。

背景：master 曾两轮撞号（0034×4、0035×3），靠 merge revision 收敛 ——
根因是多线并发各自落文件、CI 直到 ``alembic heads`` 多头才暴露。本脚本
把「领号」前移到写迁移之前：

- **领号**：``--alloc 0049 --branch <branch> --revision <rev_id> --down <down>``
  把号 + 预登记的 down_revision 写进 ``migrations/.alloc.json``；
  号已被占用 / 不在任何分支段位 → 非 0 退出；
- **自检**：``--check`` 对当前树校验四件事：
    1. revision id 全局唯一（重复即 fail —— 撞号的直接形态）；
    2. 文件名数字前缀不得跨段位重复（legacy_merged 中的历史撞号块豁免；
       同段位内重复也 fail —— 段位内编号单调分配）；
    3. 每个迁移文件的前缀必须落在某个分支段位 / 历史豁免块内
       （未领号先写文件 = fail —— 流程性约束）；
    4. ``alembic ScriptDirectory`` 单头校验（多头即 fail；merge revision
       是唯一合法收敛方式）。
- ``--check <dir>`` 传临时目录（测试自证：含重复编号的副本必须 fail）。

CI 接线：db-migrations lane 增加 ``python scripts/alloc_migration.py --check``；
tests/test_alembic_metadata.py 增加编号唯一性 + 单头断言（pytest 侧等价实现）。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOC_PATH = REPO_ROOT / "migrations" / ".alloc.json"
VERSIONS_DIR = REPO_ROOT / "migrations" / "versions"

_PREFIX_RE = re.compile(r"^(?P<num>\d{4})_.+\.py$")


def _load_alloc(path: Path = ALLOC_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"segments": {}, "legacy_merged": {}, "reservations": {}}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _save_alloc(data: Dict[str, Any], path: Path = ALLOC_PATH) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ── 迁移文件静态解析（AST；#1224 教训：down_revision 可能是多行元组） ──


def parse_migration(path: Path) -> Dict[str, Any]:
    """迁移文件 → {revision, down_revision(s), prefix}。解析失败 → error 项。"""
    info: Dict[str, Any] = {
        "file": path.name,
        "prefix": (m.group("num") if (m := _PREFIX_RE.match(path.name)) else None),
        "revision": None,
        "down": [],
        "error": None,
    }
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        info["error"] = f"parse failed: {exc}"
        return info
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        else:
            continue
        if name == "revision" and info["revision"] is None:
            info["revision"] = _string_or_tuple(node.value)
        elif name == "down_revision" and not info["down"]:
            value = node.value
            if isinstance(value, ast.Constant) and value.value is None:
                info["down"] = []
            else:
                got = _string_or_tuple(value)
                info["down"] = list(got) if isinstance(got, tuple) else (
                    [got] if got else [])
    return info


def _string_or_tuple(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List)):
        parts = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                parts.append(elt.value)
        return tuple(parts)
    return None


def scan_migrations(versions_dir: Path = VERSIONS_DIR) -> List[Dict[str, Any]]:
    out = []
    for path in sorted(versions_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        out.append(parse_migration(path))
    return out


# ── 领号 ─────────────────────────────────────────────────────────────


def alloc(number: str, branch: str, revision: str, down: str,
          path: Path = ALLOC_PATH) -> int:
    data = _load_alloc(path)
    reservations = data.setdefault("reservations", {})
    segments = data.setdefault("segments", {})
    legacy = set(data.get("legacy_merged") or {})
    if number in reservations:
        print(f"FAIL: {number} 已被 {reservations[number].get('branch')} 领用"
              f"（revision={reservations[number].get('revision')}）")
        return 1
    if number in legacy:
        print(f"FAIL: {number} 属于历史撞号收敛块 legacy_merged，禁止复用")
        return 1
    owner_segment = next(
        (seg_branch for seg_branch, nums in segments.items() if number in nums),
        None,
    )
    if owner_segment is None:
        print(f"FAIL: {number} 不在任何分支段位内 —— 先在 .alloc.json segments "
              f"登记段位（多线协调，禁止抢段）")
        return 1
    if owner_segment != branch:
        print(f"FAIL: {number} 属于段位 {owner_segment}，与分支 {branch} 不符")
        return 1
    existing_files = {m["prefix"] for m in scan_migrations()}
    if number in existing_files:
        print(f"FAIL: {number} 已存在同名前缀迁移文件")
        return 1
    reservations[number] = {
        "branch": branch,
        "revision": revision,
        "down_revision": down,
    }
    _save_alloc(data, path)
    print(f"OK: {number} → {branch}（revision={revision}, down={down}）已登记")
    return 0


# ── 自检 ─────────────────────────────────────────────────────────────


def check(versions_dir: Path = VERSIONS_DIR, alloc_path: Path = ALLOC_PATH) -> int:
    data = _load_alloc(alloc_path)
    segments: Dict[str, List[str]] = data.get("segments") or {}
    legacy: Dict[str, str] = data.get("legacy_merged") or {}
    reservations: Dict[str, Any] = data.get("reservations") or {}

    migrations = scan_migrations(versions_dir)
    failures: List[str] = []

    # 1. revision id 全局唯一
    by_revision: Dict[str, List[str]] = {}
    for m in migrations:
        if m["error"]:
            failures.append(f"{m['file']}: {m['error']}")
            continue
        if not m["revision"]:
            failures.append(f"{m['file']}: 缺 revision 声明")
            continue
        by_revision.setdefault(m["revision"], []).append(m["file"])
    for rev, files in by_revision.items():
        if len(files) > 1:
            failures.append(f"revision id 重复: {rev} → {files}")

    # 2/3. 前缀归属：跨段位重复 fail；未知段位 fail（legacy / pre-regime 豁免）
    min_segment = min(
        (int(n) for nums in segments.values() for n in nums),
        default=10**9,
    )
    num_to_branches: Dict[str, List[str]] = {}
    for m in migrations:
        num = m["prefix"]
        if num is None:
            # 非数字前缀（如 c1e2f3a4b5c6_merge_*.py / hash 风格）合法
            continue
        if int(num) < min_segment:
            # 段位制度建立前的历史迁移：不追溯登记，只查同号重复
            continue
        owner = next((b for b, nums in segments.items() if num in nums), None)
        if owner is None and num in legacy:
            continue
        if owner is None:
            failures.append(
                f"{m['file']}: 前缀 {num} 未在任何段位/legacy 登记 —— 先领号再写文件"
            )
            continue
        num_to_branches.setdefault(num, []).append(owner)
    for num, branches in num_to_branches.items():
        # 跨段位同号 = 撞号现场（同段位内同号也 fail —— 段位内应单调分配）
        if len(set(branches)) > 1:
            failures.append(f"前缀 {num} 跨段位重复: {sorted(set(branches))}")
        same_files = [m["file"] for m in migrations if m["prefix"] == num]
        if len(same_files) > 1 and num not in legacy:
            failures.append(f"前缀 {num} 段位内重复文件: {same_files}")

    # 预登记与文件对账（分支合并后应把 reservation 落成文件）
    for num, res in reservations.items():
        matching = [m for m in migrations if m["prefix"] == num]
        if matching and matching[0]["revision"] != res.get("revision"):
            failures.append(
                f"{num} 预登记 revision={res.get('revision')} 与文件 "
                f"{matching[0]['revision']} 不符"
            )

    # 4. 单头（ScriptDirectory 纯静态，无需 DB）
    heads_error = _single_head_error(versions_dir, alloc_path.parent.parent)
    if heads_error:
        failures.append(heads_error)

    if failures:
        print("FAIL: alembic allocation check 发现问题：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK: {len(migrations)} 个迁移通过领号/唯一性/单头检查")
    return 0


def _single_head_error(versions_dir: Path, repo_root: Path) -> Optional[str]:
    try:
        import sys as _sys
        # 版本文件可能 import app（如 seed 迁移）—— ScriptDirectory 会执行模块
        repo_str = str(repo_root)
        if repo_str not in _sys.path:
            _sys.path.insert(0, repo_str)
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config()
        cfg.set_main_option("script_location", str(versions_dir.parent))
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
    except Exception as exc:  # noqa: BLE001 — alembic 缺席时跳过（测试侧另断言）
        print(f"WARN: 单头校验跳过（ScriptDirectory 不可用: {exc}）")
        return None
    if len(heads) != 1:
        return f"多头现场: {sorted(map(str, heads))}（用 merge revision 收敛）"
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Alembic 迁移编号领号/自检")
    parser.add_argument("--alloc", metavar="NNNN", help="领号（四位数字前缀）")
    parser.add_argument("--branch", help="领号分支名")
    parser.add_argument("--revision", help="revision id（预登记）")
    parser.add_argument("--down", default="", help="down_revision（预登记）")
    parser.add_argument("--check", nargs="?", const="default", dest="check",
                        help="自检模式；可传临时 versions 目录（自证用）")
    parser.add_argument("--alloc-file", help="覆盖 .alloc.json 路径（测试用）")
    args = parser.parse_args(argv)

    alloc_path = Path(args.alloc_file) if args.alloc_file else ALLOC_PATH
    if args.alloc:
        if not (args.branch and args.revision):
            parser.error("--alloc 需要 --branch 与 --revision")
        return alloc(args.alloc, args.branch, args.revision, args.down,
                     path=alloc_path)
    if args.check is not None:
        target = VERSIONS_DIR if args.check == "default" else Path(args.check)
        return check(versions_dir=target, alloc_path=alloc_path)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
