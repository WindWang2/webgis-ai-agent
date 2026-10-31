"""旗舰验收：12 合成行政区图册 —— 真实渲染 + 机器解析对账（验收用户样例 1）。

断言面（全部机器可复核，无像素/人眼判断）：

- 13 页 PDF（封面 + 12 区），页数三处相等（计划/渲染/pypdf 实际）；
- 逐页文本层可检索：封面 TOC 列出 12 区；每内容页含本区名（页头注入）
  与 ``第 n / 13 页`` 页号 —— 页序与 TOC/manifest 一致；
- manifest 与 planner 绑定全对账：页 id/序/过滤身份/要素计数/指纹唯一；
- shared 分类：全部内容页 legend_digest 一致 + 图册级可比性为 true；
- 每页数据身份真实（页过滤文档要素计数 = 1 面）—— 不是改标题复制地图；
- 属主 sidecar 在场（下载路由 fail-closed 前提）。

合成数据显式标注：spec_params(simulated_data=True) → manifest.disclosures。
"""
import json
import os

import pytest

from app.services.atlas_batch.executor import execute_atlas_job

from .fixtures import DISTRICTS, district_document, spec_params
from .test_executor import _make_job

pytestmark = pytest.mark.cartography

pypdf = pytest.importorskip("pypdf")
weasyprint = pytest.importorskip("weasyprint")


def test_flagship_12_district_atlas(atlas_exports, atlas_job_db, atlas_spec_store):
    atlas_spec_store["set_document"](district_document())  # 12 区 × 3 小学
    job_id = _make_job(atlas_job_db, spec_params())
    result = execute_atlas_job(job_id)

    # ── 页数三处相等 ─────────────────────────────────────────────
    assert result["page_count_expected"] == 13
    assert result["page_count_rendered"] == 13
    assert result["page_count_pdf"] == 13
    assert result["qa"]["passed"] is True

    # ── PDF 机器解析 ─────────────────────────────────────────────
    pdf_path = os.path.join(str(atlas_exports), result["artifacts"]["pdf"])
    reader = pypdf.PdfReader(pdf_path)
    assert len(reader.pages) == 13

    cover_text = reader.pages[0].extract_text() or ""
    # 封面 TOC 列出全部 12 区（合成数据，simulated 标注见 manifest）
    for name in DISTRICTS:
        assert name in cover_text, f"TOC 缺 {name}"

    # 内容页：页序 = 区名字典序；每页含本区名（页头）与页号
    ordered = sorted(DISTRICTS)
    for i, name in enumerate(ordered):
        page_text = reader.pages[i + 1].extract_text() or ""
        assert name in page_text, f"第 {i + 2} 页缺区名 {name}"
        assert f"第 {i + 2} / 13 页" in page_text, f"第 {i + 2} 页缺页号"
        # 页头图册题在场（统一风格证据）
        assert "成都市各区小学分布图册" in page_text

    # ── manifest 与绑定全对账 ─────────────────────────────────────
    manifest_path = os.path.join(str(atlas_exports), result["artifacts"]["manifest"])
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    assert manifest["schema"] == "atlas_publication_manifest.v1"
    assert manifest["disclosures"]["simulated_data"] is True
    assert manifest["atlas"]["driver"] == "category"
    assert manifest["atlas"]["cross_page_comparability"] is True
    assert manifest["inputs"]["target_dpi"] == 300

    pages = manifest["pages"]
    assert len(pages) == 13
    assert pages[0]["cover"] is True and pages[0]["data"]["feature_count"] == 0
    content = pages[1:]
    # 页序/页题与 PDF 文本层一致
    assert [p["title"] for p in content] == ordered
    assert [p["page_number"] for p in pages] == list(range(1, 14))
    # 每内容页：真实数据绑定（过滤身份 + 计数 1 面）+ 唯一指纹
    for p in content:
        assert p["data"]["filter_property"] == "district"
        assert p["data"]["filter_value"] == p["title"]
        assert p["data"]["filter_layer_id"] == "districts"
        assert p["data"]["feature_count"] == 1
        assert p["page_fingerprint"].startswith("atlaspage-sha256:")
        assert p["product_manifest_digest"].startswith("prodman-sha256:")
        assert p["classification"]["scope"] == "shared"
        assert p["classification"]["field"] == "primaries"
    fingerprints = [p["page_fingerprint"] for p in content]
    assert len(set(fingerprints)) == 12  # 无重复页身份
    # shared 分类一致（同一绝对阈值/图例跨册）
    digests = {p["legend_digest"] for p in content}
    assert len(digests) == 1
    assert manifest["qa"]["classification_consistent"] is True

    # ── 属主与可复现输入 ─────────────────────────────────────────
    assert os.path.isfile(pdf_path + ".owner")
    assert os.path.isfile(manifest_path + ".owner")
    assert manifest["inputs"]["session_id"] == "sess-atlas-test"
    assert manifest["inputs"]["ir_fingerprint"].startswith("pubir-sha256:")
    assert manifest["inputs"]["spec_fingerprint"].startswith("pubspec-sha256:")
