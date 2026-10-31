"""planner 测试：确定性/页绑定/指纹语义（数据变更只变相关页）。"""
import pytest

from app.lib.cartography.atlas_job_spec import AtlasJobSpec, ClassificationScope
from app.lib.cartography.publication_ir import AtlasPolicy
from app.services.atlas_batch.planner import plan_atlas_binding

from .fixtures import DISTRICTS, district_document, spec_params

pytestmark = pytest.mark.cartography


def _spec(params=None):
    p = spec_params(**(params or {}))
    return AtlasJobSpec(
        spec_version=p["spec_version"], session_id=p["session_id"],
        atlas=AtlasPolicy(**p["atlas"]),
        classification_scope=ClassificationScope(
            p.get("classification_scope", "shared")),
        target_dpi=p["target_dpi"], title=p["title"],
        simulated_data=p.get("simulated_data", False),
    )


def test_deterministic_plan_and_fingerprints():
    doc = district_document()
    p1 = plan_atlas_binding(_spec(), doc)
    p2 = plan_atlas_binding(_spec(), doc)
    assert [b.fingerprint for b in p1.bindings] == [b.fingerprint for b in p2.bindings]
    assert p1.ir_fingerprint == p2.ir_fingerprint


def test_twelve_districts_cover_toc_and_bindings():
    doc = district_document()
    plan = plan_atlas_binding(_spec(), doc)
    cover = [b for b in plan.bindings if b.cover]
    content = [b for b in plan.bindings if not b.cover]
    assert len(cover) == 1 and cover[0].feature_count == 0
    assert len(content) == 12
    # 页序 = 类别字典序；页题 = 行政区名
    titles = [b.title for b in content]
    assert titles == sorted(DISTRICTS)
    # 每内容页绑定 1 个行政区面（category 过滤层的真实计数）
    assert all(b.feature_count == 1 for b in content)
    # 页码连续且含封面
    numbers = [b.page_number for b in plan.bindings]
    assert numbers == list(range(1, len(plan.bindings) + 1))
    assert all(b.bounds for b in content)


def test_shared_scope_legend_digest_consistent():
    plan = plan_atlas_binding(_spec(), district_document())
    digests = {b.legend_digest for b in plan.content_pages}
    assert len(digests) == 1 and next(iter(digests)).startswith("atlaslegend-sha256:")
    cls = plan.content_pages[0].classification
    assert cls["field"] == "primaries" and cls["method"] == "quantile"
    assert cls["class_count"] == 4  # breaks 3 条 → 4 级


def test_data_change_only_rotates_affected_page_fingerprint():
    doc = district_document()
    before = plan_atlas_binding(_spec(), doc)
    fp_before = {b.page_id: b.fingerprint for b in before.content_pages}

    # 仅改"金牛区"的属性值（面数不变、几何不变）
    doc2 = district_document()
    for f in doc2["sources"]["districts"]["inlineData"]["features"]:
        if f["properties"]["district"] == "金牛区":
            f["properties"]["primaries"] = 99
    after = plan_atlas_binding(_spec(), doc2)
    fp_after = {b.page_id: b.fingerprint for b in after.content_pages}

    changed = [pid for pid in fp_before if fp_before[pid] != fp_after[pid]]
    assert changed == ["cat_金牛区"], changed


def test_feature_count_change_rotates_fingerprint():
    doc = district_document(schools_per_district=3)
    # 目标层是 districts（面）；给"武侯区"再加一个面 → 该页 feature_count 变
    doc2 = district_document(schools_per_district=3)
    extra = dict(doc2["sources"]["districts"]["inlineData"]["features"][3])
    doc2["sources"]["districts"]["inlineData"]["features"].append(extra)
    b1 = {b.page_id: b.fingerprint
          for b in plan_atlas_binding(_spec(), doc).content_pages}
    b2 = {b.page_id: b.fingerprint
          for b in plan_atlas_binding(_spec(), doc2).content_pages}
    assert b2["cat_武侯区"] != b1["cat_武侯区"]


def test_dpi_or_scope_change_rotates_all_fingerprints():
    doc = district_document()
    base = [b.fingerprint for b in plan_atlas_binding(_spec(), doc).content_pages]
    dpi = [b.fingerprint
           for b in plan_atlas_binding(_spec({"target_dpi": 150}), doc).content_pages]
    assert dpi != base


def test_local_scope_with_frames_uses_frame_overrides_identity():
    doc = district_document()
    doc = dict(doc)
    doc["layout"] = {
        **doc["layout"],
        "frames": [
            {"id": "f1", "title": "东区", "enabled": True,
             "extent": [103.8, 30.5, 104.3, 30.9],
             "layerOverrides": {"districts": {
                 "legend_spec": {"type": "choropleth", "field": "students",
                                 "breaks": [1100], "colors": ["#fee2e2", "#991b1b"]}}},
             },
        ],
    }
    spec = _spec({"atlas": {
        "driver": "frames", "layer_id": "", "category_property": "",
        "features_per_page": 50, "page_budget": 20,
        "include_cover": False, "atlas_title": "局部分级"},
        "classification_scope": "local"})
    plan = plan_atlas_binding(spec, doc)
    pages = [b for b in plan.bindings if not b.cover]
    assert len(pages) == 1
    # 帧覆盖后的 legend（students/1100）进入页身份
    assert pages[0].classification.get("field") == "students"
    # 无过滤页的计数 = 全部内联源合计（12 面 + 36 学校点）
    assert pages[0].feature_count == 48


def test_typed_failure_on_missing_category_values():
    doc = district_document()
    doc["sources"]["districts"]["inlineData"]["features"] = []
    with pytest.raises(Exception) as ei:
        plan_atlas_binding(_spec(), doc)
    assert getattr(ei.value, "code", "") in ("atlas_source_unavailable",
                                             "atlas_no_pages")


def test_non_target_layer_content_change_rotates_fingerprints():
    """R1-P1 回归：非目标层（schools）内容变化必须轮换全部页指纹。"""
    doc = district_document()
    before = [b.fingerprint for b in plan_atlas_binding(_spec(), doc).content_pages]

    doc2 = district_document()
    for f in doc2["sources"]["schools"]["inlineData"]["features"]:
        f["properties"]["name"] = "改名后的学校"  # 计数/几何不变的内容变更
    after = [b.fingerprint for b in plan_atlas_binding(_spec(), doc2).content_pages]
    assert after != before


def test_layer_paint_change_rotates_fingerprints():
    """R1-P1 回归：样式（paint/阈值）变化轮换页指纹（结构指纹并入）。"""
    doc = district_document()
    before = [b.fingerprint for b in plan_atlas_binding(_spec(), doc).content_pages]

    doc2 = district_document()
    doc2["layers"][0]["paint"]["fill-color"] = "#9d174d"
    doc2["thresholds"] = {"maxFeatures": 77}
    after = [b.fingerprint for b in plan_atlas_binding(_spec(), doc2).content_pages]
    assert after != before
