#!/usr/bin/env python
"""Quality Runner（ADR-0104 Wave 19）——本地一键质量车道。

不引入新 build system；车道 = 既有 pytest marker/目录 + 前端 npm scripts 的
受控封装。资源纪律：车道串行（无 xdist；本仓库未装 xdist，且全量并发会
OOM），pytest 自带 timeout 兜底；frontend lane 受 pnpm 自身并发控制。

用法：
    python scripts/quality_runner.py quick                 # 快反馈（quality 闸 + 漂移）
    python scripts/quality_runner.py backend               # 后端主车道（--cov）
    python scripts/quality_runner.py frontend              # vitest + typecheck
    python scripts/quality_runner.py science               # 科学 oracle replay
    python scripts/quality_runner.py cartography           # 制图闭环闸
    python scripts/quality_runner.py data                  # 数据面
    python scripts/quality_runner.py security              # 安全回归
    python scripts/quality_runner.py quality               # tests/quality 全部红线
    python scripts/quality_runner.py full                  # 以上全部（串行）
    python scripts/quality_runner.py full --retry-failed   # 失败车道重试（--lf）

输出：
    .agent-work/quality-v1/runner-report.json   机器可读
    .agent-work/quality-v1/runner-report.md     人可读
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO / ".agent-work" / "quality-v1"
PYTEST = [sys.executable, "-m", "pytest"]

#: 后端车道默认资源护栏（bounded；不用 -n auto）
PYTEST_GUARDS = ["--timeout=120", "--timeout-method=thread", "-p", "no:cacheprovider"]

LANES: dict[str, dict] = {
    "quick": {
        "title": "quick（质量红线 + 漂移 + 生成物一致性）",
        "commands": [
            PYTEST + ["tests/quality/", "--no-cov", "-q", "--timeout=60",
                      "--timeout-method=thread", "-p", "no:cacheprovider"],
            [sys.executable, "scripts/gen_quality_manifest.py", "--check"],
            [sys.executable, "scripts/gen_drift_report.py", "--check"],
            [sys.executable, "scripts/gen_trace_certification.py", "--check"],
            [sys.executable, "scripts/gen_resource_certification.py", "--check"],
            [sys.executable, "scripts/gen_determinism_certification.py", "--check"],
            [sys.executable, "scripts/gen_quality_report.py", "--check"],
        ],
    },
    "backend": {
        "title": "backend（后端主车道，--cov 门；与 production.yml test-backend 同口径）",
        "commands": [
            PYTEST + ["-m", "not perf and not cartography and not real_services",
                      "--cov=app", "--cov-report=term-missing", "--cov-fail-under=75",
                      "--timeout=120", "--timeout-method=thread", "-q"],
        ],
    },
    "frontend": {
        "title": "frontend（vitest + typecheck）",
        "commands": [
            ["pnpm", "--dir", "frontend", "test"],
            ["pnpm", "--dir", "frontend", "typecheck"],
        ],
    },
    "science": {
        "title": "science（科学 oracle replay + GIS 单测）",
        "commands": [
            PYTEST + ["tests/science_oracles/", "tests/unit/lib/",
                      "--no-cov", "-q", "--timeout=120", "--timeout-method=thread",
                      "-p", "no:cacheprovider"],
        ],
    },
    "cartography": {
        "title": "cartography（制图闭环 + golden）",
        "commands": [
            PYTEST + ["-m", "cartography", "--no-cov", "-q",
                      "--timeout=120", "--timeout-method=thread", "-p", "no:cacheprovider"],
        ],
    },
    "data": {
        "title": "data（数据面 + fabric）",
        "commands": [
            PYTEST + ["tests/data/", "tests/unit/test_data_runtime_v2.py",
                      "--no-cov", "-q", "--timeout=120", "--timeout-method=thread",
                      "-p", "no:cacheprovider"],
        ],
    },
    "security": {
        "title": "security（安全回归 + auth）",
        "commands": [
            PYTEST + ["tests/quality/test_security_regression.py",
                      "tests/test_auth.py", "tests/test_api_auth_required.py",
                      "tests/test_auth_bypass.py", "tests/test_api_path_traversal.py",
                      "--no-cov", "-q", "--timeout=120", "--timeout-method=thread",
                      "-p", "no:cacheprovider"],
        ],
    },
    "quality": {
        "title": "quality（tests/quality 红线全量）",
        "commands": [
            PYTEST + ["tests/quality/", "--no-cov", "-q", "--timeout=60",
                      "--timeout-method=thread", "-p", "no:cacheprovider"],
        ],
    },
    "perf": {
        "title": "perf（结构+墙钟基线车道；隔离执行，勿与全量混跑）",
        "commands": [
            PYTEST + _perf_lane_files() + [
                "-m", "perf", "--no-cov", "-q",
                "--timeout=180", "--timeout-method=thread", "-p", "no:cacheprovider"],
        ],
    },
}

FULL_ORDER = ["quick", "science", "cartography", "data", "security",
              "quality", "backend", "frontend"]  # perf 单独跑（隔离策略 #664）


def _perf_lane_files() -> list:
    """R1 review MINOR：perf 车道清单单一来源化 —— 扫描 perf 标记文件，
    与 CI 契约测试（test_every_perf_marked_file_is_wired_into_a_lane）
    同一判定口径，本地与 CI 不漂移。"""
    files = []
    for pattern_dir in ("tests/benchmarks", "tests/perf"):
        for p in sorted((REPO / pattern_dir).glob("test_*.py")):
            src = p.read_text(encoding="utf-8")
            if "pytestmark = pytest.mark.perf" in src or "@pytest.mark.perf" in src:
                files.append(str(p.relative_to(REPO)))
    return files


def _run_lane(lane: str, retry_failed: bool) -> dict:
    spec = LANES[lane]
    results = []
    for cmd in spec["commands"]:
        if retry_failed and cmd[0] == sys.executable and "pytest" in cmd[1:3]:
            # R1 review MINOR：--lf 依赖 cacheprovider —— 重试命令必须去掉
            # no:cacheprovider 否则参数冲突
            cmd = [c for c in cmd if c != "-p" and c != "no:cacheprovider"] \
                if False else cmd
            filtered = []
            skip_next = False
            for c in cmd:
                if skip_next:
                    skip_next = False
                    continue
                if c == "-p":
                    skip_next = True
                    continue
                filtered.append(c)
            cmd = [*filtered, "--lf", "-q"]
        t0 = time.monotonic()
        proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
        elapsed = time.monotonic() - t0
        tail = "\n".join((proc.stdout or "").splitlines()[-12:])
        results.append({
            "command": " ".join(str(c) for c in cmd),
            "exit": proc.returncode,
            "elapsed_s": round(elapsed, 1),
            "tail": tail,
        })
    ok = all(r["exit"] == 0 for r in results)
    return {"lane": lane, "title": spec["title"], "ok": ok, "steps": results}


def _render_md(report: dict) -> str:
    lines = ["# Local Quality Run（runner 生成）", ""]
    lines.append(f"- 车道：{report['lanes_run']}")
    verdict = "PASS" if report["all_ok"] else "FAIL"
    lines.append(f"- 总判定：**{verdict}**（总耗时 {report['total_s']}s）")
    lines.append("")
    for lane in report["lanes"]:
        mark = "PASS" if lane["ok"] else "FAIL"
        lines.append(f"## {lane['lane']} — {mark}（{lane['title']}）")
        lines.append("")
        for step in lane["steps"]:
            lines.append(f"- `{' '.join(step['command'].split()[:6])} …` → "
                         f"exit={step['exit']}（{step['elapsed_s']}s）")
            if step["exit"] != 0:
                lines.append("")
                lines.append("```")
                lines.extend(step["tail"].splitlines()[-10:])
                lines.append("```")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=[*LANES.keys(), "full"])
    parser.add_argument("--retry-failed", action="store_true",
                        help="失败车道用 pytest --lf 重试")
    parser.add_argument("--json", action="store_true", help="只打印 JSON 摘要")
    args = parser.parse_args()

    lanes = FULL_ORDER if args.lane == "full" else [args.lane]
    started = time.monotonic()
    lane_reports = []
    for lane in lanes:
        report = _run_lane(lane, args.retry_failed)
        lane_reports.append(report)
        mark = "ok" if report["ok"] else "FAIL"
        print(f"[{mark}] {lane} ({report['steps'][0]['elapsed_s']}s+)", flush=True)
        if not report["ok"] and args.lane != "full":
            break  # 单车道模式遇错即停（full 会跑完出完整报告）
    total = round(time.monotonic() - started, 1)

    report = {
        "lane": args.lane,
        "lanes_run": lanes,
        "all_ok": all(r["ok"] for r in lane_reports),
        "total_s": total,
        "lanes": lane_reports,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "runner-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (REPORT_DIR / "runner-report.md").write_text(_render_md(report), encoding="utf-8")
    if args.json:
        print(json.dumps({k: report[k] for k in ("lane", "all_ok", "total_s")},
                         ensure_ascii=False))
    else:
        print(f"report: {REPORT_DIR / 'runner-report.md'}")
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
