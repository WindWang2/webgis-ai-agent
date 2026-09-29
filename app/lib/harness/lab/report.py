"""Lab 报告与基线（E15 D6）：机器可读 JSON + 人读摘要 + 显式基线。

基线纪律（任务书红线）：**只允许显式批准更新** —— ``write_baseline``
在有红时拒绝（``force=True`` 才放行并留痕），不存在任何自动"刷绿"路径；
digest 漂移比对给出逐 spec 归因。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.lib.harness.lab.metrics import DIMENSIONS, FAIL, SpecEval
from app.lib.harness.replay.determinism import sha256_of

__all__ = [
    "LAB_REPORT_KIND",
    "LAB_BASELINE_KIND",
    "build_report",
    "report_digest",
    "render_markdown",
    "write_report",
    "write_baseline",
    "compare_baseline",
]

LAB_REPORT_KIND = "eval_lab_report"
LAB_BASELINE_KIND = "eval_lab_baseline"


def build_report(evals: List[SpecEval], *, seed: int = 0,
                 spec_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    entries = [ev.as_dict() for ev in sorted(evals, key=lambda e: e.spec_id)]
    green = sum(1 for e in entries if e["ok"])
    return {
        "kind": LAB_REPORT_KIND,
        "seed": seed,
        "specs": len(entries),
        "green": green,
        "red": len(entries) - green,
        "report_digest": report_digest(evals),
        "entries": entries,
        **({"spec_paths": list(spec_paths)} if spec_paths else {}),
    }


def report_digest(evals: List[SpecEval]) -> str:
    """跨 spec 聚合 digest（逐 spec digest_fields 的规范哈希；确定性）。"""
    return sha256_of([ev.digest_fields() for ev in
                      sorted(evals, key=lambda e: e.spec_id)])


def render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = [
        "# Offline Harness Evaluation Lab Report", "",
        f"- specs: {report.get('specs')}  green: {report.get('green')}"
        f"  red: {report.get('red')}",
        f"- seed: {report.get('seed')}  digest: `{report.get('report_digest')}`",
        "- Verdicts are per-dimension: any fail fails the spec; declared"
        " dimensions that stay not_evaluated also fail (no fake success).",
        "",
        "| spec | kind | ok | " + " | ".join(DIMENSIONS) + " |",
        "|---|---|---|" + "---|" * len(DIMENSIONS),
    ]
    for entry in report.get("entries") or []:
        dims = entry.get("dimensions") or {}
        cells = " | ".join(
            str((dims.get(d) or {}).get("status") or "n/a")
            for d in DIMENSIONS)
        mark = "✅" if entry.get("ok") else "❌"
        lines.append(
            f"| {entry.get('spec_id')} | {entry.get('kind')} | {mark} | {cells} |")
    failures = [e for e in report.get("entries") or []
                if (e.get("failures") or [])
                or any((v or {}).get("status") == FAIL
                       for v in (e.get("dimensions") or {}).values())]
    if failures:
        lines += ["", "## Failures", ""]
        for entry in failures:
            lines.append(f"### {entry.get('spec_id')}")
            lines.append("")
            for name, verdict in sorted(
                    (entry.get("dimensions") or {}).items()):
                if (verdict or {}).get("status") == FAIL and verdict.get("detail"):
                    lines.append(f"- [{name}] {verdict['detail']}")
            for failure in (entry.get("failures") or [])[:12]:
                lines.append(f"- {failure}")
            lines.append("")
    return "\n".join(lines) + "\n"


def write_report(report: Dict[str, Any], output: str, fmt: str = "json") -> None:
    if fmt == "json":
        text = json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True)
    elif fmt == "md":
        text = render_markdown(report)
    else:
        raise ValueError(f"unknown format {fmt!r}")
    text += "\n"
    if output == "-":
        print(text, end="")
        return
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def write_baseline(report: Dict[str, Any], output: str, *,
                   force: bool = False) -> Dict[str, Any]:
    """基线只含确定性投影；有红拒绝写（显式 --force 才放行 —— 审计留痕面）。"""
    red = int(report.get("red") or 0)
    if red and not force:
        raise ValueError(
            f"refusing to write baseline with {red} red specs "
            "(fix or pass --force to record a known-red baseline)")
    payload: Dict[str, Any] = {
        "kind": LAB_BASELINE_KIND,
        "seed": report.get("seed"),
        "green": report.get("green"),
        "report_digest": report.get("report_digest"),
        "entries": [
            {
                "spec_id": e.get("spec_id"),
                "kind": e.get("kind"),
                "ok": e.get("ok"),
                "dimensions": {
                    name: (v or {}).get("status")
                    for name, v in sorted((e.get("dimensions") or {}).items())
                },
                "replay_digest": e.get("replay_digest"),
            }
            for e in sorted(
                (x for x in report.get("entries") or [] if isinstance(x, dict)),
                key=lambda x: str(x.get("spec_id") or ""))
        ],
    }
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    return payload


def compare_baseline(report: Dict[str, Any],
                     baseline_path: str) -> Dict[str, Any]:
    """digest/裁决漂移比对（new / missing / digest_drift / verdict_drift）。"""
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    base_entries = {e.get("spec_id"): e
                    for e in baseline.get("entries") or [] if e.get("spec_id")}
    cur_entries = {e.get("spec_id"): e
                   for e in report.get("entries") or [] if e.get("spec_id")}

    def _status_map(entry: Dict[str, Any]) -> Dict[str, Any]:
        dims = entry.get("dimensions") or {}
        return {k: (v.get("status") if isinstance(v, dict) else v)
                for k, v in dims.items()}

    drifts: List[Dict[str, Any]] = []
    for spec_id, entry in sorted(cur_entries.items()):
        base = base_entries.get(spec_id)
        if base is None:
            drifts.append({"spec_id": spec_id, "kind": "new"})
            continue
        if base.get("replay_digest") != entry.get("replay_digest"):
            drifts.append({"spec_id": spec_id, "kind": "digest_drift",
                           "baseline_ok": base.get("ok"),
                           "current_ok": entry.get("ok")})
        base_dims = base.get("dimensions") or {}
        cur_dims = _status_map(entry)
        for dim in sorted(set(base_dims) | set(cur_dims)):
            if base_dims.get(dim) != cur_dims.get(dim):
                drifts.append({
                    "spec_id": spec_id, "kind": "verdict_drift",
                    "dimension": dim,
                    "baseline": base_dims.get(dim),
                    "current": cur_dims.get(dim),
                })
    for spec_id in sorted(set(base_entries) - set(cur_entries)):
        drifts.append({"spec_id": spec_id, "kind": "missing"})
    return {
        "baseline": baseline_path,
        "digest_baseline": baseline.get("report_digest"),
        "digest_current": report.get("report_digest"),
        "green_baseline": baseline.get("green"),
        "green_current": report.get("green"),
        "drifts": drifts,
    }
