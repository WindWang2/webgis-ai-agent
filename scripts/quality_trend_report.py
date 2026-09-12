#!/usr/bin/env python
"""制图质量趋势报告（任务书 10 线 P7，ADR-0159）。

按检查项输出最近 N 次 run 的趋势（文本表 + CSV），并可自动填充常驻看板
``docs/dev/ac-10-quality-dashboard.md`` 的数值段（标记块之间，散文不动）。

用法::

    python scripts/quality_trend_report.py --last 10
    python scripts/quality_trend_report.py --last 30 --csv docs/dev/ac-10-trend.csv
    python scripts/quality_trend_report.py --dashboard docs/dev/ac-10-quality-dashboard.md

顶层六项指标（无数据诚实报 ``未测量``，绝不报 0）：
全自动率 / 一次成功率 / 自愈成功率 / 一致性 / 出版就绪 / 回归守护。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DASHBOARD_BEGIN = "<!-- AC10:AUTO:BEGIN（脚本生成段，勿手改） -->"
DASHBOARD_END = "<!-- AC10:AUTO:END -->"


def _fmt_ts(ts: Any) -> str:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.strftime("%m-%d %H:%M")
    return str(ts)[:16] if ts else ""


def collect_rows() -> List[Dict[str, Any]]:
    from app.services.cartography_metrics_store import latest_runs

    runs = asyncio.run(latest_runs(500))
    rows: List[Dict[str, Any]] = []
    for run in runs:
        for metric in run.get("metrics") or []:
            if metric.get("value") is None:
                continue
            rows.append({
                "ts": run.get("ts"),
                "lane": run.get("lane"),
                "scene_id": run.get("scene_id") or "",
                "check_id": metric.get("check_id"),
                "value": metric.get("value"),
                "verdict": metric.get("verdict"),
            })
    return rows


def group_trends(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["check_id"], []).append(row)
    # latest_runs 是新→旧；趋势表统一旧→新。
    return {k: list(reversed(v)) for k, v in sorted(grouped.items())}


def render_text(grouped: Dict[str, List[Dict[str, Any]]], last: int) -> str:
    lines: List[str] = []
    lines.append(f"制图质量趋势（最近 {last} 次 run，按检查项；旧→新）")
    lines.append("=" * 92)
    lines.append(f"{'check_id':52} {'n':>3} {'first':>10} {'last':>10} "
                 f"{'min':>10} {'max':>10} {'lane':13} trend")
    for check_id, series in grouped.items():
        window = series[-last * 5:]
        values = [r["value"] for r in window]
        first, latest = values[0], values[-1]
        direction = "→" if len(values) < 2 else (
            "↑" if latest > first else "↓" if latest < first else "＝")
        lanes = {r["lane"] for r in window}
        lines.append(
            f"{check_id[:52]:52} {len(values):>3} {first:>10.4g} {latest:>10.4g} "
            f"{min(values):>10.4g} {max(values):>10.4g} "
            f"{'/'.join(sorted(lanes))[:13]:13} {direction}"
        )
    return "\n".join(lines)


def write_csv(grouped: Dict[str, List[Dict[str, Any]]], last: int, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["check_id", "ts", "lane", "scene_id", "value", "verdict"])
        for _check_id, series in grouped.items():
            for row in series[-last * 5:]:
                ts = row["ts"]
                writer.writerow([
                    row["check_id"],
                    ts.isoformat() if isinstance(ts, datetime) else ts,
                    row["lane"], row["scene_id"], row["value"], row["verdict"],
                ])


# ── 顶层六项指标（看板同源） ──────────────────────────────────────────────


def top_level_metrics() -> Dict[str, str]:
    from app.services.cartography_metrics_store import latest_runs

    runs = asyncio.run(latest_runs(500))
    if not runs:
        return {k: "未测量" for k in (
            "全自动率", "一次成功率", "自愈成功率", "一致性", "出版就绪", "回归守护",
        )}

    def _summary_bool(run: Dict[str, Any], key: str) -> Any:
        summary = run.get("summary") or {}
        return summary.get(key)

    def _repair_attempts(run: Dict[str, Any]) -> int:
        summary = run.get("summary") or {}
        value = summary.get("repair_attempts")
        return int(value) if isinstance(value, (int, float)) else 0

    passed_runs = [r for r in runs if r.get("passed") is True]
    desired = [r for r in runs if r["lane"] == "desired_state"]
    runtime = [r for r in runs if r["lane"] == "runtime"]

    # 全自动率：无人工介入终态（passed）占全部有判定 run 的比例。
    judged = [r for r in runs if isinstance(r.get("passed"), bool)]
    auto_rate = f"{len(passed_runs) / len(judged) * 100:.1f}%" if judged else "未测量"

    # 一次成功率：desired_state 通过且零修复尝试。
    first_pass = [
        r for r in desired
        if r.get("passed") is True and _repair_attempts(r) == 0
    ]
    once_rate = (
        f"{len(first_pass) / len(desired) * 100:.1f}% (n={len(desired)})"
        if desired else "未测量"
    )

    # 自愈成功率：runtime 有修复尝试且终态通过 / runtime 有修复尝试。
    healed = [
        r for r in runtime
        if _repair_attempts(r) > 0 and r.get("passed") is True
    ]
    attempted = [r for r in runtime if _repair_attempts(r) > 0]
    heal_rate = (
        f"{len(healed) / len(attempted) * 100:.1f}% (n={len(attempted)})"
        if attempted else "未测量"
    )

    # 一致性：golden lane 通过率。
    golden = [r for r in runs if r["lane"] == "golden"]
    golden_ok = [r for r in golden if r.get("passed") is True]
    consistency = (
        f"{len(golden_ok) / len(golden) * 100:.1f}% (n={len(golden)})"
        if golden else "未测量"
    )

    # 出版就绪：08 线（publish/export）指标入账后才有数 —— 诚实缺省。
    publish = [r for r in runs if r.get("source") == "publish"]
    publish_rate = (
        f"{sum(1 for r in publish if r.get('passed')) / len(publish) * 100:.1f}% "
        f"(n={len(publish)})" if publish else "未测量"
    )

    return {
        "全自动率": auto_rate,
        "一次成功率": once_rate,
        "自愈成功率": heal_rate,
        "一致性": consistency,
        "出版就绪": publish_rate,
    }


async def _regression_guard() -> str:
    from app.services import cartography_ratchet as ratchet

    baselines = await ratchet.load_baselines()
    active = [b for b in baselines if b.status == "active"]
    rows = await ratchet.collect_observation_rows()
    observations = ratchet.aggregate_observations(rows)
    waivers = await ratchet.load_active_waivers()
    violations = ratchet.evaluate_ratchet(observations, baselines, waivers=waivers)
    blocked = [v for v in violations if not v.waived]
    if not active:
        return "未测量（无 active 基线）"
    status = "拦截中" if blocked else "全绿"
    return (
        f"{status}：active 基线 {len(active)} 条，劣化 {len(blocked)} 条"
        f"（waived {len(violations) - len(blocked)}）"
    )


def render_dashboard_block(last: int) -> str:
    metrics = top_level_metrics()
    metrics["回归守护"] = asyncio.run(_regression_guard())
    rows = collect_rows()
    grouped = group_trends(rows)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: List[str] = [DASHBOARD_BEGIN, "", f"### 数值段（脚本自动生成 @ {stamp}）", ""]
    lines.append("#### 顶层六项指标")
    lines.append("")
    lines.append("| 指标 | 当前值 |")
    lines.append("|---|---|")
    for key in ("全自动率", "一次成功率", "自愈成功率", "一致性", "出版就绪", "回归守护"):
        lines.append(f"| {key} | {metrics[key]} |")
    lines.append("")
    lines.append(f"#### 检查项趋势（最近 {last} 次 run，旧→新）")
    lines.append("")
    lines.append("```")
    lines.append(render_text(grouped, last))
    lines.append("```")
    lines.append("")
    lines.append(DASHBOARD_END)
    return "\n".join(lines)


def fill_dashboard(path: Path, last: int) -> None:
    block = render_dashboard_block(last)
    if path.exists():
        content = path.read_text(encoding="utf-8")
        begin = content.find(DASHBOARD_BEGIN)
        end = content.find(DASHBOARD_END)
        if begin != -1 and end != -1:
            content = content[:begin] + block + content[end + len(DASHBOARD_END):]
        else:
            content = content.rstrip() + "\n\n" + block + "\n"
    else:
        content = (
            "# AC-10 制图质量常驻看板\n\n"
            "> 验收总纲见任务书 §4/§5；本页数值段由 "
            "`scripts/quality_trend_report.py --dashboard` 自动填充。\n\n" + block + "\n"
        )
    path.write_text(content, encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--last", type=int, default=10)
    parser.add_argument("--check", default=None, help="只看指定检查项前缀")
    parser.add_argument("--csv", dest="csv_path", default=None)
    parser.add_argument("--dashboard", dest="dashboard_path", default=None)
    args = parser.parse_args(argv)

    rows = collect_rows()
    grouped = group_trends(rows)
    if args.check:
        grouped = {
            k: v for k, v in grouped.items() if k.startswith(args.check)
        }
        if not grouped:
            print(f"没有匹配 {args.check} 的观测。", file=sys.stderr)
            return 1
    print(render_text(grouped, args.last))
    if args.csv_path:
        write_csv(grouped, args.last, Path(args.csv_path))
        print(f"\nCSV 已写入 {args.csv_path}")
    if args.dashboard_path:
        fill_dashboard(Path(args.dashboard_path), args.last)
        print(f"看板数值段已更新 {args.dashboard_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
