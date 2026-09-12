#!/usr/bin/env python
"""cartography lane 独立覆盖率闸（任务书 10 线 P4，ADR-0159）。

背景：CI 后端 lane 显式 ``-m "not cartography"``，75% 覆盖**不含制图代码**
（production.yml:202）。本闸把 cartography lane 的覆盖率**单独计量、单独设
下限**（默认 60%，与后端 75% 分开计），本地执行、可复现。

- scope：``app/lib/cartography``（harness 属 09 线，单独报告不单独设闸）；
- 首轮实测参考值（2026-09-13，origin/master 1fd4b035）：
  cartography=50.1% / harness=46.3%（合并 49.6%）；
- 按 §0.5「provisional 先行」：``--floor`` 默认 60，但首次落地以
  ``CARTO_COV_FLOOR`` 环境变量起步（本地闸先取 50），提升到 60 后收口；
  下限只升不降（ratchet 同纪律）。

用法::

    python scripts/coverage_cartography_gate.py                # 跑 lane + 断言下限
    CARTO_COV_FLOOR=50 python scripts/coverage_cartography_gate.py
    python scripts/coverage_cartography_gate.py --skip-tests --json cov.json
                                                               # 只算已有报告
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CARTOGRAPHY_PREFIX = "app/lib/cartography/"
HARNESS_PREFIX = "app/lib/harness/"
#: 下限只升不降：历史最高下限记录（本文件内即可审计）。
FLOOR_RATCHET_HISTORY = (50.0, 60.0)


def _norm(key: str) -> str:
    return key.replace("\\", "/")


def scope_percentages(report_json: Dict[str, Any]) -> Dict[str, Tuple[int, int, float]]:
    """coverage JSON → {scope: (stmts, covered, pct)}（cartography / harness）。"""
    agg: Dict[str, Tuple[int, int]] = {
        "cartography": (0, 0),
        "harness": (0, 0),
    }
    for key, payload in (report_json.get("files") or {}).items():
        normed = _norm(key)
        summary = payload.get("summary") or {}
        stmts = int(summary.get("num_statements") or 0)
        covered = int(summary.get("covered_lines") or 0)
        if normed.lower().startswith(CARTOGRAPHY_PREFIX) or normed.lower().startswith(
            "app\\lib\\cartography\\"
        ):
            scope = "cartography"
        elif normed.lower().startswith(HARNESS_PREFIX):
            scope = "harness"
        else:
            continue
        prev_s, prev_c = agg[scope]
        agg[scope] = (prev_s + stmts, prev_c + covered)
    out: Dict[str, Tuple[int, int, float]] = {}
    for scope, (stmts, covered) in agg.items():
        pct = (covered / stmts * 100.0) if stmts else 0.0
        out[scope] = (stmts, covered, round(pct, 2))
    return out


def run_lane(json_path: Path) -> int:
    """跑 cartography lane（-m cartography，独立计量源），产出 coverage JSON。"""
    cmd = [
        sys.executable, "-m", "pytest", "-m", "cartography",
        "-o", "addopts=",
        "--cov=app/lib/cartography", "--cov=app/lib/harness",
        f"--cov-report=json:{json_path}",
        "--cov-report=term-missing",
        "--timeout=180", "--timeout-method=thread",
        "-q", "-p", "no:cacheprovider",
    ]
    env = dict(os.environ)
    env.setdefault("JWT_SECRET_KEY", "test-secret-migration-32-chars-okay")
    env.setdefault("USE_REDIS", "false")
    print("[coverage-gate] pytest -m cartography（独立计量，addopts 覆盖）", flush=True)
    return subprocess.call(cmd, cwd=str(REPO_ROOT), env=env)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--floor", type=float, default=None,
                        help="cartography scope 下限（默认读 CARTO_COV_FLOOR，"
                             "再缺省 60.0）")
    parser.add_argument("--skip-tests", action="store_true",
                        help="不重跑 lane，只从 --json 现有报告计算")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="coverage JSON 路径（--skip-tests 时必填；"
                             "否则为产出位置）")
    args = parser.parse_args(argv)

    floor = args.floor
    if floor is None:
        env_floor = os.environ.get("CARTO_COV_FLOOR")
        floor = float(env_floor) if env_floor else 60.0

    json_path = Path(args.json_path) if args.json_path else (
        Path(tempfile.mkdtemp(prefix="ac10-cov-")) / "coverage.json"
    )
    if not args.skip_tests:
        # 覆盖率不足时 pytest 本身退出码非 0（无 --cov-fail-under 时仅当测试失败）；
        # 测试失败 = lane 红线，直接透传失败。
        rc = run_lane(json_path)
        if rc != 0:
            print(f"[coverage-gate] cartography lane 测试失败（exit={rc}）", flush=True)
            return rc
    if not json_path.exists():
        print(f"[coverage-gate] 找不到 coverage JSON：{json_path}", flush=True)
        return 2
    report = json.loads(json_path.read_text(encoding="utf-8"))
    scopes = scope_percentages(report)

    carto_stmts, carto_covered, carto_pct = scopes["cartography"]
    h_stmts, h_covered, h_pct = scopes["harness"]
    combined_s = carto_stmts + h_stmts
    combined_c = carto_covered + h_covered
    combined_pct = (combined_c / combined_s * 100.0) if combined_s else 0.0

    print("=" * 72)
    print("cartography lane 独立覆盖率（与后端 75% 分开计）")
    print(f"  app/lib/cartography : {carto_stmts:5} stmts, {carto_pct:6.2f}%   ← 闸 scope")
    print(f"  app/lib/harness     : {h_stmts:5} stmts, {h_pct:6.2f}%   （09 线，仅报告）")
    print(f"  合并参考            : {combined_s:5} stmts, {combined_pct:6.2f}%")
    print(f"  下限（ratchet 只升不降）: {floor:.1f}%")
    ok = carto_pct >= floor
    print(f"  判定: {'✅ 通过' if ok else '❌ 低于下限 —— 拦截'}")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
