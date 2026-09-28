"""PublicationIR golden 对拍 —— 页面级版面单一模型（C14）。

模式与 ``test_layout_ir_golden.py`` 同源：expected 由 Python 权威实现
（``plan_publication_pages``）生成冻结；本测试锁定：

- 单页/多帧/atlas（category/feature/frames）分页的**确定性**（同输入恒同 IR）；
- 纸张几何/范围拟合逐字段（``page_geometry`` 与既有 publication 链同口径）；
- 诚实封顶（页预算截断 + ``atlas_truncated`` / ``atlas_feature_scan_truncated``）；
- typed 拒绝（非法策略 → ``MapSpecSchemaError``）。

fixture 路径：``tests/cartography/golden_corpus/publication_ir/``。
刷新：``PUBLICATION_IR_UPDATE_GOLDEN=1 pytest tests/cartography/test_publication_ir_golden.py``。
"""

import json
import os
from pathlib import Path

import pytest

from app.lib.cartography.publication_ir import (
    AtlasPolicy,
    plan_publication_pages,
    publication_preflight,
)
from app.lib.cartography.mapspec_schema import MapSpecSchemaError

pytestmark = pytest.mark.cartography

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests/cartography/golden_corpus/publication_ir"


def _doc_zones():
    """确定性 synthetic 场景：3 类别 × 多要素（含单点类别与跨经度类别）。"""
    feats = []
    # zone=A：两点（bbox 有面积）
    feats.append({"type": "Feature",
                  "geometry": {"type": "Point", "coordinates": [116.0, 39.5]},
                  "properties": {"zone": "A", "idx": 0}})
    feats.append({"type": "Feature",
                  "geometry": {"type": "Point", "coordinates": [117.0, 40.5]},
                  "properties": {"zone": "A", "idx": 1}})
    # zone=B：单点（零面积 bbox → 拟合外扩路径）
    feats.append({"type": "Feature",
                  "geometry": {"type": "Point", "coordinates": [110.0, 32.0]},
                  "properties": {"zone": "B", "idx": 2}})
    # zone=C：LineString（嵌套坐标遍历路径）
    feats.append({"type": "Feature",
                  "geometry": {"type": "LineString",
                               "coordinates": [[100.0, 30.0], [101.5, 31.5]]},
                  "properties": {"zone": "C", "idx": 3}})
    return {
        "version": "1.1",
        "sources": {
            "g": {"type": "geojson",
                  "inlineData": {"type": "FeatureCollection", "features": feats}},
        },
        "layers": [
            {"id": "l1", "source": "g", "type": "circle",
             "paint": {"circle-color": "#b91c1c"}},
        ],
        "layout": {
            "components": [
                {"id": "t", "type": "title", "options": {"text": "C14 Atlas"}},
                {"id": "a", "type": "attribution", "options": {"text": "(c) test"}},
            ],
        },
    }


def _doc_frames():
    return {
        "version": "1.1",
        "sources": {},
        "layers": [{"id": "l1", "source": "", "type": "circle"}],
        "layout": {
            "frames": [
                {"id": "f1", "title": "总览", "pageSize": {"profile": "a4_landscape"},
                 "extent": [100.0, 30.0, 110.0, 40.0]},
                {"id": "f2", "title": "放大", "pageSize": {"width": 200.0, "height": 300.0},
                 "view": {"center": [116.0, 39.9], "zoom": 11}},
                {"id": "f3", "title": "禁用页", "enabled": False,
                 "extent": [0.0, 0.0, 1.0, 1.0]},
            ],
        },
    }


CASES = {
    "single_default": lambda: (_doc_zones(), None),
    "frames_basic": lambda: (_doc_frames(), None),
    "atlas_category_cover": lambda: (
        _doc_zones(),
        {"driver": "category", "categoryProperty": "zone", "includeCover": True,
         "atlasTitle": "C14 图册"},
    ),
    "atlas_feature_chunks": lambda: (
        _doc_zones(),
        {"driver": "feature", "featuresPerPage": 2},
    ),
}


def _policy_of(raw):
    if raw is None:
        return None
    return AtlasPolicy(
        driver=raw.get("driver", "frames"),
        category_property=raw.get("categoryProperty", ""),
        include_cover=bool(raw.get("includeCover")),
        atlas_title=raw.get("atlasTitle", ""),
        features_per_page=raw.get("featuresPerPage", 50),
    )


@pytest.fixture(scope="module", params=sorted(CASES))
def golden(request):
    path = FIXTURES / f"{request.param}.json"
    doc, atlas_raw = CASES[request.param]()
    ir = plan_publication_pages(doc, atlas=_policy_of(atlas_raw))
    expected = json.loads(json.dumps(ir.model_dump(), ensure_ascii=False))
    if os.environ.get("PUBLICATION_IR_UPDATE_GOLDEN") == "1" or not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"input_doc": doc, "input_atlas": atlas_raw,
                        "expected": expected},
                       ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return path, doc, atlas_raw, expected


def test_golden_matches(golden):
    path, doc, atlas_raw, expected = golden
    stored = json.loads(path.read_text(encoding="utf-8"))
    ir = plan_publication_pages(doc, atlas=_policy_of(atlas_raw))
    assert json.loads(json.dumps(ir.model_dump(), ensure_ascii=False)) == stored["expected"]
    assert stored["expected"] == expected


def test_golden_page_fields(golden):
    _, doc, atlas_raw, expected = golden
    pages = expected["pages"]
    assert pages, "每场景至少一页"
    assert [p["page_number"] for p in pages] == list(range(1, len(pages) + 1))
    assert all(p["page_count"] == len(pages) for p in pages)
    for p in pages:
        assert p["paper"]["width_mm"] > 0 and p["paper"]["height_mm"] > 0
        assert p["component_ir"].get("version") == 2  # C2 IR v2 冻结词表
        if not (atlas_raw or {}).get("includeCover"):
            assert p["cover"] is False


def test_golden_deterministic_repeat(golden):
    doc, atlas_raw, _ = golden[1], golden[2], golden[3]
    a = plan_publication_pages(doc, atlas=_policy_of(atlas_raw)).ir_fingerprint()
    b = plan_publication_pages(doc, atlas=_policy_of(atlas_raw)).ir_fingerprint()
    assert a == b


def test_category_atlas_order_and_bounds():
    ir = plan_publication_pages(
        _doc_zones(),
        atlas=AtlasPolicy(driver="category", category_property="zone"),
    )
    # 类别页按值字典序（确定性页序）
    assert [p.filter_value for p in ir.pages] == ["A", "B", "C"]
    for page in ir.pages:
        assert page.bounds is not None and len(page.bounds) == 4
        w, s, e, n = page.bounds
        assert e > w and n > s  # 拟合后范围非退化（含单点类别 B）
    assert ir.degradations == []


def test_category_budget_truncates_with_degradation():
    ir = plan_publication_pages(
        _doc_zones(),
        atlas=AtlasPolicy(driver="category", category_property="zone", page_budget=2),
    )
    assert len(ir.pages) == 2
    assert any(d["code"] == "atlas_truncated" for d in ir.degradations)


def test_feature_scan_within_cap_and_budget_cap_honest():
    doc = _doc_zones()
    feats = doc["sources"]["g"]["inlineData"]["features"]
    feats.extend({"type": "Feature",
                  "geometry": {"type": "Point", "coordinates": [120.0 + i, 30.0]},
                  "properties": {"zone": f"Z{i}"}}
                 for i in range(60))
    from app.lib.cartography.publication_ir import (
        MAX_ATLAS_SCAN_FEATURES,
        AtlasPolicy as AP,
    )
    assert MAX_ATLAS_SCAN_FEATURES > 0
    # 扫描上界内（63 要素 < 5000）→ 全部 63 类别被发现；页预算诚实封顶 20
    ir = plan_publication_pages(
        doc, atlas=AP(driver="category", category_property="zone", page_budget=20),
    )
    assert len(ir.pages) == 20
    assert [d["code"] for d in ir.degradations] == ["atlas_truncated"]


def test_feature_scan_cap_stops_and_discloses():
    """> MAX_ATLAS_SCAN_FEATURES 要素 → 扫描停止 + scan 截断披露（不无限读）。"""
    doc = _doc_zones()
    feats = doc["sources"]["g"]["inlineData"]["features"]
    from app.lib.cartography.publication_ir import (
        MAX_ATLAS_SCAN_FEATURES,
        AtlasPolicy as AP,
    )
    total = MAX_ATLAS_SCAN_FEATURES + 50
    feats.extend({"type": "Feature",
                  "geometry": {"type": "Point", "coordinates": [100.0 + (i % 40), 20.0 + (i % 30)]},
                  "properties": {"zone": f"S{i}"}}
                 for i in range(total - len(feats)))
    ir = plan_publication_pages(
        doc, atlas=AP(driver="category", category_property="zone", page_budget=20),
    )
    codes = [d["code"] for d in ir.degradations]
    assert "atlas_feature_scan_truncated" in codes
    assert len(ir.pages) <= 20


def test_typed_rejections():
    doc = _doc_zones()
    with pytest.raises(MapSpecSchemaError) as e:
        plan_publication_pages(doc, atlas=AtlasPolicy(driver="category"))
    assert e.value.code == "atlas_category_property_missing"
    with pytest.raises(MapSpecSchemaError) as e:
        plan_publication_pages(
            doc, atlas=AtlasPolicy(driver="category", category_property="nope"))
    assert e.value.code == "atlas_category_empty"
    with pytest.raises(MapSpecSchemaError) as e:
        plan_publication_pages(
            doc, atlas=AtlasPolicy(driver="category", category_property="zone",
                                   layer_id="missing"))
    assert e.value.code == "atlas_source_unavailable"
    # frames 在场但全 disabled → 既有链同语义 typed 拒绝
    doc_f = _doc_frames()
    doc_f["layout"]["frames"][0]["enabled"] = False
    doc_f["layout"]["frames"][1]["enabled"] = False
    with pytest.raises(MapSpecSchemaError) as e:
        plan_publication_pages(doc_f)
    assert e.value.code == "publication_no_pages"


def test_preflight_warnings():
    doc = _doc_zones()
    doc["layout"]["components"] = []  # 去掉 attribution → preflight 披露
    ir = plan_publication_pages(
        doc, atlas=AtlasPolicy(driver="category", category_property="zone"))
    codes = {w["code"] for w in publication_preflight(ir, doc)}
    assert "attribution_missing" in codes

    doc2 = _doc_frames()
    doc2["layout"]["frames"][0]["title"] = "超" * 60
    ir2 = plan_publication_pages(doc2)
    codes2 = {w["code"] for w in publication_preflight(ir2, doc2)}
    assert "title_wrap_expected" in codes2
