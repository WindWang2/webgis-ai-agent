#!/usr/bin/env python
"""生成 docs/quality/QUALITY_REPORT.{md,json}（ADR-0104 Wave 20）。

静态质量报告 = 各派生工件的**聚合投影**（不重复计算语义）：
- QualityManifest（能力覆盖 / findings / 富化闸）
- Contract Drift Report（漂移计数）
- 认证表（trace / cancellation / resource / determinism / chaos / security）
- 已知限制汇总（各工件自带，不在此重写）
- 本地 runner 状态**不进**本报告（机器相关；见 .agent-work/quality-v1/）

用法：
    python scripts/gen_quality_report.py            # 写入
    python scripts/gen_quality_report.py --check    # 过期则退出 1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]
DEFAULT_MD = Path("docs/quality/QUALITY_REPORT.md")
DEFAULT_JSON = Path("docs/quality/quality-report.json")


def _load_json(rel: str) -> dict:
    p = REPO / rel
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def build_report() -> dict:
    manifest = _load_json("docs/quality/quality-manifest.json")
    drift = _load_json("docs/quality/contract-drift-report.json")
    report = {
        "artifact": "QUALITY_REPORT",
        "aggregates": {
            "quality_manifest": {
                "fingerprint": manifest.get("fingerprint", ""),
                "counts": manifest.get("counts", {}),
                "gate": {
                    "total": manifest.get("gate", {}).get("total"),
                    "pass": manifest.get("gate", {}).get("pass"),
                },
                "findings_by_code": _count_by(manifest.get("findings", []), "code"),
            },
            "contract_drift": {
                "fingerprint": drift.get("fingerprint", ""),
                "counts": drift.get("counts", {}),
            },
            "certifications": {
                "trace_completeness": "docs/quality/certifications/TRACE_COMPLETENESS.md",
                "cancellation_coverage": "docs/quality/certifications/CANCELLATION_COVERAGE.md",
                "resource_safety": "docs/quality/certifications/RESOURCE_SAFETY.md",
                "determinism": "docs/quality/certifications/DETERMINISM.md",
                "chaos_fault_registry": "docs/quality/certifications/CHAOS_FAULT_REGISTRY.md",
                "security_controls": "docs/quality/certifications/SECURITY_CONTROLS.md",
            },
            "scenario_corpus": {
                "module": "app/evaluation/quality_corpus.py",
                "note": "规模与确定性由 tests/quality/test_quality_scenario_corpus.py 锁定",
            },
        },
    }
    return report


def _count_by(items: list, key: str) -> dict:
    out: dict = {}
    for item in items:
        k = item.get(key, "?")
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def render_md(report: dict) -> str:
    agg = report["aggregates"]
    lines: list[str] = []
    lines.append("# Quality Report（自动生成）")
    lines.append("")
    lines.append("> 由 `python scripts/gen_quality_report.py` 聚合各派生工件，")
    lines.append("> 请勿手改。本地运行状态（runner）不在本报告内：见")
    lines.append("> `.agent-work/quality-v1/runner-report.md`（gitignored）。")
    lines.append("")

    m = agg["quality_manifest"]
    lines.append("## Capability Coverage（QualityManifest）")
    lines.append("")
    counts = m.get("counts", {})
    lines.append(f"- tools {counts.get('tools', 0)} · algorithms {counts.get('algorithms', 0)}"
                 f" · capabilities {counts.get('capabilities', 0)}"
                 f" · artifact_types {counts.get('artifact_types', 0)}"
                 f" · recipes {counts.get('recipes', 0)}")
    gate = m.get("gate", {})
    verdict = "PASS" if gate.get("pass") else "FAIL"
    lines.append(f"- 描述符富化闸：**{verdict}**（可执行工具 {gate.get('total')}）")
    lines.append("")
    lines.append("| finding code | count |")
    lines.append("|---|---|")
    for code, n in m.get("findings_by_code", {}).items():
        lines.append(f"| {code} | {n} |")
    lines.append("")

    d = agg["contract_drift"]
    dc = d.get("counts", {})
    lines.append("## Contract Drift")
    lines.append("")
    lines.append(f"- total {dc.get('total', 0)} · BLOCKER {dc.get('BLOCKER', 0)}"
                 f" · MAJOR {dc.get('MAJOR', 0)} · MINOR {dc.get('MINOR', 0)}")
    lines.append("")

    lines.append("## Certifications")
    lines.append("")
    lines.append("| 认证表 | 位置 |")
    lines.append("|---|---|")
    for name, rel in agg["certifications"].items():
        lines.append(f"| {name} | `{rel}` |")
    lines.append("")
    lines.append("## Known Limitations")
    lines.append("")
    lines.append("- 静态测试引用 ≠ 行为覆盖（manifest findings 是复核线索）。")
    lines.append("- trace 认证表如实披露 contract-only 阶段（无人填充）。")
    lines.append("- 科学/制图回归套件内的 xfail 项即已知缺口（strict=False，")
    lines.append("  见各模块 docstring 的 KNOWN-GAP 列表）。")
    lines.append("- OpenAPI 快照不覆盖 WS/SSE 消息契约（各自回归闸保护）。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args()

    report = build_report()
    md = render_md(report)
    js = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    if args.check:
        stale = []
        for path, content in ((DEFAULT_MD, md), (DEFAULT_JSON, js)):
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path))
        if stale:
            print("stale: " + ", ".join(stale))
            print("run: python scripts/gen_quality_report.py")
            return 1
        print("quality report up to date")
        return 0

    DEFAULT_MD.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_MD.write_text(md, encoding="utf-8")
    DEFAULT_JSON.write_text(js, encoding="utf-8")
    print(f"wrote {DEFAULT_MD} and {DEFAULT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
