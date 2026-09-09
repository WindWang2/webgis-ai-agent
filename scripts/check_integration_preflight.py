#!/usr/bin/env python
"""Integration preflight（Quality V3，Epic 10 R2）——合并前协调闸一条龙。

把跨分支协调检查收敛为单命令，挂进 ``scripts/quality_runner.py`` 的
quick lane（CI release DAG 覆盖面内，Subagent-A 挑战 C-1 的强制点）：

1. ownership 规则结构校验（pattern/owner/policy 词表 + 两两不相交）；
2. ownership ↔ artifact_graph.DECLARED 双向 parity；
3. migration 多 head 检测 + NNNN 高水位（W3）；
4. ADR watermark：编号 > watermark 的重复撞号 → 红 + 悬空链接棘轮（W4）；
5. 生成物 staleness（复用 V2 闸）。

秒级预算（< 10s）：全部是静态扫描，无网络/无 DB/无重 AST。

用法：
    python scripts/check_integration_preflight.py            # 违例 → exit 1
    python scripts/check_integration_preflight.py --report   # JSON 报告到 stdout
    python scripts/check_integration_preflight.py --update-baselines
                                                             # 刷新 ADR 悬空链接棘轮基线
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]


def run_preflight(repo_root: Path) -> dict:
    """执行全部协调检查，返回结构化报告（纯函数便于测试）。"""
    from app.lib.integration import adr as adr_mod
    from app.lib.integration import migrations_coord as mig_mod
    from app.lib.integration import ownership as ownership_mod
    from app.lib.quality.artifact_graph import DECLARED, find_stale, load_recorded

    t0 = time.monotonic()
    checks: list[dict] = []

    def _add(name: str, errors: list[str], detail: dict | None = None) -> None:
        entry = {"check": name, "ok": not errors, "errors": errors}
        if detail:
            entry["detail"] = detail
        checks.append(entry)

    # 1+2. ownership 结构 + parity
    doc = ownership_mod.load_document(repo_root)
    _add("ownership_structure",
         ownership_mod.validate_document(doc, repo_root),
         {"rules": len(doc.rules)})
    declared_artifacts = [e.artifact for e in DECLARED]
    _add("ownership_artifact_parity",
         ownership_mod.parity_with_artifact_graph(doc, declared_artifacts),
         {"declared": len(declared_artifacts)})

    # 3. migration 单头 + 高水位
    mig = mig_mod.scan(repo_root)
    mig_errors: list[str] = []
    if len(mig.heads) != 1:
        mig_errors.append(
            f"migration 多 head（{len(mig.heads)}）: {sorted(mig.heads)}")
    over = mig.revisions_over_watermark(doc.migration_watermark)
    if over:
        mig_errors.append(
            f"revision 序号超过高水位 {doc.migration_watermark}（分配后未推进 "
            "ownership.migration_watermark？）: " + ", ".join(over))
    _add("migration_heads", mig_errors,
         {"heads": sorted(mig.heads), "revisions": len(mig.revisions)})

    # 4. ADR watermark + 悬空链接棘轮
    adr_errors = adr_mod.check_watermark(repo_root, doc.adr_watermark)
    adr_errors += adr_mod.check_referenced_files(repo_root)
    _add("adr_watermark", adr_errors,
         {"watermark": doc.adr_watermark,
          "known_dangling": len(adr_mod.load_dangling_baseline(repo_root))})

    # 5. 生成物 staleness（复用 V2 闸语义，聚合进同一报告）
    stale = find_stale(load_recorded())
    _add("generated_staleness",
         [f"{a} 输入指纹已变，需再生成" for a in stale],
         {"artifacts": len(load_recorded())})

    ok = all(c["ok"] for c in checks)
    return {
        "preflight_version": 1,
        "ok": ok,
        "elapsed_s": round(time.monotonic() - t0, 3),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true",
                        help="输出 JSON 报告（而非人读摘要）")
    parser.add_argument("--update-baselines", action="store_true",
                        help="刷新 ADR 悬空链接棘轮基线（存量即基线，慎用）")
    args = parser.parse_args()

    if args.update_baselines:
        from app.lib.integration import adr as adr_mod
        import json as _json

        dangling = adr_mod.dangling_links(REPO)
        path = REPO / adr_mod.DANGLING_BASELINE_PATH
        path.write_text(
            _json.dumps({"dangling": dangling}, ensure_ascii=False, indent=1)
            + "\n", encoding="utf-8")
        print(f"wrote {adr_mod.DANGLING_BASELINE_PATH}（{len(dangling)} 条）")
        return 0

    report = run_preflight(REPO)
    if args.report:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        verdict = "PASS" if report["ok"] else "FAIL"
        print(f"integration preflight: {verdict}（{report['elapsed_s']}s）")
        for check in report["checks"]:
            mark = "ok" if check["ok"] else "RED"
            print(f"  [{mark}] {check['check']}")
            for err in check["errors"]:
                print(f"        - {err}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
