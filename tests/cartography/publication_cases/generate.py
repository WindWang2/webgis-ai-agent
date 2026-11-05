"""Publication Cases v1 golden 生成器（ADR-0283 D5）.

对六族 × zh|en × 16:9|A4|A3 全矩阵求 solve_layout_v5 + build_publication_layout，
投影为确定性 digest 写入 goldens/publication_cases.v1.json（排序稳定，
双跑 diff=0）。运行：`python tests/cartography/publication_cases/generate.py`
（在仓库根；写入同目录 goldens/）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.lib.cartography.layout_constraint_spec import (  # noqa: E402
    derive_layout_constraints,
)
from app.lib.cartography.layout_description import (  # noqa: E402
    LayoutInput,
    build_publication_layout,
)
from app.lib.cartography.layout_solver import (  # noqa: E402
    LayoutParticipantV4,
    solve_layout_v5,
)
from tests.cartography.publication_cases.cases import (  # noqa: E402
    CASES,
    LOCALES,
    PAGE_PROFILES,
    case_participants,
    case_texts,
)


def _digest_entry(case: str, locale: str, page: dict) -> dict:
    parts = [LayoutParticipantV4(**p) for p in case_participants(case)]
    comp_shims = [
        {"id": p.id, "type": p.type} for p in parts
    ]
    doc = derive_layout_constraints(comp_shims, page_profile="viewport")
    sol = solve_layout_v5(parts, constraint_spec=doc, page_profile="viewport")

    texts = case_texts(case, locale)
    layout = build_publication_layout(LayoutInput(
        paper_size=page["paper_size"],
        orientation=page["orientation"],
        dpi=page["dpi"],
        frame_width=page["frame"]["width"],
        frame_height=page["frame"]["height"],
        request_title=texts["title"],
        request_subtitle=texts["subtitle"],
        attribution_text=texts["attribution"],
        meters_per_pixel=20.3,
        safe_area_mm={"top": 12, "right": 10, "bottom": 15, "left": 10},
    ))

    return {
        "case": case,
        "locale": locale,
        "page": page["key"],
        "layout_solution": {
            "placements": sorted(
                [{"id": p.id, "zone": p.zone, "width_units": p.width_units}
                 for p in sol.placements],
                key=lambda e: e["id"],
            ),
            "suppressed": sorted(
                [{"id": s.id, "reason": s.reason} for s in sol.suppressed],
                key=lambda e: e["id"],
            ),
            "collapsed_ids": sorted(sol.collapsed_ids),
            "hidden_ids": sorted(sol.hidden_ids),
            "repair_actions": [s.action for s in sol.repair_steps],
            "constraint_findings": sorted(
                [{"code": f.code, "component_id": f.component_id}
                 for f in sol.constraint_findings],
                key=lambda e: (e["code"], e["component_id"]),
            ),
        },
        "publication": {
            "page": {k: v for k, v in layout["page"].items()},
            "texts": [
                {k: v for k, v in t.items() if k != "lines"} | (
                    {"line_count": len(t["lines"])} if "lines" in t else {}
                )
                for t in layout["texts"]
            ],
            "scale_bar_label": (layout.get("scaleBar") or {}).get("label"),
            "degradations": [d["code"] for d in layout["degradations"]],
        },
    }


def build_golden() -> dict:
    entries = []
    for case in CASES:
        for locale in LOCALES:
            for page in PAGE_PROFILES:
                entries.append(_digest_entry(case, locale, page))
    return {
        "name": "publication_cases_v1",
        "spec_version": "layout_constraint_spec.v1",
        "_provenance": (
            "ADR-0283 D5 —— 六族出版用例结构 golden。生成器 "
            "tests/cartography/publication_cases/generate.py（纯确定性："
            "derive_layout_constraints + solve_layout_v5 + "
            "build_publication_layout）；消费方 "
            "test_publication_cases_v1.py 逐字段对拍 + 语义不变量断言。"
        ),
        "entries": entries,
    }


def main() -> int:
    golden = build_golden()
    out = Path(__file__).parent / "goldens" / "publication_cases.v1.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(golden, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out} ({len(golden['entries'])} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
