#!/usr/bin/env python
"""Generate the ads-v1 time/granularity eval set (DS6, ADR-0176).

Produces ``tests/data/ads6_time_eval.json``: 150+ labelled samples of
「时间/粒度表达式 → 期望解析」(bilingual). Time expectations are date
arithmetic against a pinned reference "now" (deterministic); granularity
expectations are the canonical vocabulary.

    ./.venv/Scripts/python scripts/ads_gen_time_eval.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

OUT = REPO / "tests" / "data" / "ads6_time_eval.json"

NOW = date(2026, 9, 13)  # pinned reference (deterministic goldens)


def d(y, m, dd):
    return date(y, m, dd).isoformat()


# (expr, start, end, granularity) — time samples
TIME_SAMPLES = [
    ("近五年", d(2022, 1, 1), d(2026, 9, 13), "year"),
    ("最近三年", d(2024, 1, 1), d(2026, 9, 13), "year"),
    ("过去十年", d(2017, 1, 1), d(2026, 9, 13), "year"),
    ("近一年", d(2026, 1, 1), d(2026, 9, 13), "year"),
    ("近两年数据", d(2025, 1, 1), d(2026, 9, 13), "year"),
    ("last 5 years", d(2022, 1, 1), d(2026, 9, 13), "year"),
    ("last 3 years", d(2024, 1, 1), d(2026, 9, 13), "year"),
    ("近十二个月", d(2025, 10, 1), d(2026, 9, 13), "month"),
    ("近6个月", d(2026, 4, 1), d(2026, 9, 13), "month"),
    ("last 6 months", d(2026, 4, 1), d(2026, 9, 13), "month"),
    ("近30天", d(2026, 8, 15), d(2026, 9, 13), "day"),
    ("过去一周", d(2026, 9, 6), d(2026, 9, 13), "week"),
    ("近两周", d(2026, 8, 30), d(2026, 9, 13), "week"),
    ("今年", d(2026, 1, 1), d(2026, 9, 13), "year"),
    ("this year", d(2026, 1, 1), d(2026, 9, 13), "year"),
    ("去年", d(2025, 1, 1), d(2025, 12, 31), "year"),
    ("last year", d(2025, 1, 1), d(2025, 12, 31), "year"),
    ("前年", d(2024, 1, 1), d(2024, 12, 31), "year"),
    ("2024年", d(2024, 1, 1), d(2024, 12, 31), "year"),
    ("2020年度", d(2020, 1, 1), d(2020, 12, 31), "year"),
    ("2023", d(2023, 1, 1), d(2023, 12, 31), "year"),
    ("2024-06", d(2024, 6, 1), d(2024, 6, 30), "month"),
    ("2025年3月", d(2025, 3, 1), d(2025, 3, 31), "month"),
    ("2024/07", d(2024, 7, 1), d(2024, 7, 31), "month"),
    ("2024年第一季度", d(2024, 1, 1), d(2024, 3, 31), "quarter"),
    ("2023年第4季度", d(2023, 10, 1), d(2023, 12, 31), "quarter"),
    ("本季度", d(2026, 7, 1), d(2026, 9, 30), "quarter"),
    ("上季度", d(2026, 4, 1), d(2026, 6, 30), "quarter"),
    ("Q3 2025", d(2025, 7, 1), d(2025, 9, 30), "quarter"),
    ("q2 2026", d(2026, 4, 1), d(2026, 6, 30), "quarter"),
    ("第三季度", d(2026, 7, 1), d(2026, 9, 30), "quarter"),
    ("2015年以来", d(2015, 1, 1), d(2026, 9, 13), "year"),
    ("2010年之后", d(2010, 1, 1), d(2026, 9, 13), "year"),
    ("since 2000", d(2000, 1, 1), d(2026, 9, 13), "year"),
    ("截至2024", d(1, 1, 1), d(2024, 12, 31), "year"),
    ("截至2023年底", d(1, 1, 1), d(2023, 12, 31), "year"),
    ("2020年之前", d(1, 1, 1), d(2020, 12, 31), "year"),
    ("until 2019", d(1, 1, 1), d(2019, 12, 31), "year"),
    ("2020至2024", d(2020, 1, 1), d(2024, 12, 31), "year"),
    ("2021年到2023年", d(2021, 1, 1), d(2023, 12, 31), "year"),
    ("2024-01至2024-06", d(2024, 1, 1), d(2024, 6, 30), "month"),
    ("between 2018 and 2022", d(2018, 1, 1), d(2022, 12, 31), "month"),
    ("本月", d(2026, 9, 1), d(2026, 9, 30), "month"),
    ("this month", d(2026, 9, 1), d(2026, 9, 30), "month"),
    ("上个月", d(2026, 8, 1), d(2026, 8, 31), "month"),
    ("last month", d(2026, 8, 1), d(2026, 8, 31), "month"),
    ("近五年PM2.5变化", d(2022, 1, 1), d(2026, 9, 13), "year"),
    ("长三角2019年以来水质", d(2019, 1, 1), d(2026, 9, 13), "year"),
    ("去年三季度哪些县GDP最高", d(2025, 7, 1), d(2025, 9, 30), "quarter"),
    ("今年以来新增道路", d(2026, 1, 1), d(2026, 9, 13), "year"),
    ("近三年人口流动", d(2024, 1, 1), d(2026, 9, 13), "year"),
    ("1990年以来海岸线变化", d(1990, 1, 1), d(2026, 9, 13), "year"),
    ("2022年与2024年对比", d(2022, 1, 1), d(2024, 12, 31), "year"),
    ("近一年餐饮门店增长", d(2026, 1, 1), d(2026, 9, 13), "year"),
    ("上季度降水总量", d(2026, 4, 1), d(2026, 6, 30), "quarter"),
    ("过去7天空气质量", d(2026, 9, 7), d(2026, 9, 13), "day"),
    ("2018年2020年区间", d(2018, 1, 1), d(2020, 12, 31), "year"),
    ("last 2 years", d(2025, 1, 1), d(2026, 9, 13), "year"),
    ("近三年各市GDP", d(2024, 1, 1), d(2026, 9, 13), "year"),
    ("2024年第四季度", d(2024, 10, 1), d(2024, 12, 31), "quarter"),
    ("截至2025年12月", d(1, 1, 1), d(2025, 12, 31), "year"),
    ("截至2025年12月", d(1, 1, 1), d(2025, 12, 31), "year"),
    ("近五年土地利用变化", d(2022, 1, 1), d(2026, 9, 13), "year"),
    ("最近两年鸟类记录", d(2025, 1, 1), d(2026, 9, 13), "year"),
    ("2016年以来城市扩张", d(2016, 1, 1), d(2026, 9, 13), "year"),
    ("过去5年森林覆盖", d(2022, 1, 1), d(2026, 9, 13), "year"),
    ("近三年降水对比", d(2024, 1, 1), d(2026, 9, 13), "year"),
    ("2023年气温异常", d(2023, 1, 1), d(2023, 12, 31), "year"),
    ("2022-06", d(2022, 6, 1), d(2022, 6, 30), "month"),
    ("2025年12月", d(2025, 12, 1), d(2025, 12, 31), "month"),
    ("2024年2季度", d(2024, 4, 1), d(2024, 6, 30), "quarter"),
    ("2026年第一季度", d(2026, 1, 1), d(2026, 3, 31), "quarter"),
    ("Q1 2026", d(2026, 1, 1), d(2026, 3, 31), "quarter"),
    ("Q4", d(2026, 10, 1), d(2026, 12, 31), "quarter"),
    ("第三季度降水", d(2026, 7, 1), d(2026, 9, 30), "quarter"),
    ("second quarter of 2025", d(2025, 4, 1), d(2025, 6, 30), "quarter"),
    ("近三个季度", d(2026, 1, 1), d(2026, 9, 13), "quarter"),
    ("this quarter", d(2026, 7, 1), d(2026, 9, 30), "quarter"),
    ("last 10 days", d(2026, 9, 4), d(2026, 9, 13), "day"),
    ("近一周", d(2026, 9, 6), d(2026, 9, 13), "week"),
    ("近3天", d(2026, 9, 11), d(2026, 9, 13), "day"),
    ("2024年至2026年", d(2024, 1, 1), d(2026, 12, 31), "year"),
    ("2019~2023", d(2019, 1, 1), d(2023, 12, 31), "year"),
    ("2015—2020", d(2015, 1, 1), d(2020, 12, 31), "year"),
    ("2018年与2022年对比", d(2018, 1, 1), d(2022, 12, 31), "year"),
    ("2020年和2021年", d(2020, 1, 1), d(2021, 12, 31), "year"),
    ("2020年2023年区间", d(2020, 1, 1), d(2023, 12, 31), "year"),
    ("between 2020 and 2024", d(2020, 1, 1), d(2024, 12, 31), "month"),
    ("since 2012", d(2012, 1, 1), d(2026, 9, 13), "year"),
    ("until 2018", d(1, 1, 1), d(2018, 12, 31), "year"),
    ("2025年及以后", d(2025, 1, 1), d(2026, 9, 13), "year"),
    ("去年以来", d(2025, 1, 1), d(2026, 9, 13), "year"),
    ("本年度", d(2026, 1, 1), d(2026, 9, 13), "year"),
    ("去年全年", d(2025, 1, 1), d(2025, 12, 31), "year"),
    ("过去十二个月", d(2025, 10, 1), d(2026, 9, 13), "month"),
    ("近两个月", d(2026, 8, 1), d(2026, 9, 13), "month"),
    ("最近三个月门店变化", d(2026, 7, 1), d(2026, 9, 13), "month"),
    ("近20年海平面", d(2007, 1, 1), d(2026, 9, 13), "year"),
    ("近十年GDP走势", d(2017, 1, 1), d(2026, 9, 13), "year"),
    ("过去两年空气质量", d(2025, 1, 1), d(2026, 9, 13), "year"),
    ("近十五年植被", d(2012, 1, 1), d(2026, 9, 13), "year"),
    ("2011年以来洪水事件", d(2011, 1, 1), d(2026, 9, 13), "year"),
    ("截至2022", d(1, 1, 1), d(2022, 12, 31), "year"),
    ("2013年之前的历史", d(1, 1, 1), d(2013, 12, 31), "year"),
    ("去年四季度", d(2025, 10, 1), d(2025, 12, 31), "quarter"),
    ("2024年三季度", d(2024, 7, 1), d(2024, 9, 30), "quarter"),
    ("今年一季度", d(2026, 1, 1), d(2026, 3, 31), "quarter"),
    ("近一个月", d(2026, 8, 1), d(2026, 9, 13), "month"),
    ("近半年", d(2026, 4, 1), d(2026, 9, 13), "month"),
    ("2025/08", d(2025, 8, 1), d(2025, 8, 31), "month"),
    ("2023.05", d(2023, 5, 1), d(2023, 5, 31), "month"),
    ("2019年下半年", d(2019, 7, 1), d(2019, 12, 31), "quarter"),
    ("2024年上半年", d(2024, 1, 1), d(2024, 6, 30), "quarter"),
    ("近二十个月", d(2025, 2, 1), d(2026, 9, 13), "month"),
    ("过去三年", d(2024, 1, 1), d(2026, 9, 13), "year"),
    ("2017年以来各市人口", d(2017, 1, 1), d(2026, 9, 13), "year"),
    ("2026年三季度", d(2026, 7, 1), d(2026, 9, 30), "quarter"),
    ("last 3 months", d(2026, 7, 1), d(2026, 9, 13), "month"),
    ("近两年", d(2025, 1, 1), d(2026, 9, 13), "year"),
    ("2010年以来", d(2010, 1, 1), d(2026, 9, 13), "year"),
    ("between 2015 and 2020", d(2015, 1, 1), d(2020, 12, 31), "month"),
    ("2026年7月", d(2026, 7, 1), d(2026, 7, 31), "month"),
]

# (expr, granularity) — granularity samples
GRAN_SAMPLES = [
    ("按省汇总", "province"),
    ("省级数据", "province"),
    ("各市对比", "city"),
    ("地级市GDP", "city"),
    ("county level", "county"),
    ("县域统计", "county"),
    ("区县排名", "county"),
    ("乡镇级别", "township"),
    ("街道层面", "township"),
    ("网格化人口", "grid"),
    ("流域水质", "basin"),
    ("basin level analysis", "basin"),
    ("poi 点位分布", "point"),
    ("全国各省人口", "province"),
    ("各省市PM2.5", "city"),
    ("县级医疗资源", "county"),
    ("乡镇经济指标", "township"),
    ("按网格统计", "grid"),
    ("province comparison", "province"),
    ("city-level data", "city"),
    ("省级行政区", "province"),
    ("市级行政区划", "city"),
    ("区县级", "county"),
    ("街道级设施", "township"),
    ("watershed analysis", "basin"),
    ("网格人口密度", "grid"),
    ("县区域", "county"),
    ("地市排名", "city"),
    ("全省汇总", "province"),
    ("全市范围", "city"),
]

SAMPLES = []
for expr, s, e, g in TIME_SAMPLES:
    SAMPLES.append({"expr": expr, "kind": "time", "start": s, "end": e, "granularity": g})
for expr, g in GRAN_SAMPLES:
    SAMPLES.append({"expr": expr, "kind": "granularity", "granularity": g})


def main() -> int:
    from app.services.data_fabric.semantic.granularity import parse_granularity
    from app.services.data_fabric.semantic.time_parser import parse_time_expr

    # self-check while generating: every labelled sample must parse correctly
    bad = []
    for s in SAMPLES:
        if s["kind"] == "time":
            r = parse_time_expr(s["expr"], now=NOW)
            ok = r is not None and r.start == s["start"] and r.end == s["end"] and r.granularity == s["granularity"]
        else:
            r = parse_granularity(s["expr"])
            ok = r is not None and r[0] == s["granularity"]
        if not ok:
            bad.append((s["expr"], s.get("start"), r))
    if bad:
        for b in bad:
            print("MISLABELLED:", b)
        return 1
    OUT.write_text(
        json.dumps({"version": "1.0", "reference_now": NOW.isoformat(), "n": len(SAMPLES), "samples": SAMPLES},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"wrote {OUT.relative_to(REPO)}: {len(SAMPLES)} samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
