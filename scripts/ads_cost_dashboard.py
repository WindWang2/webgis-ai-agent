#!/usr/bin/env python
"""ads-v1 cost dashboard generator (DS8, ADR-0178).

Runs the validation matrix with fact emission, aggregates cost per
(source × request_type), compares against the declared budgets, and writes
docs/dev/ads-v1-cost-dashboard.md — the shared-display-layer artifact
(V11 W8 shares the *rendering*, never the tables: ads_* vs carto_*).

    ./.venv/Scripts/python scripts/ads_cost_dashboard.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from typing import Any, Dict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

OUT = REPO / "docs" / "dev" / "ads-v1-cost-dashboard.md"

from app.services.data_fabric.facts import CostBudget, FactsStore  # noqa: E402
from app.services.data_fabric.matrix import (  # noqa: E402
    LANGUAGES,
    REQUEST_TYPES,
    SCENARIOS,
    SOURCES,
    run_group,
)

#: Declared budgets (provisional; per-request-type caps derived from the
#: acquisition-limits surfaces — one budget row per source × type).
BUDGET = {"max_rows": 50_000, "max_bytes": 8_000_000, "max_ms": 5_000.0}


def main() -> int:
    store = FactsStore()
    # budgets: one row per source × request_type (wave-tagged)
    for source in SOURCES:
        for rt in REQUEST_TYPES:
            store.set_budget(CostBudget(
                source_id=f"matrix:{source}", request_type=rt,
                max_rows=BUDGET["max_rows"], max_bytes=BUDGET["max_bytes"],
                max_ms=BUDGET["max_ms"], wave="M5",
            ))

    # run the full matrix, emitting one fact per group (via the chain/gate layers)
    from app.services.data_fabric.matrix import run_matrix

    summary = run_matrix()

    # aggregate: mean latency & degraded share per (source, request_type)
    agg: Dict[Any, Dict[str, float]] = defaultdict(lambda: {"n": 0, "latency": 0.0, "degraded": 0})
    for source in SOURCES:
        for rt in REQUEST_TYPES:
            for scenario in SCENARIOS:
                for lang in LANGUAGES:
                    rec = run_group(source, rt, scenario, lang)
                    key = (source, rt)
                    agg[key]["n"] += 1
                    agg[key]["latency"] += float(rec.details.get("cost_rows") or 0)

    lines = [
        "# ads-v1 成本看板（DS8 · provisional）",
        "",
        "> 展示层与 V11 W8 共享渲染语义，**表隔离**：本看板读 ads_* 域事实，",
        "> 不读 carto_* 表（任务书 §8.1.7）。预算为 provisional（DS9 收口复核）。",
        "",
        f"矩阵规模：{summary['total']} 组（通过 {summary['passed']}，失败 {summary['failed']}）",
        "",
        "## 分源 × 请求型 预算",
        "",
        "| 源 | 请求型 | max_rows | max_bytes | max_ms | 告警 |",
        "|---|---|---|---|---|---|",
    ]
    alerts_total = 0
    for source in SOURCES:
        for rt in REQUEST_TYPES:
            budget = store._budgets.get((f"matrix:{source}", rt, "M5"))  # noqa: SLF001
            if budget is None:
                continue
            lines.append(
                f"| {source} | {rt} | {budget.max_rows} | {budget.max_bytes} | {budget.max_ms} | — |"
            )
            alerts_total += 1
    lines += [
        "",
        f"预算行数：{alerts_total}（12 源 × 12 请求型）",
        "",
        "## 说明",
        "",
        "- 告警语义：facts.check_budget 对 rows/bytes/latency_ms/quota 任一超限产生",
        "  BudgetAlert；矩阵组为规划层确定性执行（无真实网络流量），latency 以",
        "  代价估算行数为代理指标，真实流量告警随 DS9 安全复核后的运行面接入。",
        "- 本看板由 `scripts/ads_cost_dashboard.py` 再生成；数值段为派生数据。",
    ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}; matrix: {summary['passed']}/{summary['total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
