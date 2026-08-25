#!/usr/bin/env python
"""校准制图规则阈值（spec 开放问题 1 的落点）。

输入是系统自身已经产出的证据——``_cartographic_review`` / 质量评审结果的
JSON（含 ``checks`` 数组，规则 evidence 里有 ``load_ratio`` /
``min_adjacent_delta_e`` / ``label_ink_ratio`` / ``avg_feature_area_px`` /
``encoded_field_count`` 等观测值）。来源不限：eval 套件的 ``report.json``
导出、``session_data_manager.get_map_state(sid)["_cartographic_review"]`` 的
批量导出、或手工构造的评审样本。

用法::

    # 单个/多个评审 JSON
    python scripts/calibrate_cartography_thresholds.py run1.json run2.json

    # 目录（递归收集 *.json）
    python scripts/calibrate_cartography_thresholds.py eval-runs/

    # stdin
    cat review.json | python scripts/calibrate_cartography_thresholds.py -

输出：每个指标在实测样本上的分布（n/最小/中位/最大）与按分位数建议的
warn/fail 阈值，以及可直接粘贴进 ``.env`` 的配置行。

哲学（与 ADR-0069 一致）：这是**建议**，不是自动改配置——阈值变更影响
gate 行为，必须由人看过分布后决定。脚本绝不写文件。
"""
from __future__ import annotations

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


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    observations = collect_observations(iter_review_payloads(args))
    total = sum(len(v) for v in observations.values())
    if total == 0:
        print("没有从输入中找到任何规则观测值（checks[].evidence）。", file=sys.stderr)
        return 1
    print(render(calibrate(observations)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
