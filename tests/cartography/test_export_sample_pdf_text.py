"""ac-08（ADR-0157 P3/P7）· PDF 中文文本层提取硬门禁。

任务书 §5：PDF 文本可被程序提取与检索（**含中文**）—— 用文本抽取断言，
禁止肉眼验收。样例由 frontend/scripts/ac08/pdf-text-probe.mjs 用真实
jsPDF + 仓内 vendored Noto Sans SC 子集字体生成
（docs/dev/ac-08-samples/sample-export-cjk.pdf，随 PR 入档）。
"""
from pathlib import Path

import pytest

pypdf = pytest.importorskip("pypdf")

SAMPLE = (
    Path(__file__).parent.parent.parent
    / "docs"
    / "dev"
    / "ac-08-samples"
    / "sample-export-cjk.pdf"
)

pytestmark = pytest.mark.skipif(
    not SAMPLE.is_file(), reason=f"样例缺失：{SAMPLE}（运行 pdf-text-probe.mjs 生成）"
)


def _extract_text() -> str:
    reader = pypdf.PdfReader(str(SAMPLE))
    assert len(reader.pages) >= 1
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _embedded_font_names() -> list[str]:
    reader = pypdf.PdfReader(str(SAMPLE))
    names: list[str] = []
    for page in reader.pages:
        res = page.get("/Resources") or {}
        fonts = res.get("/Font") or {}
        fonts = fonts.get_object() if fonts is not None else {}
        for ref in fonts.values():
            font = ref.get_object()
            base = str(font.get("/BaseFont") or "")

            def _descriptor_has_file(fd) -> bool:
                if fd is None:
                    return False
                d = fd.get_object()
                return any(k in d for k in ("/FontFile", "/FontFile2", "/FontFile3"))

            embedded = _descriptor_has_file(font.get("/FontDescriptor"))
            # Type0 CJK 字体：字形在 DescendantFonts 的描述符里（jsPDF 嵌
            # TTF 走 /FontFile2）。
            descendants = font.get("/DescendantFonts")
            if descendants is not None:
                for d in descendants.get_object():
                    if _descriptor_has_file(d.get_object().get("/FontDescriptor")):
                        embedded = True
            names.append(f"{base}{'+embedded' if embedded else ''}")
    return names


def test_sample_pdf_exists_with_pdf_magic():
    data = SAMPLE.read_bytes()
    assert data.startswith(b"%PDF"), "样例非 PDF"
    assert len(data) > 50_000, "样例过小（可能缺嵌入字体/地图体）"


def test_cjk_title_extractable_and_searchable():
    """中文标题从 PDF 文本层程序化提取 —— 不再是栅格位图。"""
    text = _extract_text()
    assert "成都市学校分布图" in text, f"中文标题不可提取，实际文本层：{text[:200]!r}"
    assert "出版级导出" in text, "副标题中文不可提取"
    # 检索语义：子串定位（文字可选可检索的最低契约）
    idx = text.find("学校分布图")
    assert idx > 0


def test_cjk_text_not_rasterized_fallback():
    """文本层含真字形（嵌入字体）而非 pdf_text_rasterized_cjk 兜底。"""
    text = _extract_text()
    # 栅格化兜底下标题不进文本层 —— 这里断言相反面（提取到完整标题）
    assert text.count("成都市学校分布图") >= 1


def test_embedded_cjk_font_present():
    """字体描述符含 FontFile2（嵌入 TrueType）—— 中文可复现渲染的依据。"""
    names = _embedded_font_names()
    assert names, "页面无字体资源"
    assert any("embedded" in n for n in names), f"字体均未嵌入：{names}"
