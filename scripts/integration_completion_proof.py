#!/usr/bin/env python
"""完成证明（Epic 10 §17）：合并前冲突检出端到端演示。

在 tmp 目录构造模拟仓库 + 两个模拟 feature 分支，走真实的
manifest → merge_sim 工具链（非 mock），证明系统可以在合并**之前**
发现四类冲突：

1. migration 冲突（同 NNNN 序号 + 双新增挂同一 down → 合并后多 head）；
2. registry ID 冲突（两分支新增同名 tool）；
3. generated artifact ownership 冲突（双方都改 regenerate-dont-edit
   产物 → advisory，合并后统一再生成）；
4. API semantic incompatibility 面（双方共改同一路由文件 → advisory
   热点 + co-change 标记；语义级 breaking 由 V2 api_compat 在合并后
   兜底 —— 本工具的诚实边界）。

退出码 0 = 全部类别被检出（演示通过）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

OWNERSHIP = {
    "version": 1, "adr_watermark": 118, "migration_watermark": 33,
    "rules": [
        {"pattern": "migrations/versions/*", "owner": "core", "policy": "allocator"},
        {"pattern": "CHANGELOG.md", "owner": "docs", "policy": "append-only"},
        {"pattern": "app/tools/*", "owner": "tools", "policy": "additive-only"},
        {"pattern": "app/api/routes/*", "owner": "api", "policy": "coordinated"},
        {"pattern": "docs/quality/QUALITY_MANIFEST.md", "owner": "quality",
         "policy": "regenerate-dont-edit"},
    ],
}


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                           text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


def _build_simulated_repo(root: Path) -> Path:
    repo = root / "sim"
    for d in ("app/tools", "app/api/routes", "migrations/versions",
              "docs/quality", "docs/integration"):
        (repo / d).mkdir(parents=True)
        (repo / d / ".gitkeep").write_text("", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "proof@t")
    _git(repo, "config", "user.name", "proof")
    (repo / "README.md").write_text("sim base\n", encoding="utf-8")
    (repo / "docs/quality/QUALITY_MANIFEST.md").write_text(
        "generated: base\n", encoding="utf-8")
    (repo / "migrations/versions/0033_base.py").write_text(
        'revision = "0033_base"\ndown_revision = "0032_prev"\n',
        encoding="utf-8")
    (repo / "docs/integration/ownership.json").write_text(
        json.dumps(OWNERSHIP), encoding="utf-8")
    _commit(repo, "base")
    return repo


def _build_branch(repo: Path, name: str, *, short: str, mig_seq: str,
                  tool: str, route_body: str, manifest_line: str) -> None:
    _git(repo, "checkout", "-q", "-B", name, "master")
    (repo / f"migrations/versions/{mig_seq}_{short}_probe.py").write_text(
        f'revision = "{mig_seq}_{short}_probe"\n'
        f'down_revision = "0033_base"\n',
        encoding="utf-8")
    (repo / f"app/tools/{short}_tool.py").write_text(
        f'name = "{tool}"\n', encoding="utf-8")
    (repo / "app/api/routes/layer.py").write_text(route_body, encoding="utf-8")
    (repo / "docs/quality/QUALITY_MANIFEST.md").write_text(
        manifest_line, encoding="utf-8")
    _commit(repo, f"{name} changes")


def main() -> int:
    from app.lib.integration.manifest import build_manifest
    from app.lib.integration.merge_sim import compare_pair
    from app.lib.integration.ownership import OwnershipDocument, OwnershipRule

    doc = OwnershipDocument(
        version=1, adr_watermark=118, migration_watermark=33,
        rules=tuple(OwnershipRule(**{**r, "suites": tuple(r.get("suites", ()))})
                    for r in OWNERSHIP["rules"]))

    with tempfile.TemporaryDirectory(prefix="webgis-proof-") as tmp:
        repo = _build_simulated_repo(Path(tmp))

        # 分支 A 与 B：同 migration 序号（0034）、同 tool ID、
        # 共改同一路由文件、共改同一生成物
        for short in ("a", "b"):
            name = f"feat/{short}"
            _build_branch(
                repo, name, short=short, mig_seq="0034",
                tool="colliding_tool",
                route_body=f"# route touched by {name}\n"
                           "router = None\n",
                manifest_line=f"generated: touched by {name}\n")

        ma = build_manifest("feat/a", "master", repo, doc=doc)
        mb = build_manifest("feat/b", "master", repo, doc=doc)
        report = compare_pair(ma, mb, repo_root=repo, doc=doc).as_dict()

    severity = {a["axis"]: a["severity"] for a in report["checked"]}
    checks = [
        ("migration 冲突（同序号）",
         severity.get("migration_sequence_collision") == "blocking"),
        ("migration 冲突（同 down fork → 合并后多 head）",
         severity.get("migration_forked_down") == "blocking"),
        ("registry ID 冲突（tool 同名新增）",
         severity.get("registry_id_collision") == "blocking"),
        ("生成物所有权冲突（regenerate-dont-edit 双改）",
         severity.get("shared_regenerate") == "advisory"),
        ("API 语义冲突面（路由共改 → advisory 热点）",
         severity.get("api_route_cochange") == "advisory"),
    ]
    print("完成证明 —— 合并前冲突检出（两模拟分支，真实工具链）")
    print(f"  分支: {report['branches']}  base: {report['base']}")
    ok = True
    for label, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed
    print(f"  checked 轴: {len(report['checked'])}, "
          f"unknown: {len(report['unknown'])}, "
          f"not_checked: {len(report['not_checked'])}")
    print(f"  诚实边界: unknown/not_checked 段声明了语义级冲突的检测边界")
    print(f"  verdict: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
