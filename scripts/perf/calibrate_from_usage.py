"""Actual-usage 校准 CLI（R5，ADR-0213 D4）。

与 ``calibrate_governor.py``（synthetic corpus）互补：本脚本消费
**真实运行**积累的 ``CalibrationStore`` 快照，产出先验**建议**文件。

数据来源：``--snapshot PATH`` 读 ``CalibrationStore.snapshot()`` 的 JSON
文件（快照由测试/dev 进程产出；生产侧定期 dump 属 follow-up）。

产出（provisional 纪律，绝不自动应用）：
- 建议文件（默认 ``docs/dev/unified-cost-calibration-suggest.json``）：
  per (tool_key, dimension) 的 mean_ratio / suggested_factor / action；
  应用 = 人工评审后显式修改 ``governor/estimation.py`` 先验表或
  ``config/governor_budgets.json``，走 PR review。

Usage:
  python scripts/perf/calibrate_from_usage.py --snapshot snap.json
  python scripts/perf/calibrate_from_usage.py --snapshot snap.json --report out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.governor.calibration import suggest_priors  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True,
                        help="CalibrationStore.snapshot() JSON 文件")
    parser.add_argument("--report", default=None,
                        help="建议输出路径（缺省 docs/dev/ 下建议文件）")
    parser.add_argument("--min-samples", type=int, default=8,
                        help="产出建议的最小样本数（默认 8）")
    args = parser.parse_args()

    snap_path = Path(args.snapshot)
    if not snap_path.exists():
        print(f"snapshot not found: {snap_path}", file=sys.stderr)
        return 2
    try:
        snapshot = json.loads(snap_path.read_text())
    except (OSError, ValueError) as exc:
        print(f"invalid snapshot: {exc}", file=sys.stderr)
        return 2

    report = suggest_priors(snapshot, min_samples=args.min_samples)
    out_path = (Path(args.report) if args.report else
                REPO_ROOT / "docs/dev/unified-cost-calibration-suggest.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    suggestions = report.get("suggestions") or []
    print(f"wrote {out_path} ({len(suggestions)} suggestions, "
          f"drift_band={report.get('drift_band')}, "
          f"min_samples={report.get('min_samples')})")
    for s in suggestions[:20]:
        print(f"  {s['tool_key']} {s['dimension']}: "
              f"mean_ratio={s['mean_ratio']} -> {s['action']} "
              f"(n={s['n']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
