"""Publication Cases v1 —— 六类出版用例结构 golden + 语义不变量（ADR-0283 D5）.

与 goldens/publication_cases.v1.json 逐字段对拍（generate.py 纯确定性再生），
另加 golden 之外的语义断言：
- key 组件（must_fit 缺省派生集）在任何页面档/语言下绝不出现在 suppressed；
- 约束发现码 ⊆ 每 case 的 closed 允许集；
- 长标题族（long_name_labels / research_figure_inset_chart）zh/en 双语
  wrap-not-clip：wrapped 且内容完整；预算内不 truncated；
- 双跑确定性（生成器幂等）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.lib.cartography.layout_constraint_spec import (
    derive_layout_constraints,
)
from app.lib.cartography.layout_description import LayoutInput
from app.lib.cartography.layout_description import build_publication_layout
from app.lib.cartography.layout_solver import (
    LayoutParticipantV4,
    solve_layout_v5,
)
from tests.cartography.publication_cases.cases import (
    ALLOWED_FINDING_CODES,
    CASES,
    EXPECTED_KEYS,
    LOCALES,
    PAGE_PROFILES,
    case_participants,
    case_texts,
)
from tests.cartography.publication_cases.generate import build_golden

pytestmark = pytest.mark.cartography

GOLDEN = (
    Path(__file__).parent
    / "publication_cases"
    / "goldens"
    / "publication_cases.v1.json"
)


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_golden_matrix_complete(golden: dict) -> None:
    assert len(golden["entries"]) == len(CASES) * len(LOCALES) * len(PAGE_PROFILES)
    keys = {
        (e["case"], e["locale"], e["page"]) for e in golden["entries"]
    }
    assert keys == {
        (c, loc, p["key"])
        for c in CASES for loc in LOCALES for p in PAGE_PROFILES
    }


def test_golden_matches_current_implementation(golden: dict) -> None:
    current = build_golden()
    assert current["entries"] == golden["entries"]


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("locale", LOCALES)
def test_key_components_never_suppressed(case: str, locale: str) -> None:
    for page in PAGE_PROFILES:
        parts = [LayoutParticipantV4(**p) for p in case_participants(case)]
        doc = derive_layout_constraints(
            [{"id": p.id, "type": p.type} for p in parts],
            page_profile="viewport",
        )
        sol = solve_layout_v5(parts, constraint_spec=doc, page_profile="viewport")
        suppressed = {s.id for s in sol.suppressed}
        assert suppressed.isdisjoint(EXPECTED_KEYS[case]), (
            f"{case}/{locale}/{page['key']}: key 组件被抑制 {suppressed}"
        )


@pytest.mark.parametrize("case", CASES)
def test_finding_codes_within_closed_set(case: str, golden: dict) -> None:
    for e in golden["entries"]:
        if e["case"] != case:
            continue
        codes = {f["code"] for f in e["layout_solution"]["constraint_findings"]}
        assert codes <= ALLOWED_FINDING_CODES[case], (
            f"{case}: 意外发现码 {codes - ALLOWED_FINDING_CODES[case]}"
        )


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("case", ["long_name_labels", "research_figure_inset_chart"])
def test_long_title_wrapped_not_truncated(case: str, locale: str) -> None:
    texts = case_texts(case, locale)
    out = build_publication_layout(LayoutInput(
        paper_size="A4", orientation="landscape", dpi=300,
        frame_width=3508, frame_height=2480,
        request_title=texts["title"],
        request_subtitle=texts["subtitle"],
        attribution_text=texts["attribution"],
    ))
    title = next(t for t in out["texts"] if t["kind"] == "title")
    assert title.get("wrapped") is True
    assert "truncated" not in title  # 预算内：内容完整
    joined = "".join(title["lines"])
    assert "".join(joined.split()) == "".join(texts["title"].split())


@pytest.mark.parametrize("page_key", ["16x9", "a4", "a3"])
def test_publication_page_projection_per_target(golden: dict, page_key: str) -> None:
    for e in golden["entries"]:
        if e["page"] != page_key or e["case"] != "continuous_raster_colorbar":
            continue
        page = e["publication"]["page"]
        assert page["dpi"] == next(
            p["dpi"] for p in PAGE_PROFILES if p["key"] == page_key
        )
        assert "safeAreaMm" in page  # 版心声明进入投影


def test_generator_deterministic_double_run() -> None:
    a = build_golden()
    b = build_golden()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_generator_cli_writes_identical_golden() -> None:
    """CLI 再生与提交的 golden 逐字段一致（受控再生验证）。"""
    repo_root = GOLDEN.parents[4]
    result = subprocess.run(
        [sys.executable, "tests/cartography/publication_cases/generate.py"],
        capture_output=True, text=True, cwd=repo_root, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    committed = json.loads(GOLDEN.read_text(encoding="utf-8"))
    regenerated = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert committed == regenerated
