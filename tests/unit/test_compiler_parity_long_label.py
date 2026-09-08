"""V5（ADR-0118 W10）跨孪生长文本截断 parity —— Python 侧。

共享 fixture `tests/fixtures/compiler_parity_long_label.json`（200 字符级
标注）。python 孪生在渲染器内截断（fit_label_text）；TS 导出路径在
buildVectorSvgExport 后处理截断（同 60 code-point 口径）。两侧对同一
fixture 必须产出**同一条截断文本**（前 59 code points + "…"），且全文本
不得完整进入任何产物。TS 侧断言见
frontend/lib/map-kit/vector-svg-export.parity.test.ts。
"""
import json
from pathlib import Path

from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "compiler_parity_long_label.json"


def _load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _long_label() -> str:
    spec = _load()
    fc = spec["sources"]["s1"]["data"]
    return fc["features"][0]["properties"]["name"]


def test_fixture_label_is_long():
    assert len(_long_label()) >= 200


def test_python_twin_truncates_long_label():
    spec = _load()
    comp = compile_mapspec_to_svg_detailed(spec, target_dpi=72)
    expected = "".join(list(_long_label())[:59]) + "…"
    assert expected in comp.svg, "python twin must emit the 59-cp prefix + ellipsis"
    assert _long_label() not in comp.svg, "full 200+ char label must not be embedded"


def test_python_twin_emits_label_truncated_diagnostic():
    spec = _load()
    comp = compile_mapspec_to_svg_detailed(spec, target_dpi=72)
    codes = [d["code"] for d in comp.diagnostics]
    assert "label_truncated" in codes
