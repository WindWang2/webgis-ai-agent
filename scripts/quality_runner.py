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

Quality V2 profiles（分层；报告与 flake 统计写 .agent-work/quality-v2/）：
    python scripts/quality_runner.py changed              # git diff 驱动的受影响面 + 顺序轮换
    python scripts/quality_runner.py full-local           # 全量本地验收（含顺序 seed 轮换；perf 隔离）

输出：
    .agent-work/quality-v1/runner-report.json   机器可读
    .agent-work/quality-v1/runner-report.md     人可读
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO / ".agent-work" / "quality-v2"
PYTEST = [sys.executable, "-m", "pytest"]

#: 后端车道默认资源护栏（bounded；不用 -n auto）
PYTEST_GUARDS = ["--timeout=120", "--timeout-method=thread", "-p", "no:cacheprovider"]

def _perf_lane_files() -> list:
    """R1 review MINOR：perf 车道清单单一来源化 —— 扫描 perf 标记文件，
    与 CI 契约测试（test_every_perf_marked_file_is_wired_into_a_lane）
    同一判定口径，本地与 CI 不漂移。"""
    # nightly 专属文件（event-loop lag / 大规模 MapSpec）排除 —— 与
    # test_ci_perf_coverage_contract 的 NIGHTLY_ONLY_PERF_FILES 口径一致，
    # 本地 perf 车道不跑会撞 180s 预算的墙钟子集。
    nightly_only = {"test_perf_harness_v2.py", "test_perf_mapspec_e2e.py"}
    files = []
    for pattern_dir in ("tests/benchmarks", "tests/perf"):
        for p in sorted((REPO / pattern_dir).glob("test_*.py")):
            if p.name in nightly_only:
                continue
            src = p.read_text(encoding="utf-8")
            if "pytestmark = pytest.mark.perf" in src or "@pytest.mark.perf" in src:
                files.append(str(p.relative_to(REPO)))
    return files


LANES: dict[str, dict] = {
    "quick": {
        "title": "quick（质量红线 + 漂移 + 生成物一致性）",
        "commands": [
            PYTEST + ["tests/quality/", "--no-cov", "-q", "--timeout=60",
                      "--timeout-method=thread", "-p", "no:cacheprovider"],
            # R2-M2 去重：manifest/drift 字节闸由 readiness --check 内部
            # 执行（LIVE_GATES），不再直接挂载（省 ~9s 双跑）；readiness
            # 渲染含各闸状态，任一闸漂移即 --check 红，强制力等价。
            [sys.executable, "scripts/gen_trace_certification.py", "--check"],
            [sys.executable, "scripts/gen_resource_certification.py", "--check"],
            [sys.executable, "scripts/gen_determinism_certification.py", "--check"],
            [sys.executable, "scripts/gen_quality_report.py", "--check"],
            [sys.executable, "scripts/check_generated_staleness.py"],
            # Quality V3（Epic 10）：协调闸强制点（ADR watermark / migration
            # 多头 / ownership parity / 生成物 staleness 聚合报告）
            [sys.executable, "scripts/check_integration_preflight.py"],
            # Quality V3 W14：前端行为证据索引字节闸（@behavior 标签漂移即红）
            [sys.executable, "scripts/gen_frontend_behavior.py", "--check"],
            # Quality V3 W15/R1-C1：release readiness 字节闸（内容性状态
            # 锁定；commit 身份字段归一；内含 manifest/drift/preflight/
            # frontend-behavior 四闸执行）
            [sys.executable, "scripts/gen_release_readiness.py", "--check"],
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
            PYTEST + ["tests/data/",
                      "tests/unit/test_data_fabric_adapters.py",
                      "tests/unit/test_data_fabric_contract.py",
                      "tests/unit/test_data_fabric_registry.py",
                      "tests/unit/test_data_fabric_routes.py",
                      "tests/unit/test_data_fabric_resource_guards.py",
                      "tests/unit/test_data_fabric_reliability.py",
                      "tests/unit/test_data_fabric_fault_injection.py",
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


def _changed_py_targets() -> list:
    """git diff（工作区 + 最近一次 merge-base 与 origin/master）驱动的
    受影响测试目标：改动 app/ 下某目录 → 对应 tests 目录 + tests/quality
    红线恒跑；改动 tests/ → 原样跑。返回 pytest 路径参数列表（有界）。"""
    import subprocess

    def _git(args: list) -> str:
        return subprocess.run(["git"] + args, cwd=REPO, capture_output=True,
                              text=True, timeout=30).stdout

    files = set(_git(["diff", "--name-only", "HEAD"]).splitlines())
    files |= set(_git(["diff", "--name-only", "origin/master...HEAD"]
                      ).splitlines())
    targets: list = []
    # #1216（audit3 D-5）：前缀匹配（按特异性降序）—— 此前
    # `"/".join(parts[:3])` 对两段键（app/tools、app/services、app/api、
    # app/core）永不匹配，主应用面的改动在 changed lane 静默失去测试映射。
    prefix_map = [
        ("app/lib/gis", "tests/unit/gis"),
        ("app/lib/quality", "tests/quality"),
        ("app/tools", "tests/unit/tools"),
        ("app/services", "tests/unit"),
        ("app/api", "tests"),
        ("app/core", "tests"),
    ]
    for f in sorted(files):
        if f.startswith("tests/") and f.endswith(".py"):
            targets.append(f)
        elif f.startswith("app/"):
            for prefix, mapped in prefix_map:
                if f == prefix or f.startswith(prefix + "/"):
                    if (REPO / mapped).is_dir():
                        targets.append(mapped)
                    break
    # 顺序污染轮换：changed profile 恒定启用（Quality V2 W10）
    os.environ.setdefault("QUALITY_ORDER_SEED", str(int(time.time()) % 100000))
    # 红线闸恒跑 + 去重有界
    base = ["tests/quality/", "tests/unit/tools/"]
    seen: set = set()
    ordered = []
    for t in [*base, *targets]:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered[:40]


def _run_lane(lane: str, retry_failed: bool) -> dict:
    spec = LANES[lane]
    results = []
    for cmd in spec["commands"]:
        t0 = time.monotonic()
        proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
        elapsed = time.monotonic() - t0
        tail = "\n".join((proc.stdout or "").splitlines()[-12:])
        step = {
            "command": " ".join(str(c) for c in cmd),
            "exit": proc.returncode,
            "elapsed_s": round(elapsed, 1),
            "tail": tail,
        }
        # Quality V2 flake 统计：仅在显式 --retry-failed 时，失败的 pytest
        # 步骤用 --lf 重跑一次；重跑全绿 = 顺序/负载敏感的 flake 候选
        # （recovered=true 计入报告，不改变车道判定口径——ok 仍按首次）。
        if retry_failed and proc.returncode != 0 \
                and cmd[0] == sys.executable and "pytest" in cmd[1:3]:
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
            retry_cmd = [*filtered, "--lf", "-q"]
            t1 = time.monotonic()
            retry_proc = subprocess.run(retry_cmd, cwd=REPO,
                                        capture_output=True, text=True)
            step["recovered"] = retry_proc.returncode == 0
            step["retry_tail"] = "\n".join(
                (retry_proc.stdout or "").splitlines()[-12:])
            step["retry_elapsed_s"] = round(time.monotonic() - t1, 1)
        results.append(step)
    ok = all(r["exit"] == 0 for r in results)
    flake_recovered = sum(1 for r in results if r.get("recovered"))
    return {"lane": lane, "title": spec["title"], "ok": ok,
            "flake_recovered": flake_recovered, "steps": results}


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
    parser.add_argument("lane", choices=[*LANES.keys(), "full",
                                         "changed", "full-local",
                                         "impact", "integration", "real"])
    parser.add_argument("--retry-failed", action="store_true",
                        help="失败车道用 pytest --lf 重试")
    parser.add_argument("--json", action="store_true", help="只打印 JSON 摘要")
    args = parser.parse_args()

    if args.lane == "impact":
        # Quality V3 W7/W9：import 图驱动的最小可靠测试面（完备性护栏；
        # 不替代 domain full tests —— 只用于高效集成复测）
        sys.path.insert(0, str(REPO))
        from app.lib.integration.impact import build_graph, select_tests
        from app.lib.integration.manifest import changed_files

        files = changed_files("HEAD", "origin/master", REPO,
                              include_worktree=True)
        app_files = [f for f in files if f.startswith("app/")]
        graph = build_graph(REPO, use_cache=True)
        result = select_tests(app_files, repo_root=REPO, graph=graph,
                              max_targets=80)
        if not result.complete:
            print("UNCOVERED（完备性护栏触发，--allow-uncovered 可豁免）:",
                  file=sys.stderr)
            for f in result.uncovered:
                print(f"  - {f}", file=sys.stderr)
            if not os.environ.get("ALLOW_UNCOVERED"):
                return 2
        targets = result.targets or ["tests/quality/"]
        LANES["impact"] = {
            "title": f"impact（import 闭包选择面 {len(targets)} 目标；"
                     f"映射兜底 {len(result.via_mapping)}；"
                     "改动的 tests/** 文件不在本选择面 —— 由 V2 changed/"
                     "full lane 兜底）",
            "commands": [
                PYTEST + targets + ["--no-cov", "-q", "--timeout=120",
                                    "--timeout-method=thread",
                                    "-p", "no:cacheprovider"],
            ],
        }
        lanes = ["impact"]
    elif args.lane == "integration":
        # Quality V3 W9：协调闸一条龙（preflight 已含在 quick；此 lane
        # 额外生成本分支 manifest，供 merge-sim / release readiness 消费）
        LANES["integration"] = {
            "title": "integration（协调闸 + 分支 manifest）",
            "commands": [
                [sys.executable, "scripts/check_integration_preflight.py"],
                [sys.executable, "scripts/gen_integration_manifest.py",
                 "--branch", "HEAD", "--include-worktree"],
            ],
        }
        lanes = ["integration"]
    elif args.lane == "real":
        # Quality V3 W12：real-services lane（opt-in）。REAL_SERVICES=1
        # 才武装 pytest 的 real_services marker；migration 生命周期
        # （SQLite 恒跑 + PG opt-in）+ 真实 Redis/PG 探测 + 多进程
        # harness（含 API 重启 chaos）。**不会**替你启动 docker 服务 ——
        # 服务由 compose/本机预先起好，本 lane 只做可达性探测后运行。
        if not os.environ.get("REAL_SERVICES") and \
                not os.environ.get("TEST_POSTGRES_URL"):
            print("real lane 需要 REAL_SERVICES=1 或 TEST_POSTGRES_URL "
                  "（opt-in 资源纪律）；默认 quick lane 不受影响。")
            return 2
        real_targets = ["tests/integration/test_migration_lifecycle.py",
                        "tests/integration/test_real_services_lane.py",
                        "tests/quality/test_storage_differential.py"]
        harness_cmd = [sys.executable, "scripts/integration_harness.py",
                       "--port", os.environ.get("HARNESS_PORT", "8901"),
                       "--workers", "2", "--chaos"]
        LANES["real"] = {
            "title": "real（opt-in：真实服务 + migration 生命周期 + 多进程 harness chaos）",
            "commands": [
                PYTEST + real_targets + ["--no-cov", "-q", "--timeout=300",
                                         "--timeout-method=thread",
                                         "-p", "no:cacheprovider"],
                harness_cmd,
            ],
        }
        lanes = ["real"]
    elif args.lane == "changed":
        # changed profile：受影响面 pytest + 红线再生成检查（顺序轮换已在
        # _changed_py_targets 内启用）
        targets = _changed_py_targets()
        LANES["changed"] = {
            "title": "changed（git diff 驱动受影响面 + 顺序轮换 "
                     f"QUALITY_ORDER_SEED={os.environ['QUALITY_ORDER_SEED']}）",
            "commands": [
                PYTEST + targets + ["--no-cov", "-q", "--timeout=120",
                                    "--timeout-method=thread",
                                    "-p", "no:cacheprovider"],
                [sys.executable, "scripts/gen_quality_manifest.py", "--check"],
            ],
        }
        lanes = ["changed"]
    elif args.lane == "full-local":
        # 全量本地验收：完整车道 + 顺序轮换 seed（每轮不同 → 连跑两轮
        # 不同序，暴露顺序污染）；perf 仍隔离。
        os.environ.setdefault("QUALITY_ORDER_SEED",
                              str(int(time.time()) % 100000))
        lanes = FULL_ORDER
    else:
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
