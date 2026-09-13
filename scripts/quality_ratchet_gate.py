#!/usr/bin/env python
"""制图质量 ratchet 门禁 CLI（ADR-0159 P2）。

用法::

    # 首轮：从事实库最近 run 构建基线（provisional，只记录不拦截）
    python scripts/quality_ratchet_gate.py baseline

    # 基线确认后激活（才开始拦截）
    python scripts/quality_ratchet_gate.py baseline --activate

    # 门禁检查：劣化 → 退出码 1（waiver 未到期的放行并在报告中高亮）
    python scripts/quality_ratchet_gate.py check

    # 登记豁免（带理由 + 到期日，写入库，到期自动失效）
    python scripts/quality_ratchet_gate.py waive --check-id carto.load.ratio.load_ratio \
        --reason "upstream regression, fix in flight" --days 30 --by ac-10

    # 离线模式：从 run 观测 JSON 建基线（不依赖事实库）
    python scripts/quality_ratchet_gate.py baseline --from-json runs.json

无 CI 是硬前提：本脚本是本地门禁（``scripts/quality_gate_local.sh`` 的一步）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_observation_rows(path: str) -> List[Dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    # 接受两种形态：[{scene_id,check_id,value}, ...] 或
    # [{scene_id, metrics:[{check_id,value}, ...]}, ...]
    rows: List[Dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if "metrics" in item:
            scene = item.get("scene_id")
            for metric in item.get("metrics") or []:
                rows.append({
                    "scene_id": metric.get("scene_id") or scene,
                    "check_id": metric.get("check_id"),
                    "value": metric.get("value"),
                })
        else:
            rows.append({
                "scene_id": item.get("scene_id"),
                "check_id": item.get("check_id"),
                "value": item.get("value"),
            })
    return rows


def cmd_baseline(args: argparse.Namespace) -> int:
    from app.services import cartography_ratchet as ratchet
    from app.services.cartography_metrics_store import ensure_tables

    ensure_tables()
    if args.from_json:
        rows = _load_observation_rows(args.from_json)
    else:
        rows = ratchet.collect_observation_rows_sync(
            lanes=tuple(args.lanes.split(",")), since_runs=args.since_runs,
        )
    entries = ratchet.build_baseline_entries(
        rows, quantile=args.quantile, tolerance_pct=args.tolerance,
    )
    if not entries:
        print("没有可用观测：不写基线（诚实缺省，绝不伪造）。")
        return 1
    status = "active" if args.activate else "provisional"
    written = ratchet.write_baselines_sync(
        entries, status=status, source=args.source,
    )
    print(f"写入 {written} 条基线（status={status}, quantile=p{int(args.quantile*100)}, "
          f"tolerance=±{args.tolerance}%）")
    for entry in entries:
        print(f"  {entry.scene_id:24} {entry.check_id:56} "
              f"value={entry.value:.4f} n={entry.sample_n} dir={entry.direction}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from app.services import cartography_ratchet as ratchet
    from app.services.cartography_metrics_store import ensure_tables

    ensure_tables()
    if args.from_json:
        rows = _load_observation_rows(args.from_json)
    else:
        rows = ratchet.collect_observation_rows_sync(
            lanes=tuple(args.lanes.split(",")), since_runs=args.since_runs,
        )
    observations = ratchet.aggregate_observations(rows, quantile=args.quantile)
    baselines = ratchet.load_baselines_sync()
    waivers = ratchet.load_active_waivers_sync()
    violations = ratchet.evaluate_ratchet(
        observations, baselines, waivers=waivers, tolerance_pct=args.tolerance,
    )
    blocked = [v for v in violations if not v.waived]
    waived = [v for v in violations if v.waived]

    print("=" * 88)
    print(f"ratchet check：观测 {len(observations)} 条（聚合后），"
          f"基线 {len(baselines)} 条（active {sum(1 for b in baselines if b.status == 'active')}），"
          f"豁免 {len(waivers)} 条")
    if blocked:
        print(f"\n❌ RATCHET 拦截（{len(blocked)} 条劣化超容差）：")
        for v in blocked:
            print(f"  {v.scene_id:24} {v.check_id:56} "
                  f"baseline={v.baseline_value:.4f} observed={v.observed_value:.4f} "
                  f"劣化 {v.delta_pct:+.1f}% ({v.direction})")
    if waived:
        print(f"\n⚠️ WAIVED（{len(waived)} 条命中豁免——报告高亮项，到期自动恢复拦截）：")
        for v in waived:
            print(f"  {v.scene_id:24} {v.check_id:56} "
                  f"observed={v.observed_value:.4f} 劣化 {v.delta_pct:+.1f}% "
                  f"理由: {v.waiver_reason[:60]}")
    if not violations:
        print("\n✅ 无劣化：全部观测不劣于基线（或无 active 基线可比）。")
    print("=" * 88)
    if args.report_json:
        Path(args.report_json).write_text(json.dumps({
            "observations": len(observations),
            "blocked": [v.to_dict() for v in blocked],
            "waived": [v.to_dict() for v in waived],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if blocked else 0


def cmd_waive(args: argparse.Namespace) -> int:
    from app.services import cartography_ratchet as ratchet
    from app.services.cartography_metrics_store import ensure_tables

    ensure_tables()
    ok = ratchet.add_waiver_sync(
        check_id=args.check_id,
        reason=args.reason,
        days=args.days,
        scene_id=args.scene,
        created_by=args.by,
    )
    if not ok:
        print("豁免写入失败（事实库不可用？）", file=sys.stderr)
        return 1
    print(f"已登记豁免：{args.check_id}（{args.scene}）{args.days} 天后到期，"
          f"理由：{args.reason}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_base = sub.add_parser("baseline", help="从事实库/JSON 构建基线")
    p_base.add_argument("--from-json", help="离线观测行 JSON（默认读事实库）")
    p_base.add_argument("--lanes", default="desired_state,runtime")
    p_base.add_argument("--since-runs", type=int, default=60)
    p_base.add_argument("--quantile", type=float, default=0.66)
    p_base.add_argument("--tolerance", type=float, default=5.0)
    p_base.add_argument("--activate", action="store_true",
                        help="写入即 active（默认 provisional 只记录）")
    p_base.add_argument("--source", default="first_run")
    p_base.set_defaults(func=cmd_baseline)

    p_check = sub.add_parser("check", help="ratchet 门禁检查（劣化退出码 1）")
    p_check.add_argument("--from-json", help="离线观测行 JSON（默认读事实库）")
    p_check.add_argument("--lanes", default="desired_state,runtime")
    p_check.add_argument("--since-runs", type=int, default=60)
    p_check.add_argument("--quantile", type=float, default=0.66)
    p_check.add_argument("--tolerance", type=float, default=5.0)
    p_check.add_argument("--report-json", help="把裁决结果写入 JSON 文件")
    p_check.set_defaults(func=cmd_check)

    p_waive = sub.add_parser("waive", help="登记豁免（理由 + 到期日）")
    p_waive.add_argument("--check-id", required=True)
    p_waive.add_argument("--reason", required=True)
    p_waive.add_argument("--days", type=int, default=30)
    p_waive.add_argument("--scene", default="*")
    p_waive.add_argument("--by", default="")
    p_waive.set_defaults(func=cmd_waive)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
