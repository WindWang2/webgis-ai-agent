#!/usr/bin/env python
"""校准制图规则阈值（spec 开放问题 1 的落点）。

输入是系统自身已经产出的证据——``_cartographic_review`` / 质量评审结果的
JSON（含 ``checks`` 数组，规则 evidence 里有 ``load_ratio`` /
``min_adjacent_delta_e`` / ``label_ink_ratio`` / ``avg_feature_area_px`` /
``encoded_field_count`` 等观测值）。来源不限：eval 套件的 ``report.json``
导出、``session_data_manager.get_map_state(sid)["_cartographic_review"]`` 的
批量导出、或手工构造的评审样本。

用法::

    # 单个/多个评审 JSON（dry-run 默认：只打印建议，绝不写任何东西）
    python scripts/calibrate_cartography_thresholds.py run1.json run2.json

    # 目录（递归收集 *.json）
    python scripts/calibrate_cartography_thresholds.py eval-runs/

    # stdin
    cat review.json | python scripts/calibrate_cartography_thresholds.py -

    # 把建议值作为基线入库（ADR-0159 P6：显式 --write 才落库）
    python scripts/calibrate_cartography_thresholds.py eval-runs/ --write \
        --diff-out docs/dev/ac-10-calibration-diff.json

输出：每个指标在实测样本上的分布（n/最小/中位/最大）与按分位数建议的
warn/fail 阈值，以及可直接粘贴进 ``.env`` 的配置行。

哲学（与 ADR-0069 一致）：阈值变更影响 gate 行为。本脚本**默认不改**现有
检查的硬编码默认值（``app/core/config.py`` 的 CARTO_* 与
``_carto_threshold`` 一字不动）；``--write`` 只把分位数建议作为 **provisional
基线** 写入制图质量事实库（``cartography_quality_baselines``，ADR-0159），
并生成与既有基线的 diff 报告——是否采纳由人裁决。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

# 指标 → (warn 建议分位, fail 建议分位, .env 键, 取值路径, 方向)
# 方向 "low_bad"：值越小越糟（如色差），fail 阈 < warn 阈。
_METRICS: Dict[str, Dict[str, Any]] = {
    "load_ratio": {
        "env": ("CARTO_LOAD_WARN_RATIO", "CARTO_LOAD_FAIL_RATIO"),
        "extract": lambda ev: ev.get("load_ratio"),
        "percentiles": (0.66, 0.90),
        "direction": "high_bad",
        "fmt": "{:.2f}",
    },
    "min_adjacent_delta_e": {
        "env": ("CARTO_COLOR_SEP_WARN_DELTA_E", "CARTO_COLOR_SEP_FAIL_DELTA_E"),
        "extract": lambda ev: ev.get("min_adjacent_delta_e"),
        # low_bad：值越小越糟。warn 取 p33（最差的三分之一进警告档），
        # fail 取 p10（最差的十分之一进失败档）——fail 阈必须低于 warn 阈。
        "percentiles": (0.33, 0.10),
        "direction": "low_bad",
        "fmt": "{:.1f}",
    },
    "label_ink_ratio": {
        "env": ("CARTO_LABEL_WARN_RATIO", "CARTO_LABEL_FAIL_RATIO"),
        "extract": lambda ev: ev.get("label_ink_ratio"),
        "percentiles": (0.66, 0.90),
        "direction": "high_bad",
        "fmt": "{:.2f}",
    },
    "avg_feature_area_px": {
        "env": (None, "CARTO_SVS_AREA_PX"),
        "extract": lambda ev: ev.get("avg_feature_area_px"),
        "percentiles": (None, 0.05),
        "direction": "low_bad",
        "fmt": "{:.2f}",
    },
    "encoded_field_count": {
        "env": ("CARTO_VISUALVAR_WARN_COUNT", "CARTO_VISUALVAR_FAIL_COUNT"),
        "extract": lambda ev: ev.get("encoded_field_count"),
        "percentiles": (None, 0.95),
        "direction": "high_bad",
        "fmt": "{:.0f}",
        "integer": True,
    },
}


def iter_review_payloads(sources: Sequence[str]) -> Iterable[Dict[str, Any]]:
    for source in sources:
        if source == "-":
            try:
                yield json.loads(sys.stdin.read())
            except json.JSONDecodeError as exc:
                print(f"[skip] stdin is not JSON: {exc}", file=sys.stderr)
            continue
        path = Path(source)
        if path.is_dir():
            for child in sorted(path.rglob("*.json")):
                yield from _load_file(child)
        else:
            yield from _load_file(path)


def _load_file(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[skip] {path}: {exc}", file=sys.stderr)
        return
    # 三种可接受形态：评审结果本体（含 checks）、{"reviews": [...]} 打包、
    # eval report（嵌套位置不定，递归找含 checks 的 dict）。
    yield from _find_review_dicts(payload, depth=0)


def _find_review_dicts(node: Any, depth: int) -> Iterable[Dict[str, Any]]:
    if depth > 6 or isinstance(node, (str, int, float, bool, type(None))):
        return
    if isinstance(node, dict):
        if isinstance(node.get("checks"), list) and node.get("checks"):
            yield node
            # 评审体内嵌的子评审不再递归（checks 已覆盖）。
            return
        for value in node.values():
            yield from _find_review_dicts(value, depth + 1)
        return
    if isinstance(node, list):
        for item in node:
            yield from _find_review_dicts(item, depth + 1)


def collect_observations(reviews: Iterable[Dict[str, Any]]) -> Dict[str, List[float]]:
    observations: Dict[str, List[float]] = {name: [] for name in _METRICS}
    for review in reviews:
        for check in review.get("checks") or []:
            if not isinstance(check, dict):
                continue
            evidence = check.get("evidence")
            if not isinstance(evidence, dict):
                continue
            for name, spec in _METRICS.items():
                value = spec["extract"](evidence)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                ):
                    observations[name].append(float(value))
    return observations


def _percentile(sorted_values: List[float], q: float) -> float:
    if not sorted_values:
        return float("nan")
    position = q * (len(sorted_values) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(sorted_values) - 1)
    frac = position - low
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac


def _round(value: float, spec: Dict[str, Any]) -> Any:
    if spec.get("integer"):
        return int(round(value))
    return float(spec["fmt"].format(value))


def calibrate(observations: Dict[str, List[float]]) -> List[Dict[str, Any]]:
    reports: List[Dict[str, Any]] = []
    for name, values in observations.items():
        spec = _METRICS[name]
        if not values:
            reports.append({"metric": name, "n": 0, "note": "no observations"})
            continue
        ordered = sorted(values)
        warn_q, fail_q = spec["percentiles"]
        suggested: Dict[str, Any] = {}
        if warn_q is not None:
            suggested["warn"] = _round(_percentile(ordered, warn_q), spec)
        if fail_q is not None:
            suggested["fail"] = _round(_percentile(ordered, fail_q), spec)
        reports.append({
            "metric": name,
            "n": len(ordered),
            "min": _round(ordered[0], spec) if spec.get("integer") else ordered[0],
            "median": _round(_percentile(ordered, 0.5), spec),
            "max": _round(ordered[-1], spec) if spec.get("integer") else ordered[-1],
            "direction": spec["direction"],
            "suggested": suggested,
        })
    return reports


def render(reports: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("制图规则阈值校准（建议值——请人工审阅分布后再写入 .env）")
    lines.append("=" * 64)
    env_lines: List[str] = []
    for report in reports:
        if not report.get("n"):
            lines.append(f"- {report['metric']}: 无观测样本")
            continue
        lines.append(
            f"- {report['metric']}: n={report['n']} min={report['min']} "
            f"median={report['median']} max={report['max']} ({report['direction']})"
        )
        suggested = report.get("suggested") or {}
        parts = []
        for level in ("warn", "fail"):
            if level in suggested:
                parts.append(f"{level}={suggested[level]}")
        if parts:
            lines.append(f"    建议: {', '.join(parts)}")
        warn_env, fail_env = _METRICS[report["metric"]]["env"]
        if warn_env and "warn" in suggested:
            env_lines.append(f"{warn_env}={suggested['warn']}")
        if fail_env and "fail" in suggested:
            env_lines.append(f"{fail_env}={suggested['fail']}")
    if env_lines:
        lines.append("")
        lines.append("建议配置（复制到 .env 前请人工确认）:")
        lines.extend(f"# {line}" for line in env_lines)
    return "\n".join(lines)


def _direction_of(spec: Dict[str, Any]) -> str:
    return "low_bad" if spec["direction"] == "low_bad" else "high_bad"


#: 指标名 → 规则 id（与 ratchet 方向表/事实库 check_id 命名对齐）
_RULE_ID = {
    "load_ratio": "carto.load.ratio",
    "min_adjacent_delta_e": "carto.color.separability",
    "label_ink_ratio": "carto.label.collision_est",
    "avg_feature_area_px": "carto.scale.svs",
    "encoded_field_count": "carto.visualvar.overload",
}


def baseline_entries_from_reports(
    reports: List[Dict[str, Any]],
    scene: str = "*",
    tolerance_pct: float = 5.0,
) -> List[Any]:
    """把校准建议转成 ratchet 基线条目（provisional）。

    每个指标落两行：``<metric>.warn``（p66 档）与 ``<metric>.fail``
    （fail 档）。方向沿用指标语义（low_bad 的 fail 阈 < warn 阈）。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.services.cartography_ratchet import Baseline

    entries: List[Any] = []
    for report in reports:
        name = report.get("metric")
        suggested = report.get("suggested") or {}
        spec = _METRICS.get(name)
        if not spec or not suggested:
            continue
        rule_id = _RULE_ID.get(str(name), str(name))
        direction = _direction_of(spec)
        warn_q, fail_q = spec["percentiles"]
        if "warn" in suggested and warn_q is not None:
            entries.append(Baseline(
                scene_id=scene, check_id=f"{rule_id}.warn",
                value=float(suggested["warn"]), quantile=float(warn_q),
                direction=direction, tolerance_pct=tolerance_pct,
                status="provisional", sample_n=int(report.get("n") or 0),
            ))
        if "fail" in suggested and fail_q is not None:
            entries.append(Baseline(
                scene_id=scene, check_id=f"{rule_id}.fail",
                value=float(suggested["fail"]), quantile=float(fail_q),
                direction=direction, tolerance_pct=tolerance_pct,
                status="provisional", sample_n=int(report.get("n") or 0),
            ))
    return entries


def build_diff_report(
    reports: List[Dict[str, Any]],
    existing: Dict[str, float],
) -> Dict[str, Any]:
    """新建议 vs 既有基线（同 check_id 键）的 diff——dry-run 与 --write 都产出。"""
    changes: List[Dict[str, Any]] = []
    for report in reports:
        name = report.get("metric")
        rule_id = _RULE_ID.get(str(name), str(name))
        for level, value in (report.get("suggested") or {}).items():
            key = f"{rule_id}.{level}"
            old = existing.get(key)
            changes.append({
                "key": key,
                "n": report.get("n"),
                "old_baseline": old,
                "new_suggestion": value,
                "delta_pct": (
                    round((float(value) - float(old)) / abs(float(old)) * 100.0, 2)
                    if old not in (None, 0) and isinstance(old, (int, float))
                    else None
                ),
            })
    return {"changes": changes}


def load_existing_baselines() -> Dict[str, float]:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.services.cartography_ratchet import load_baselines_sync

        return {
            f"{b.check_id}": b.value
            for b in load_baselines_sync() if b.scene_id == "*"
        }
    except Exception:  # noqa: BLE001 — 事实库不可用时 diff 诚实降级为无旧值
        return {}


def write_baselines(entries: List[Any], activate: bool) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.services.cartography_metrics_store import ensure_tables
    from app.services.cartography_ratchet import write_baselines_sync

    ensure_tables()
    return write_baselines_sync(
        entries, status="active" if activate else None, source="calibration",
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="校准制图规则阈值（默认 dry-run，--write 才落库）")
    parser.add_argument("sources", nargs="*",
                        help="评审 JSON 文件/目录/'-'（stdin）")
    parser.add_argument("--write", action="store_true",
                        help="把建议值作为 provisional 基线写入事实库"
                             "（默认 dry-run 只打印）")
    parser.add_argument("--activate", action="store_true",
                        help="随 --write 直接置 active（默认 provisional）")
    parser.add_argument("--scene", default="*",
                        help="基线 scope（默认全局 *）")
    parser.add_argument("--tolerance", type=float, default=5.0,
                        help="基线劣化容差百分比（默认 ±5）")
    parser.add_argument("--diff-out", default=None,
                        help="diff 报告输出路径（JSON）；缺省只打印")
    args = parser.parse_args(argv)

    if not args.sources:
        parser.print_help(sys.stderr)
        return 2
    observations = collect_observations(iter_review_payloads(args.sources))
    total = sum(len(v) for v in observations.values())
    if total == 0:
        print("没有从输入中找到任何规则观测值（checks[].evidence）。", file=sys.stderr)
        return 1
    reports = calibrate(observations)
    print(render(reports))

    entries = baseline_entries_from_reports(
        reports, scene=args.scene, tolerance_pct=args.tolerance,
    )
    existing = load_existing_baselines()
    diff = build_diff_report(reports, existing)
    diff_text = json.dumps(diff, ensure_ascii=False, indent=2)

    if not args.write:
        print("\n[dry-run] 未写入任何内容（--write 才落库）。")
        print("[dry-run] diff 报告（vs 既有全局基线）:")
        print(diff_text)
        if args.diff_out:
            print("[dry-run] --diff-out 已忽略：dry-run 不写文件。")
        return 0

    if not entries:
        print("\n没有可入库的建议值（观测不足）——未写入。", file=sys.stderr)
        return 1
    written = write_baselines(entries, activate=args.activate)
    print(f"\n[write] 已入库 {written} 条校准基线（scene={args.scene}, "
          f"status={'active' if args.activate else 'provisional'}, source=calibration）")
    if args.diff_out:
        Path(args.diff_out).write_text(diff_text, encoding="utf-8")
        print(f"[write] diff 报告已写入 {args.diff_out}")
    else:
        print("[write] diff 报告:")
        print(diff_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
