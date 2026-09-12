"""ac-08（ADR-0157 P6）· 版面描述 IR golden 对拍 —— Python 镜像面。

与 frontend/lib/export/layout-description.golden.test.ts 消费**同一份**
fixture（expected 由 TS 参照实现生成冻结）。本文件断言：Python 镜像
（app/lib/cartography/layout_description.py）逐字段复现 expected ——
前后端「同一版面描述」的对拍硬闸。
"""
import json
import math
from pathlib import Path

import pytest

from app.lib.cartography import layout_description as ld

FIXTURE = (
    Path(__file__).parent
    / "golden_corpus"
    / "layout_description"
    / "basic_cmyk_overflow.json"
)


@pytest.fixture(scope="module")
def fixture_data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _build_ir(data: dict) -> dict:
    inp = data["input"]
    return ld.build_publication_layout(
        ld.LayoutInput(
            paper_size=inp["paperSize"],
            orientation=inp["orientation"],
            dpi=inp["dpi"],
            frame_width=inp["frame"]["width"],
            frame_height=inp["frame"]["height"],
            request_title=inp["requestTitle"],
            meters_per_pixel=inp["metersPerPixel"],
            mask_extent=tuple(inp["extent"]["mask"]),
            export_extent=tuple(inp["extent"]["export"]),
            data_overflow=inp["extent"]["dataOverflow"],
            color_mode=inp["colorMode"],
        )
    )


def _assert_deep(actual, expected, path: str = "") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: 非对象"
        assert set(actual.keys()) == set(expected.keys()), (
            f"{path}: 键集漂移 {set(actual.keys()) ^ set(expected.keys())}"
        )
        for k in expected:
            _assert_deep(actual[k], expected[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), (
            f"{path}: 数组长度漂移"
        )
        for i, (a, e) in enumerate(zip(actual, expected)):
            _assert_deep(a, e, f"{path}[{i}]")
    elif isinstance(expected, float):
        assert isinstance(actual, (int, float)), f"{path}: 非数值"
        assert math.isclose(actual, expected, rel_tol=0, abs_tol=1e-9), (
            f"{path}: {actual} != {expected}"
        )
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


def test_layout_description_matches_ts_reference(fixture_data):
    """Python 镜像逐字段复现 TS 参照实现冻结的 expected（1e-9 浮点容差）。"""
    ir = _build_ir(fixture_data)
    _assert_deep(ir, fixture_data["expected"], "ir")


def test_extent_math_matches_ts_reference(fixture_data):
    """Mercator 范围数学镜像复现冻结边界（同 1e-9 容差）。"""
    m = fixture_data["extentMath"]
    out = ld.export_bounds_for_frame(tuple(m["mask"]), m["aspectWH"])
    assert out is not None
    for a, e in zip(out, m["expectedExport"]):
        assert math.isclose(a, e, rel_tol=0, abs_tol=1e-9), f"{a} != {e}"
    for probe in m["mercNormYProbes"]:
        assert math.isclose(
            ld.merc_norm_y(probe["lat"]), probe["y"], rel_tol=0, abs_tol=1e-12
        )


def test_srgb_profile_has_no_bleed(fixture_data):
    """出版档开关语义：srgb 无出血无裁切线（与 TS page 装配一致）。"""
    data = json.loads(json.dumps(fixture_data))
    data["input"]["colorMode"] = "srgb"
    ir = _build_ir(data)
    assert ir["page"]["bleedMm"] == 0
    assert ir["page"]["cropMarks"] is False
