"""页头注入与 QA 门测试（纯函数面）。"""
import pytest

from app.services.atlas_batch.page_compiler import inject_page_header
from app.services.atlas_batch.qa import verify_atlas_pages, verify_pdf_page_count
from app.lib.cartography.atlas_job_spec import ClassificationScope

pytestmark = pytest.mark.cartography


def test_inject_page_header_appends_deterministic_texts():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1188" height="840" '
           'viewBox="0 0 1188 840"><rect width="1188" height="840"/></svg>')
    out = inject_page_header(
        svg, page_number=3, page_count=13, page_title="金牛区",
        width_px=1188, height_px=840, atlas_title="成都小学图册")
    assert out.count("<text") == 2
    assert "金牛区" in out and "第 3 / 13 页" in out
    assert "成都小学图册" in out
    # 根属性/viewBox 不变；注入在 </svg> 之前
    assert out.startswith('<svg xmlns="http://www.w3.org/2000/svg" width="1188"')
    assert out.endswith("</svg>")
    # 确定性
    out2 = inject_page_header(
        svg, page_number=3, page_count=13, page_title="金牛区",
        width_px=1188, height_px=840, atlas_title="成都小学图册")
    assert out == out2


def test_inject_escapes_xml_and_malformed_svg_untouched():
    out = inject_page_header(
        '<svg width="10" height="10"></svg>', page_number=1, page_count=2,
        page_title='A"&<b>', width_px=100, height_px=100)
    assert "&amp;" in out and "&lt;" in out
    # 无 </svg> → 不注入不破坏
    assert inject_page_header("<svg/>", page_number=1, page_count=1,
                              page_title="x", width_px=10, height_px=10) == "<svg/>"


class _B:
    """最小 binding 形状（QA 只读这些字段）。"""

    def __init__(self, page_id, cover=False, bounds=(1.0, 2.0, 3.0, 4.0),
                 legend_digest="d1"):
        self.page_id = page_id
        self.cover = cover
        self.bounds = list(bounds) if bounds is not None else None
        self.legend_digest = legend_digest


def _bindings(n=3):
    return [_B(f"p{i}") for i in range(n)]


def test_qa_passes_full_render_shared():
    bs = _bindings(3)
    report = verify_atlas_pages(
        bs, ["p0", "p1", "p2"],
        classification_scope=ClassificationScope.shared,
        classification_scope_disclosed=True)
    assert report.passed and report.classification_consistent is True
    assert report.page_count_expected == 3 == report.page_count_rendered


def test_qa_detects_missing_duplicate_order():
    bs = _bindings(3)
    r1 = verify_atlas_pages(bs, ["p0", "p2"],
                            classification_scope=ClassificationScope.shared,
                            classification_scope_disclosed=True)
    assert not r1.passed and "atlas_page_missing" in [v["code"] for v in r1.violations]
    r2 = verify_atlas_pages(bs, ["p0", "p1", "p1"],
                            classification_scope=ClassificationScope.shared,
                            classification_scope_disclosed=True)
    assert not r2.passed and r2.duplicate_page_ids == ["p1"]
    r3 = verify_atlas_pages(bs, ["p1", "p0", "p2"],
                            classification_scope=ClassificationScope.shared,
                            classification_scope_disclosed=True)
    assert not r3.passed and r3.ordering_ok is False


def test_qa_shared_classification_inconsistency_fails():
    bs = [_B("p0"), _B("p1", legend_digest="d2"), _B("p2")]
    report = verify_atlas_pages(
        bs, ["p0", "p1", "p2"],
        classification_scope=ClassificationScope.shared,
        classification_scope_disclosed=True)
    assert not report.passed
    assert report.classification_consistent is False
    assert any(v["code"] == "atlas_classification_inconsistent"
               for v in report.violations)


def test_qa_local_scope_requires_disclosure():
    bs = _bindings(2)
    ok = verify_atlas_pages(
        bs, ["p0", "p1"], classification_scope=ClassificationScope.local,
        classification_scope_disclosed=True)
    assert ok.passed and ok.classification_consistent is None
    bad = verify_atlas_pages(
        bs, ["p0", "p1"], classification_scope=ClassificationScope.local,
        classification_scope_disclosed=False)
    assert not bad.passed
    assert any(v["code"] == "atlas_comparability_undisclosed"
               for v in bad.violations)


def test_qa_missing_bounds_fails():
    bs = [_B("p0"), _B("p1", bounds=None)]
    report = verify_atlas_pages(
        bs, ["p0", "p1"], classification_scope=ClassificationScope.shared,
        classification_scope_disclosed=True)
    assert not report.passed
    assert any(v["code"] == "atlas_page_bounds_missing" for v in report.violations)


def test_qa_pdf_page_count_gate():
    bs = _bindings(3)
    report = verify_atlas_pages(
        bs, ["p0", "p1", "p2"], classification_scope=ClassificationScope.shared,
        classification_scope_disclosed=True)
    assert verify_pdf_page_count(report, 3).passed
    bad = verify_pdf_page_count(report, 2)
    assert not bad.passed
    assert any(v["code"] == "atlas_page_count_mismatch" for v in bad.violations)
