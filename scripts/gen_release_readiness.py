#!/usr/bin/env python
"""Release readiness 证据聚合器（Quality V3 W15，Epic 10 §M / R7）。

三类状态源，全部诚实：

1. **现场执行的廉价闸**（生成时真实运行）：quality manifest 字节闸、
   contract drift、integration preflight（ownership/ADR watermark/
   migration 单头/生成物 staleness/手改检测）、前端行为索引 ——
   pass 即真实证据；
2. **车道证据**（仅在提供有效 runner 报告时采信）：path 存在 + JSON
   可解析 + sha256 记录。未提供 = ``not-run``（附原因），不伪造 pass；
3. **政策断言（独立于字节闸，不可绕过）**：gates 全 pass 且
   quick/backend 车道有 pass 证据，否则 verdict = NOT-READY 并退出非零；
   豁免必须在 ``--waiver`` JSON 里逐条给 reason（进入 known_gaps）。

产物：``docs/integration/RELEASE_READINESS.{json,md}``（确定性：
身份 = git commit，无时钟）。

入库语义：提交的产物 = **无车道证据**的机器无关快照 —— gates 全绿 +
车道 ``not-run`` + verdict NOT-READY（诚实：PR 本身不可声称 READY）。
合并前的 READY 判定 = 本地带 ``--runner-report`` 运行（READY 产物留在
本机，lane 证据以 sha256 进入报告），或走 CI release DAG。

用法：
    python scripts/gen_release_readiness.py --check
    python scripts/gen_release_readiness.py \\
        --runner-report .agent-work/quality-v2/runner-report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
OUT_JSON = REPO / "docs/integration/RELEASE_READINESS.json"
OUT_MD = REPO / "docs/integration/RELEASE_READINESS.md"

#: 现场闸：命令 → 名称（全部秒级/十秒级；资源纪律）
LIVE_GATES = (
    ("quality_manifest", [sys.executable, "scripts/gen_quality_manifest.py", "--check"]),
    ("contract_drift", [sys.executable, "scripts/gen_drift_report.py", "--check"]),
    ("integration_preflight", [sys.executable, "scripts/check_integration_preflight.py", "--report"]),
    ("frontend_behavior", [sys.executable, "scripts/gen_frontend_behavior.py", "--check"]),
)

#: 发布政策要求 pass 证据的车道（runner 报告中的 lane 名）
REQUIRED_LANES = ("quick", "backend")
KNOWN_LANES = ("quick", "backend", "frontend", "science", "cartography",
               "data", "security", "quality", "perf", "real")


def _git_commit() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                          capture_output=True, text=True, timeout=10
                          ).stdout.strip()


def _run_gate(cmd: list) -> dict:
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                          timeout=120)
    ok = proc.returncode == 0
    # detail 剥离易变字段（elapsed/耗时）—— 产物必须字节确定，
    # 否则 --check 在同 commit 上都不自洽（C-1）
    import re as _re

    volatile = _re.compile(r"elapsed_s|\d+\.\d+s|\d+\.\d+ ms")
    detail = [ln for ln in (proc.stdout or "").strip().splitlines()
              if not volatile.search(ln)][-6:]
    return {"command": " ".join(str(c) for c in cmd), "status":
            "pass" if ok else "fail", "detail": detail}


def _load_runner_evidence(path: str | None) -> dict:
    """校验 runner 报告 → {lane: pass|fail|not-run} + sha256 证据。"""
    if not path:
        return {"lanes": {lane: "not-run" for lane in KNOWN_LANES},
                "reason": "未提供 --runner-report（车道证据 opt-in）",
                "evidence": None}
    p = Path(path)
    if not p.is_absolute():
        p = REPO / path
    if not p.exists():
        return {"lanes": {lane: "not-run" for lane in KNOWN_LANES},
                "reason": f"报告不存在: {path}", "evidence": None}
    try:
        report = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"lanes": {lane: "not-run" for lane in KNOWN_LANES},
                "reason": f"报告不可解析: {path}", "evidence": None}
    evidence = {
        "path": str(p),
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "total_s": report.get("total_s"),
    }
    lanes = {}
    for lane in KNOWN_LANES:
        entry = next((ln for ln in report.get("lanes", [])
                      if ln.get("lane") == lane), None)
        if entry is None:
            lanes[lane] = "not-run"
        else:
            lanes[lane] = "pass" if entry.get("ok") else "fail"
    return {"lanes": lanes, "reason": None, "evidence": evidence}


def _waivers(path: str | None) -> list[dict]:
    if not path:
        return []
    p = Path(path)
    if not p.is_absolute():
        p = REPO / path
    if not p.exists():
        raise SystemExit(f"waiver 文件不存在: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    for w in data.get("waivers", []):
        if not w.get("reason"):
            raise SystemExit(f"waiver 缺 reason: {w}")
    return data.get("waivers", [])


def _known_gaps() -> list[dict]:
    from app.lib.integration import adr as adr_mod
    from app.lib.integration.ownership import load_document

    doc = load_document(REPO)
    gaps = [{"id": f"adr-duplicate-{n}", "detail": f"存量 ADR {n:04d} 撞号"
             f"（watermark 下 known limitation）: {files}"}
            for n, files in adr_mod.scan(REPO).duplicates().items()
            if n <= doc.adr_watermark]
    gaps.append({
        "id": "real-lane-opt-in",
        "detail": "real-services 车道为 opt-in 资源纪律；未运行时本报告"
                  "以 not-run 显式标注，不构成 pass",
    })
    gaps.append({
        "id": "browser-e2e",
        "detail": "Playwright 浏览器级 E2E 沿用既有 nightly REQUIRE_BROWSER "
                  "机制；本 Epic 的前端证据为 vitest 组件/模块行为级",
    })
    return gaps


def build_state(runner_report: str | None, waiver_path: str | None) -> tuple[dict, list[str]]:
    """返回 (state, violations)。violations 非空 = NOT-READY。"""
    gates = {}
    for name, cmd in LIVE_GATES:
        gates[name] = _run_gate(cmd)
    evidence_block = _load_runner_evidence(runner_report)
    waivers = _waivers(waiver_path)
    waiver_lanes = {w.get("lane") for w in waivers}

    violations: list[str] = []
    for name, result in gates.items():
        if result["status"] != "pass":
            violations.append(f"gate {name} 未通过")
    for lane in REQUIRED_LANES:
        status = evidence_block["lanes"].get(lane, "not-run")
        if status == "pass" or lane in waiver_lanes:
            continue
        violations.append(
            f"lane {lane} 状态 {status}（需 pass 证据或显式 waiver）")

    # git_commit = 生成时 HEAD（pre-commit）。身份以包含本文件的 commit
    # 为准 —— 内嵌"最终 commit"存在自指回归（文件进 commit 后 HEAD 必然
    # 改变，字节闸将永不自洽）。
    state = {
        "policy_version": 1,
        "git_commit_generation_time": _git_commit(),
        "gates": gates,
        "lanes": evidence_block["lanes"],
        "lane_evidence_reason": evidence_block["reason"],
        "runner_evidence": evidence_block["evidence"],
        "waivers": waivers,
        "known_gaps": _known_gaps(),
    }
    if violations:
        state["verdict"] = "NOT-READY"
        state["violations"] = sorted(violations)
    else:
        state["verdict"] = "READY"
        state["violations"] = []
    return state, violations


def _render_md(state: dict) -> str:
    lines = ["# Release Readiness（生成物 · 确定性）", ""]
    lines.append(f"- verdict: **{state['verdict']}**")
    lines.append(f"- git commit（生成时）: `{state['git_commit_generation_time'][:12]}`"
                 "（身份以包含本文件的 commit 为准）")
    lines.append("")
    lines.append("## 现场闸（生成时真实执行）")
    lines.append("")
    for name, result in state["gates"].items():
        lines.append(f"- [{result['status']}] {name}")
    lines.append("")
    lines.append("## 车道证据")
    lines.append("")
    for lane, status in state["lanes"].items():
        lines.append(f"- {lane}: {status}")
    if state.get("lane_evidence_reason"):
        lines.append(f"- 证据说明: {state['lane_evidence_reason']}")
    if state.get("runner_evidence"):
        ev = state["runner_evidence"]
        lines.append(f"- 报告: `{ev['path']}`（sha256 `{ev['sha256'][:16]}…`）")
    if state.get("violations"):
        lines.append("")
        lines.append("## 政策缺口")
        lines.append("")
        for v in state["violations"]:
            lines.append(f"- {v}")
    lines.append("")
    lines.append("## Known gaps（诚实披露）")
    lines.append("")
    for gap in state["known_gaps"]:
        lines.append(f"- {gap['id']}: {gap['detail']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="校验已提交产物与再生成一致（字节闸）")
    parser.add_argument("--runner-report", default=None)
    parser.add_argument("--waiver", default=None,
                        help="车道豁免 JSON（[{lane, reason}]，须给 reason）")
    args = parser.parse_args()

    state, _ = build_state(args.runner_report, args.waiver)
    rendered_json = json.dumps(state, ensure_ascii=False, sort_keys=True,
                               indent=1) + "\n"
    rendered_md = _render_md(state)

    if args.check:
        # C-1（R1）：commit 身份字段做归一比对 —— 产物内嵌"生成时 HEAD"
        # 会在每次 commit 后必然过期（自指回归），把该字段从闸中剥离
        # （结构/政策内容由其余字段锁定）；闸锁定的是 gates/lanes/waivers/
        # known_gaps/verdict 这些**内容性**状态。
        def _normalize(text: str, as_json: bool) -> str:
            if not as_json:
                import re as _re

                pattern = r"- git commit（生成时）: `[0-9a-f]+`[^\n]*"
                return _re.sub(
                    pattern, "- git commit（生成时）: <normalized>", text)
            data = json.loads(text)
            data.pop("git_commit_generation_time", None)
            return json.dumps(data, ensure_ascii=False, sort_keys=True,
                              indent=1) + "\n"

        checks = (
            (OUT_JSON, rendered_json, True),
            (OUT_MD, rendered_md, False),
        )
        for out, rendered, is_json in checks:
            if not out.exists():
                print(f"FAIL: {out.relative_to(REPO)} 缺失 —— "
                      "重跑 scripts/gen_release_readiness.py")
                return 1
            if _normalize(out.read_text(encoding="utf-8"), is_json) != \
                    _normalize(rendered, is_json):
                print(f"FAIL: {out.relative_to(REPO)} 过期 —— "
                      "重跑 scripts/gen_release_readiness.py")
                return 1
        print(f"ok: verdict={state['verdict']}")
        return 0

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(rendered_json, encoding="utf-8")
    OUT_MD.write_text(rendered_md, encoding="utf-8")
    print(f"wrote RELEASE_READINESS verdict={state['verdict']}")
    for v in state.get("violations", []):
        print(f"  - {v}")
    return 0 if state["verdict"] == "READY" else 1


if __name__ == "__main__":
    sys.exit(main())
