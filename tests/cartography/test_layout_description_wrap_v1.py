"""ADR-0283 D2 · 关键文本 wrap-not-clip —— Python 镜像 golden 对拍.

与 frontend/lib/export/layout-description.wrap.test.ts 消费**同一份**
fixture（expected 由 TS 参照实现 UPDATE_LAYOUT_GOLDEN=1 生成冻结）。
断言：Python 镜像逐字段复现 expected（折行 lines/wrapped/truncated、
safeAreaMm 增量、cmyk 出血档）；短文本保持旧形态（无增量键）。
"""
import json
import math
from pathlib import Path

import pytest

from app.lib.cartography import layout_description as ld

pytestmark = pytest.mark.cartography

FIXTURE = (
    Path(__file__).parent
    / "golden_corpus"
    / "layout_description"
    / "long_title_wrap.json"
)


@pytest.fixture(scope="module")
def fixture_data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _build(data: dict) -> dict:
    inp = data["input"]
    return ld.build_publication_layout(
        ld.LayoutInput(
            paper_size=inp["paperSize"],
            orientation=inp["orientation"],
            dpi=inp["dpi"],
            frame_width=inp["frame"]["width"],
            frame_height=inp["frame"]["height"],
            request_title=inp["requestTitle"],
            request_subtitle=inp["requestSubtitle"],
            author=inp["author"],
            meters_per_pixel=inp["metersPerPixel"],
            mask_extent=tuple(inp["extent"]["mask"]),
            export_extent=tuple(inp["extent"]["export"]),
            data_overflow=inp["extent"]["dataOverflow"],
            color_mode=inp["colorMode"],
            safe_area_mm=inp.get("safeAreaMm"),
        )
    )


def _assert_deep(actual, expected, path: str = "") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: 非对象"
        assert set(actual.keys()) == set(expected.keys()), (
            f"{path}: 键集不一致 actual={sorted(actual)} expected={sorted(expected)}")
        for k in expected:
            _assert_deep(actual[k], expected[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: 非数组"
        assert len(actual) == len(expected), f"{path}: 长度不一致"
        for i, (a, e) in enumerate(zip(actual, expected)):
            _assert_deep(a, e, f"{path}[{i}]")
    elif isinstance(expected, float):
        assert math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9), (
            f"{path}: {actual} != {expected}")
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


def test_mirror_reproduces_expected(fixture_data: dict) -> None:
    actual = _build(fixture_data)
    assert actual["version"] == ld.PUBLICATION_LAYOUT_VERSION
    _assert_deep(actual, fixture_data["expected"])


def test_long_cjk_title_wrapped_not_truncated(fixture_data: dict) -> None:
    texts = _build(fixture_data)["texts"]
    title = next(t for t in texts if t["kind"] == "title")
    assert title.get("wrapped") is True
    assert "truncated" not in title
    joined = "".join(title["lines"])
    assert "".join(joined.split()) == "".join(fixture_data["input"]["requestTitle"].split())


def test_long_latin_subtitle_wrapped_by_word(fixture_data: dict) -> None:
    texts = _build(fixture_data)["texts"]
    subtitle = next(t for t in texts if t["kind"] == "subtitle")
    assert subtitle.get("wrapped") is True
    assert "truncated" not in subtitle


def test_safe_area_projected_only_when_declared(fixture_data: dict) -> None:
    page = _build(fixture_data)["page"]
    assert page["safeAreaMm"] == fixture_data["input"]["safeAreaMm"]
    base = _build(fixture_data)
    inp2 = ld.LayoutInput(
        paper_size="A4", orientation="landscape", dpi=300,
        frame_width=2480, frame_height=1754,
        request_title="短标题",
    )
    page2 = ld.build_publication_layout(inp2)["page"]
    assert "safeAreaMm" not in page2
    assert base["version"] == ld.PUBLICATION_LAYOUT_VERSION


def test_short_title_keeps_legacy_shape() -> None:
    out = ld.build_publication_layout(
        ld.LayoutInput(
            paper_size="screen", orientation="landscape", dpi=96,
            frame_width=1920, frame_height=1080,
            request_title="短标题", request_subtitle="短副标题",
        )
    )
    assert out["texts"][0] == {"kind": "title", "text": "短标题"}
    assert out["texts"][1] == {"kind": "subtitle", "text": "短副标题"}


def test_budget_exhaustion_visible_truncation() -> None:
    huge = "测" * 400
    out = ld.build_publication_layout(
        ld.LayoutInput(
            paper_size="A4", orientation="landscape", dpi=300,
            frame_width=2480, frame_height=1754,
            request_title=huge,
        )
    )
    title = out["texts"][0]
    assert title["wrapped"] is True
    assert title["truncated"] is True
    assert len(title["lines"]) == 2
    assert len("".join(title["lines"])) < 400
