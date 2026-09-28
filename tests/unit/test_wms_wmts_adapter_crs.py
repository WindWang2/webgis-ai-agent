"""G10: CRS 解析链路测试守护 — GetCapabilities 解析 →
_extract_layer_crs_and_bbox → normalize_crs → 去重保序。

此前整条链路只有 normalize_crs 本体有分支测试
（test_data_fabric_metadata.py），_extract_layer_crs_and_bbox 与
describe() 里的去重保序循环零守护。这里用真实风格的 GetCapabilities
片段锁死每个分支的正反例：嵌套 Layer 的 CRS/bbox 继承、最近声明优先、
WMS 1.3/1.1/WMTS 三代 bbox 编码、畸形 bbox 诚实拒绝、去重保序与
主 srs 优先级。全部离线（parse_safe_xml + FakeSession，无网络）。"""

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.adapters import WMSWMTSAdapter
from app.services.data_fabric.adapters.wms_wmts_adapter import (
    _extract_layer_crs_and_bbox,
)
from app.services.data_fabric.security import DataFabricSecurity

# ── helpers ──────────────────────────────────────────────────────────────────


def extract(xml: bytes, dataset_id: str = "roads"):
    """经生产同款 parse_safe_xml 解析后调用被测函数。"""
    root = DataFabricSecurity.parse_safe_xml(xml)
    return _extract_layer_crs_and_bbox(root, dataset_id)


def make_adapter(xml: bytes) -> WMSWMTSAdapter:
    """离线 adapter：RFC1918 IP 字面量（免 DNS，沙箱与 CI 一致）+
    FakeSession 路由 GetCapabilities，无任何真实网络。"""
    profile = ConnectionProfile(provider_type="wms_wmts", endpoint="")
    profile.url = "http://10.0.0.20/wms"
    profile.allow_private = True
    adapter = WMSWMTSAdapter(profile)
    adapter.session = _FakeSession(
        routes={"GetCapabilities": _FakeResponse(content=xml)}
    )
    return adapter


class _FakeResponse:
    def __init__(self, *, content=b""):
        self.content = content
        self.text = content.decode()
        self.status_code = 200
        self.headers = {"Content-Type": "text/xml"}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i : i + chunk_size]

    def close(self):
        return None


class _FakeSession:
    def __init__(self, routes=None):
        self.calls = []
        self.headers = {}
        self.routes = routes or {}

    def get(self, url, params=None, timeout=None, **kwargs):
        p = dict(params or {})
        self.calls.append({"url": url, "params": p})
        key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(p.items()))
        for needle, resp in self.routes.items():
            if needle in url or needle in key:
                return resp
        return _FakeResponse()


# ── GetCapabilities fixtures（真实风格缩略片段） ─────────────────────────────

# WMS 1.3.0：namespaced，叶子 Layer 无任何声明，全部继承自父 Layer。
WMS_130_NESTED = b"""<?xml version="1.0" encoding="UTF-8"?>
<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms">
  <Capability>
    <Layer>
      <Name>root</Name>
      <CRS>EPSG:4326</CRS>
      <EX_GeographicBoundingBox>
        <westBoundLongitude>-180</westBoundLongitude>
        <eastBoundLongitude>180</eastBoundLongitude>
        <southBoundLatitude>-90</southBoundLatitude>
        <northBoundLatitude>90</northBoundLatitude>
      </EX_GeographicBoundingBox>
      <Layer>
        <Name>roads</Name>
        <Title>Roads</Title>
      </Layer>
    </Layer>
  </Capability>
</WMS_Capabilities>"""

# WMTS 1.0.0：ows:Identifier + WGS84BoundingBox 角点文本。
WMTS_100 = b"""<?xml version="1.0" encoding="UTF-8"?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0"
              xmlns:ows="http://www.opengis.net/ows/1.1">
  <Contents>
    <Layer>
      <ows:Identifier>ortho</ows:Identifier>
      <ows:WGS84BoundingBox>
        <ows:LowerCorner>100.0 20.0</ows:LowerCorner>
        <ows:UpperCorner>120.0 40.0</ows:UpperCorner>
      </ows:WGS84BoundingBox>
    </Layer>
  </Contents>
</Capabilities>"""


# ── Layer 定位 ────────────────────────────────────────────────────────────────


def test_layer_found_by_name_wms():
    crs, bbox, _ = extract(WMS_130_NESTED)
    assert crs == ["EPSG:4326"]
    assert bbox == [-180.0, -90.0, 180.0, 90.0]


def test_layer_found_by_identifier_wmts():
    crs, bbox, _ = extract(WMTS_100, "ortho")
    assert crs == []
    assert bbox == [100.0, 20.0, 120.0, 40.0]


def test_layer_not_found_returns_honest_empty_with_note():
    crs, bbox, notes = extract(WMS_130_NESTED, "no-such-layer")
    assert crs == []
    assert bbox is None
    assert any("no-such-layer" in n and "not found" in n for n in notes)


def test_layer_name_whitespace_is_stripped_for_match():
    xml = b"""<Root><Layer><Name> roads </Name><SRS>EPSG:4326</SRS></Layer></Root>"""
    crs, _, _ = extract(xml)
    assert crs == ["EPSG:4326"]


def test_nested_child_layer_matched_without_parent_name_collision():
    """父 Layer 的 Name 不是候选（只查直接子元素）——父名 'roads' 与
    子层名 'rails' 各自独立命中，不串层。"""
    xml = b"""<Root><Layer><Name>roads</Name>
      <SRS>EPSG:4326</SRS>
      <Layer><Name>rails</Name><SRS>EPSG:3857</SRS></Layer>
    </Layer></Root>"""
    parent_crs, _, _ = extract(xml, "roads")
    child_crs, _, _ = extract(xml, "rails")
    assert parent_crs == ["EPSG:4326"]
    assert child_crs == ["EPSG:3857"]


def test_duplicate_layer_names_first_in_document_order_wins():
    xml = b"""<Root>
      <Layer><Name>roads</Name><SRS>EPSG:4326</SRS></Layer>
      <Layer><Name>roads</Name><SRS>EPSG:3857</SRS></Layer>
    </Root>"""
    crs, _, _ = extract(xml)
    assert crs == ["EPSG:4326"]


# ── CRS 继承与多形式声明 ─────────────────────────────────────────────────────


def test_crs_inherited_from_parent_when_layer_declares_none():
    crs, _, notes = extract(WMS_130_NESTED)
    assert crs == ["EPSG:4326"]
    assert not any("no CRS/SRS" in n for n in notes)


def test_nearest_crs_declaration_wins_over_ancestor():
    xml = b"""<Root><Layer><Name>root</Name><CRS>EPSG:4326</CRS>
      <Layer><Name>roads</Name><CRS>EPSG:3857</CRS></Layer>
    </Layer></Root>"""
    crs, _, _ = extract(xml)
    assert crs == ["EPSG:3857"]


def test_crs_from_higher_ancestor_when_nearer_levels_lack_it():
    xml = b"""<Root><Layer><Name>top</Name><CRS>EPSG:4326</CRS>
      <Layer><Name>mid</Name>
        <Layer><Name>roads</Name></Layer>
      </Layer>
    </Layer></Root>"""
    crs, _, notes = extract(xml)
    assert crs == ["EPSG:4326"]
    assert not any("no CRS/SRS" in n for n in notes)


def test_srs_element_wms_111_passthrough():
    xml = b"""<Root><Layer><Name>roads</Name><SRS>EPSG:4326</SRS></Layer></Root>"""
    crs, _, _ = extract(xml)
    assert crs == ["EPSG:4326"]


def test_srs_space_separated_multiple_values():
    xml = b"""<Root><Layer><Name>roads</Name>
      <SRS>EPSG:4326 EPSG:3857</SRS></Layer></Root>"""
    crs, _, _ = extract(xml)
    assert crs == ["EPSG:4326", "EPSG:3857"]


def test_srs_comma_separated_multiple_values():
    xml = b"""<Root><Layer><Name>roads</Name>
      <SRS>EPSG:4326,EPSG:3857</SRS></Layer></Root>"""
    crs, _, _ = extract(xml)
    assert crs == ["EPSG:4326", "EPSG:3857"]


def test_multiple_crs_elements_all_collected_in_order():
    xml = b"""<Root><Layer><Name>roads</Name>
      <CRS>EPSG:3857</CRS><CRS>CRS84</CRS><CRS>EPSG:4326</CRS>
    </Layer></Root>"""
    crs, _, _ = extract(xml)
    assert crs == ["EPSG:3857", "CRS84", "EPSG:4326"]


def test_empty_crs_element_contributes_nothing():
    xml = b"""<Root><Layer><Name>roads</Name><CRS>  </CRS>
      <SRS></SRS></Layer></Root>"""
    crs, _, notes = extract(xml)
    assert crs == []
    assert any("no CRS/SRS" in n for n in notes)


def test_empty_crs_at_layer_does_not_block_ancestor_inheritance():
    """近层空 <CRS> 视为「无声明」而非「声明为空」——祖先声明仍可继承。"""
    xml = b"""<Root><Layer><Name>root</Name><CRS>EPSG:4326</CRS>
      <Layer><Name>roads</Name><CRS>  </CRS></Layer>
    </Layer></Root>"""
    crs, _, notes = extract(xml)
    assert crs == ["EPSG:4326"]
    assert not any("no CRS/SRS" in n for n in notes)


def test_child_srs_wins_over_parent_crs_mixed_generation():
    """混代链：同级的 CRS 与 SRS 等价收集，最近一级声明整体胜出。"""
    xml = b"""<Root><Layer><Name>root</Name><CRS>EPSG:4326</CRS>
      <Layer><Name>roads</Name><SRS>EPSG:3857</SRS></Layer>
    </Layer></Root>"""
    crs, _, _ = extract(xml)
    assert crs == ["EPSG:3857"]


def test_no_crs_on_chain_returns_empty_with_note():
    xml = b"""<Root><Layer><Name>roads</Name><Title>x</Title></Layer></Root>"""
    crs, _, notes = extract(xml)
    assert crs == []
    assert any("no CRS/SRS" in n and "never silently assumed" in n for n in notes)


# ── bbox 三代编码 ────────────────────────────────────────────────────────────


def test_ex_geographic_bbox_wms_130():
    xml = b"""<Root><Layer><Name>roads</Name>
      <EX_GeographicBoundingBox>
        <westBoundLongitude>100</westBoundLongitude>
        <eastBoundLongitude>120</eastBoundLongitude>
        <southBoundLatitude>20</southBoundLatitude>
        <northBoundLatitude>40</northBoundLatitude>
      </EX_GeographicBoundingBox></Layer></Root>"""
    _, bbox, notes = extract(xml)
    assert bbox == [100.0, 20.0, 120.0, 40.0]
    assert not any("bbox" in n.lower() for n in notes)


def test_latlonbbox_attributes_wms_111():
    xml = b"""<Root><Layer><Name>roads</Name>
      <LatLonBoundingBox minx="1.0" miny="2.0" maxx="3.0" maxy="4.0"/>
    </Layer></Root>"""
    _, bbox, _ = extract(xml)
    assert bbox == [1.0, 2.0, 3.0, 4.0]


def test_latlonbbox_missing_attribute_is_rejected():
    xml = b"""<Root><Layer><Name>roads</Name>
      <LatLonBoundingBox minx="1.0" miny="2.0" maxx="3.0"/>
    </Layer></Root>"""
    _, bbox, notes = extract(xml)
    assert bbox is None
    assert any("bbox left unknown" in n for n in notes)


def test_wgs84bbox_corners_wmts():
    _, bbox, _ = extract(WMTS_100, "ortho")
    assert bbox == [100.0, 20.0, 120.0, 40.0]


def test_wgs84bbox_degenerate_corner_rejected():
    xml = b"""<Root><Layer><Name>roads</Name>
      <WGS84BoundingBox><LowerCorner>1.0</LowerCorner>
      <UpperCorner>2.0 3.0</UpperCorner></WGS84BoundingBox>
    </Layer></Root>"""
    _, bbox, notes = extract(xml)
    assert bbox is None
    assert any("bbox left unknown" in n for n in notes)


def test_bbox_inherited_from_parent_when_layer_lacks_one():
    xml = b"""<Root><Layer><Name>root</Name>
      <EX_GeographicBoundingBox>
        <westBoundLongitude>0</westBoundLongitude>
        <eastBoundLongitude>10</eastBoundLongitude>
        <southBoundLatitude>0</southBoundLatitude>
        <northBoundLatitude>10</northBoundLatitude>
      </EX_GeographicBoundingBox>
      <Layer><Name>roads</Name></Layer>
    </Layer></Root>"""
    _, bbox, _ = extract(xml)
    assert bbox == [0.0, 0.0, 10.0, 10.0]


def test_incomplete_bbox_on_layer_falls_through_to_ancestor():
    """叶子层 EX_GeographicBoundingBox 缺子元素 → 该层视为无声明，
    继承祖先的完整声明（而不是丢弃整个链路）。"""
    xml = b"""<Root><Layer><Name>root</Name>
      <EX_GeographicBoundingBox>
        <westBoundLongitude>0</westBoundLongitude>
        <eastBoundLongitude>10</eastBoundLongitude>
        <southBoundLatitude>0</southBoundLatitude>
        <northBoundLatitude>10</northBoundLatitude>
      </EX_GeographicBoundingBox>
      <Layer><Name>roads</Name>
        <EX_GeographicBoundingBox>
          <westBoundLongitude>5</westBoundLongitude>
        </EX_GeographicBoundingBox>
      </Layer>
    </Layer></Root>"""
    _, bbox, notes = extract(xml)
    assert bbox == [0.0, 0.0, 10.0, 10.0]
    assert not any("malformed" in n for n in notes)


def test_implausible_bbox_ignored_with_note_and_ancestor_used():
    xml = b"""<Root><Layer><Name>root</Name>
      <EX_GeographicBoundingBox>
        <westBoundLongitude>0</westBoundLongitude>
        <eastBoundLongitude>10</eastBoundLongitude>
        <southBoundLatitude>0</southBoundLatitude>
        <northBoundLatitude>10</northBoundLatitude>
      </EX_GeographicBoundingBox>
      <Layer><Name>roads</Name>
        <EX_GeographicBoundingBox>
          <westBoundLongitude>999</westBoundLongitude>
          <eastBoundLongitude>1000</eastBoundLongitude>
          <southBoundLatitude>20</southBoundLatitude>
          <northBoundLatitude>30</northBoundLatitude>
        </EX_GeographicBoundingBox>
      </Layer>
    </Layer></Root>"""
    _, bbox, notes = extract(xml)
    assert bbox == [0.0, 0.0, 10.0, 10.0]
    assert any("malformed or outside WGS84" in n for n in notes)


def test_reversed_bbox_corners_rejected_as_antimeridian_naive():
    """w>e（未做反经线跨接的角序）诚实拒绝 → None + note，绝不猜。"""
    xml = b"""<Root><Layer><Name>roads</Name>
      <EX_GeographicBoundingBox>
        <westBoundLongitude>170</westBoundLongitude>
        <eastBoundLongitude>-170</eastBoundLongitude>
        <southBoundLatitude>20</southBoundLatitude>
        <northBoundLatitude>30</northBoundLatitude>
      </EX_GeographicBoundingBox></Layer></Root>"""
    _, bbox, notes = extract(xml)
    assert bbox is None
    assert any("malformed or outside WGS84" in n for n in notes)


def test_projected_only_bounding_box_never_leaks_as_geographic():
    """投影 <BoundingBox>（米制）不是三种地理编码之一——绝不冒充
    WGS84 bbox。"""
    xml = b"""<Root><Layer><Name>roads</Name>
      <BoundingBox CRS="EPSG:3857" minx="1" miny="2" maxx="3" maxy="4"/>
    </Layer></Root>"""
    _, bbox, notes = extract(xml)
    assert bbox is None
    assert any("bbox left unknown" in n for n in notes)


def test_no_bbox_anywhere_returns_none_with_note():
    xml = b"""<Root><Layer><Name>roads</Name><CRS>EPSG:4326</CRS></Layer></Root>"""
    _, bbox, notes = extract(xml)
    assert bbox is None
    assert any("bbox left unknown" in n and "fabricated" in n for n in notes)


# ── describe()：normalize_crs 去重保序 + 主 srs 优先级 ───────────────────────


def test_describe_dedup_preserves_first_seen_order():
    adapter = make_adapter(
        b"""<Root version="1.1.1"><Layer><Name>roads</Name>
          <SRS>EPSG:3857 EPSG:4326 EPSG:3857</SRS></Layer></Root>"""
    )
    desc = adapter.describe("roads")
    assert desc.metadata["normalized_crs"] == ["EPSG:3857", "EPSG:4326"]
    assert desc.metadata["advertised_crs"] == ["EPSG:3857", "EPSG:4326", "EPSG:3857"]
    # 主 srs：4326/CRS84 优先于首个声明项（与是否声明 bbox 无关）
    assert desc.srs == "EPSG:4326"


def test_describe_equivalent_forms_dedup_to_one_canonical():
    adapter = make_adapter(
        b"""<Root><Layer><Name>roads</Name>
          <CRS>EPSG:4326</CRS>
          <CRS>urn:ogc:def:crs:EPSG::4326</CRS>
          <CRS>4326</CRS>
        </Layer></Root>"""
    )
    desc = adapter.describe("roads")
    assert desc.metadata["normalized_crs"] == ["EPSG:4326"]
    assert len(desc.metadata["advertised_crs"]) == 3


def test_describe_crs84_equivalents_dedup_to_crs84_marker():
    adapter = make_adapter(
        b"""<Root><Layer><Name>ortho</Name>
          <CRS>CRS84</CRS>
          <CRS>http://www.opengis.net/def/crs/OGC/1.3/CRS84</CRS>
        </Layer></Root>"""
    )
    desc = adapter.describe("ortho")
    assert desc.metadata["normalized_crs"] == ["CRS84"]
    assert desc.srs == "CRS84"


def test_describe_urn_form_normalized_via_epsg_branch():
    """URN 冒号形式实证（G10 目标项 3）：metadata.py 的 _CRS_URI_RE 只匹配
    http URI，但 ``EPSG in up`` 尾部分支接住 URN → EPSG:4326。锁死该行为。"""
    adapter = make_adapter(
        b"""<Root><Layer><Name>roads</Name>
          <CRS>urn:ogc:def:crs:EPSG::4326</CRS></Layer></Root>"""
    )
    desc = adapter.describe("roads")
    assert desc.metadata["normalized_crs"] == ["EPSG:4326"]
    assert desc.srs == "EPSG:4326"


def test_describe_unknown_crs_dropped_but_valid_ones_kept():
    adapter = make_adapter(
        b"""<Root><Layer><Name>roads</Name>
          <CRS>unknown</CRS><CRS></CRS><CRS>EPSG:4326</CRS>
        </Layer></Root>"""
    )
    desc = adapter.describe("roads")
    assert desc.metadata["normalized_crs"] == ["EPSG:4326"]
    assert desc.srs == "EPSG:4326"


def test_describe_no_crs_primary_is_none_with_notes():
    adapter = make_adapter(
        b"""<Root><Layer><Name>roads</Name><Title>x</Title></Layer></Root>"""
    )
    desc = adapter.describe("roads")
    assert desc.srs is None
    assert desc.metadata["normalized_crs"] == []
    assert any("no CRS/SRS" in n for n in desc.metadata["notes"])


def test_describe_primary_is_first_declared_when_no_geographic_crs():
    adapter = make_adapter(
        b"""<Root><Layer><Name>roads</Name>
          <CRS>EPSG:3857</CRS><CRS>EPSG:3857</CRS></Layer></Root>"""
    )
    desc = adapter.describe("roads")
    assert desc.srs == "EPSG:3857"
    assert desc.metadata["normalized_crs"] == ["EPSG:3857"]


def test_describe_axis_order_note_only_for_130_with_4326():
    adapter = make_adapter(WMS_130_NESTED)
    desc = adapter.describe("roads")
    assert "axis_order_note" in desc.metadata
    assert desc.metadata["bbox_crs"] == "EPSG:4326"


def test_describe_no_axis_order_note_for_crs84():
    xml = b"""<?xml version="1.0"?>
    <Root version="1.3.0"><Layer><Name>ortho</Name>
      <CRS>CRS84</CRS>
      <EX_GeographicBoundingBox>
        <westBoundLongitude>0</westBoundLongitude>
        <eastBoundLongitude>10</eastBoundLongitude>
        <southBoundLatitude>0</southBoundLatitude>
        <northBoundLatitude>10</northBoundLatitude>
      </EX_GeographicBoundingBox>
    </Layer></Root>"""
    adapter = make_adapter(xml)
    desc = adapter.describe("ortho")
    assert "axis_order_note" not in desc.metadata
    assert desc.srs == "CRS84"
    assert desc.bbox == [0.0, 0.0, 10.0, 10.0]


def test_no_axis_order_note_for_wms_111_even_with_4326():
    """axis_order_note 的版本维度反例：1.1.x + EPSG:4326 无轴序陷阱。"""
    xml = b"""<?xml version="1.0"?>
    <Root version="1.1.1"><Layer><Name>roads</Name>
      <SRS>EPSG:4326</SRS>
      <LatLonBoundingBox minx="0" miny="0" maxx="10" maxy="10"/>
    </Layer></Root>"""
    adapter = make_adapter(xml)
    desc = adapter.describe("roads")
    assert "axis_order_note" not in desc.metadata
    assert desc.srs == "EPSG:4326"


def test_describe_case_sensitive_dedup_is_current_behavior():
    """特性化测试（G10 登记，不改动）：normalize_crs 对 `EPSG:` 前缀是
    原样透传（test_data_fabric_metadata.py 显式断言 `epsg:3857` 透传），
    因此大小写变体在去重后各占一位——这是既定行为，若维护者决定大小写
    归一需同步修改本测试与 normalize_crs。"""
    adapter = make_adapter(
        b"""<Root><Layer><Name>roads</Name>
          <CRS>EPSG:4326</CRS><CRS>epsg:4326</CRS></Layer></Root>"""
    )
    desc = adapter.describe("roads")
    assert desc.metadata["normalized_crs"] == ["EPSG:4326", "epsg:4326"]
    assert desc.srs == "EPSG:4326"


def test_describe_layer_not_found_still_honest():
    adapter = make_adapter(WMS_130_NESTED)
    desc = adapter.describe("ghost")
    assert desc.srs is None
    assert desc.bbox is None
    assert desc.metadata["bbox_crs"] is None
    assert any("ghost" in n and "not found" in n for n in desc.metadata["notes"])


# ── query()：链路末端 GetMap 的 CRS/BBOX 配对 ────────────────────────────────


def test_query_emits_crs_and_bbox_for_geographic_crs():
    adapter = make_adapter(WMS_130_NESTED)
    res = adapter.query("roads", _qs(bbox=[116.0, 39.0, 117.0, 40.0]))
    assert "CRS=EPSG:4326" in res.metadata["getmap_url"]
    # BBOX 用 describe 时 capabilities 声明的 WGS84 extent（不是查询 bbox）
    assert "BBOX=-180.0,-90.0,180.0,90.0" in res.metadata["getmap_url"]
    assert res.metadata["axis_order_note"]


def test_query_omits_bbox_for_projected_crs_no_silent_reprojection():
    xml = b"""<Root><Layer><Name>roads</Name><CRS>EPSG:3857</CRS>
      <EX_GeographicBoundingBox>
        <westBoundLongitude>0</westBoundLongitude>
        <eastBoundLongitude>10</eastBoundLongitude>
        <southBoundLatitude>0</southBoundLatitude>
        <northBoundLatitude>10</northBoundLatitude>
      </EX_GeographicBoundingBox></Layer></Root>"""
    adapter = make_adapter(xml)
    res = adapter.query("roads", _qs())
    assert "CRS=EPSG:3857" in res.metadata["getmap_url"]
    assert "BBOX=" not in res.metadata["getmap_url"]
    assert "not geographic" in res.metadata["bbox_note"]


def test_query_omits_crs_and_bbox_when_chain_declares_neither():
    adapter = make_adapter(
        b"""<Root><Layer><Name>roads</Name><Title>x</Title></Layer></Root>"""
    )
    res = adapter.query("roads", _qs())
    assert "CRS=" not in res.metadata["getmap_url"]
    assert "BBOX=" not in res.metadata["getmap_url"]
    assert "advertised crs unknown" in res.metadata["crs_note"]
    assert "bbox_note" in res.metadata


def test_query_projected_crs_containing_4326_substring_never_gets_bbox():
    """G10 回归（query 地理判定缺陷）：EPSG:24326 的码内含子串 "4326"，
    旧子串匹配把它误判成地理 CRS，把 WGS84 度值 BBOX 静默配给投影
    CRS（正是 MAJOR-1 要堵的静默谎言）。地理身份必须精确匹配。"""
    xml = b"""<Root><Layer><Name>roads</Name><CRS>EPSG:24326</CRS>
      <EX_GeographicBoundingBox>
        <westBoundLongitude>0</westBoundLongitude>
        <eastBoundLongitude>10</eastBoundLongitude>
        <southBoundLatitude>0</southBoundLatitude>
        <northBoundLatitude>10</northBoundLatitude>
      </EX_GeographicBoundingBox></Layer></Root>"""
    adapter = make_adapter(xml)
    res = adapter.query("roads", _qs())
    assert "CRS=EPSG:24326" in res.metadata["getmap_url"]
    assert "BBOX=" not in res.metadata["getmap_url"]
    assert "not geographic" in res.metadata["bbox_note"]


def test_query_bbox_known_crs_unknown_gets_honest_note():
    """G10 回归（note 口径缺陷）：bbox 已知但 CRS 未知时，BBOX 省略的
    原因是「服务器默认 CRS 未知」——不是「选定 CRS 非地理」。"""
    xml = b"""<Root><Layer><Name>roads</Name>
      <EX_GeographicBoundingBox>
        <westBoundLongitude>0</westBoundLongitude>
        <eastBoundLongitude>10</eastBoundLongitude>
        <southBoundLatitude>0</southBoundLatitude>
        <northBoundLatitude>10</northBoundLatitude>
      </EX_GeographicBoundingBox></Layer></Root>"""
    adapter = make_adapter(xml)
    res = adapter.query("roads", _qs())
    assert "CRS=" not in res.metadata["getmap_url"]
    assert "BBOX=" not in res.metadata["getmap_url"]
    assert "default CRS is unknown" in res.metadata["bbox_note"]
    assert "advertised crs unknown" in res.metadata["crs_note"]


def test_query_geographic_detection_still_matches_canonical_forms():
    """精确化后，地理身份的正例不得缩水：EPSG:4326（含小写透传）、
    CRS84、CRS:84、OGC URI 归一形式仍全部配对 BBOX。"""
    forms = {
        "EPSG:4326": b"<CRS>EPSG:4326</CRS>",
        "epsg:4326": b"<CRS>epsg:4326</CRS>",
        "CRS84": b"<CRS>CRS84</CRS>",
        "CRS:84": b"<CRS>CRS:84</CRS>",
        "CRS84-URI": b"<CRS>http://www.opengis.net/def/crs/OGC/1.3/CRS84</CRS>",
    }
    for label, crs_el in forms.items():
        xml = (
            b"<Root><Layer><Name>roads</Name>" + crs_el
            + b"<EX_GeographicBoundingBox>"
            b"<westBoundLongitude>0</westBoundLongitude>"
            b"<eastBoundLongitude>10</eastBoundLongitude>"
            b"<southBoundLatitude>0</southBoundLatitude>"
            b"<northBoundLatitude>10</northBoundLatitude>"
            b"</EX_GeographicBoundingBox></Layer></Root>"
        )
        res = make_adapter(xml).query("roads", _qs())
        assert "BBOX=0.0,0.0,10.0,10.0" in res.metadata["getmap_url"], label


def _qs(bbox=None):
    from app.schemas.data_fabric_schema import QuerySpec

    return QuerySpec(limit=5, bbox=bbox)


@pytest.mark.parametrize(
    "raw,expected",
    [
        # 链路入口约定：normalize_crs 的 URN / CRS84 / 裸码行为在 adapter
        # 场景下的关键回归锚点（本体分支测试在 test_data_fabric_metadata.py）。
        ("urn:ogc:def:crs:EPSG::4326", "EPSG:4326"),
        ("URN:OGC:DEF:CRS:EPSG::3857", "EPSG:3857"),
        ("http://www.opengis.net/def/crs/EPSG/0/3857", "EPSG:3857"),
        ("EPSG:4326 ", "EPSG:4326"),
        (None, None),
        ("", None),
    ],
)
def test_adapter_pipeline_crs_normalization_anchors(raw, expected):
    from app.services.data_fabric.metadata import normalize_crs

    assert normalize_crs(raw) == expected
