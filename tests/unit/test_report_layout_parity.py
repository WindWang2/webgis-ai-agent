"""ac-08（ADR-0157 P6）· 报告附图与前端导出同源版面对拍。

对拍硬闸（§5）：后端报告附图（report_service._compile_vector_svg_for_report）
编译画幅由**同一版面描述 IR**（app/lib/cartography/layout_description）决定
—— 与前端 buildPublicationLayout 消费同一决策面（golden fixture 双端对拍见
test_layout_description_golden.py）。本文件锁定报告侧接线语义：
- spec 的 export_layout 组件（A4/A3 × 横竖）→ 编译画幅纵横比与 IR.page 一致；
- A3 纵版 spec → 纵横比翻转（证明组件参数真实参与决策，而非恒定 1200×800）。
"""
import asyncio
import math
from typing import Any

import pytest

from app.services import report_service as rs
from app.services.report_service import ReportService


def _spec_with_export_layout(paper: str, orientation: str) -> dict[str, Any]:
    return {
        "version": "1.0",
        "sources": {
            "s1": {"type": "geojson", "inlineData": {"features": []}}
        },
        "layers": [
            {"id": "l1", "type": "fill", "source": "s1", "paint": {"fill-color": "#2563eb"}}
        ],
        "layout": {
            "components": [
                {
                    "id": "exp",
                    "type": "export_layout",
                    "options": {"paperSize": paper, "orientation": orientation, "dpi": 300},
                }
            ]
        },
    }


@pytest.mark.parametrize(
    "paper,orientation,expected_ratio",
    [
        ("A4", "landscape", 297 / 210),
        ("A4", "portrait", 210 / 297),
        ("A3", "landscape", 420 / 297),
    ],
)
def test_report_compile_uses_publication_layout_aspect(paper, orientation, expected_ratio):
    """报告附图编译画幅纵横比 == 版面描述 IR.page（前端同源决策）。"""
    svc = ReportService.__new__(ReportService)  # 只测编译接线，不触 DB 面
    captured: dict[str, Any] = {}

    def _fake_compile(mapspec, dpi, *, width, height, include_chrome):
        captured["width"] = width
        captured["height"] = height
        captured["include_chrome"] = include_chrome

        class _Comp:
            timed_out = False
            diagnostics: list = []
            svg = "<svg xmlns='http://www.w3.org/2000/svg'></svg>"

        return _Comp()

    original = rs.compile_mapspec_to_svg_detailed
    rs.compile_mapspec_to_svg_detailed = _fake_compile
    try:
        comp = asyncio.run(
            svc._compile_vector_svg_bounded(
                _spec_with_export_layout(paper, orientation), timeout_s=5.0
            )
        )
    finally:
        rs.compile_mapspec_to_svg_detailed = original

    assert captured["include_chrome"] is True
    assert captured["width"] == 1200
    ratio = captured["width"] / captured["height"]
    assert math.isclose(ratio, expected_ratio, rel_tol=1e-3), (
        f"编译画幅纵横比 {ratio} 偏离 IR 版面 {expected_ratio}"
    )
    assert comp.svg.startswith("<svg")


def test_report_layout_input_defaults_and_title():
    """无 export_layout 组件 → 默认 A4 landscape；title 组件文本进 IR.spec_title。"""
    spec = {
        "layout": {
            "components": [
                {"id": "t", "type": "title", "options": {"text": "长江流域图"}}
            ]
        }
    }
    inp = rs._report_layout_input(spec)
    assert inp.paper_size == "A4"
    assert inp.orientation == "landscape"
    assert inp.spec_title == "长江流域图"
    ir = rs.build_publication_layout(inp)
    assert ir["texts"][0] == {"kind": "title", "text": "长江流域图"}
