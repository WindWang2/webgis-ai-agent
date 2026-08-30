"""Data Runtime V2 契约层单元测试（P1/P2/P3）。

覆盖：
- DatasetProfile：三源 O(1) 构造、有界性、resolver 适配、诚实缺省；
- ArtifactTypeRegistry：service 层可产出的 artifact_type 全部注册（词表
  不漂移守卫）；
- validate_output_contract：几何族错配 / 未注册声明 / 空产物 / CRS 缺失
  / 未知不判死 / 空 contract；
- AlgorithmResolver input_type_mismatch（V2 新拒绝码，仅已知类型生效）；
- registry.validate() 契约一致性：approximate ∧ deterministic 矛盾、
  unit_requirements 词表封闭。
"""

from app.lib.gis.algorithm_registry import AlgorithmDescriptor, get_algorithm_registry
from app.lib.gis.algorithm_resolver import AlgorithmResolver
from app.lib.gis.artifacts import get_artifact_type_registry
from app.lib.gis.contract_validation import (
    C_CRS_UNDECLARED,
    C_EMPTY_ARTIFACT,
    C_GEOMETRY_KIND_MISMATCH,
    C_UNREGISTERED_TYPE,
    validate_output_contract,
)
from app.lib.gis.capability_registry import get_capability_registry
from app.lib.gis.dataset_profile import (
    MAX_PROFILE_FIELDS,
    DatasetProfile,
)


# ── DatasetProfile ─────────────────────────────────────────────────────

def test_profile_from_ref_descriptor_o1():
    desc = {
        "ref_id": "ref:geojson-a",
        "feature_count": 120,
        "geometry_types": ["Point", "MultiPoint"],
        "bbox": [104.0, 30.0, 105.0, 31.0],
        "crs": "",
        "field_schema": {"count": {"type": "number"}, "name": {"type": "string"}},
        "field_schema_complete": True,
        "estimated_bytes": 2048,
    }
    p = DatasetProfile.from_ref_descriptor(desc)
    assert p.source == "ref_descriptor"
    assert p.feature_count == 120
    assert p.geometry_kind == "point"
    assert p.fields_status == "explicit"
    assert p.numeric_fields == ["count"]
    assert p.categorical_fields == ["name"]
    # resolver 适配出口保持既有 camelCase 契约
    rp = p.to_resolver_profile()
    assert rp["featureCount"] == 120
    assert rp["geometryTypes"] == ["Point", "MultiPoint"]  # descriptor 保存序
    assert rp["artifactType"] is None  # 未知如实 None


def test_profile_unknown_fields_honest():
    desc = {"feature_count": 5, "geometry_types": ["Point"]}
    p = DatasetProfile.from_ref_descriptor(desc)
    assert p.fields == {}
    assert p.fields_status == "unknown"  # schema 不可得 ≠ 无字段
    assert p.bbox is None and p.crs == ""


def test_profile_truncated_schema_is_unknown():
    desc = {
        "feature_count": 5,
        "geometry_types": ["Point"],
        "field_schema": {"a": {"type": "number"}},
        "field_schema_complete": False,
    }
    assert DatasetProfile.from_ref_descriptor(desc).fields_status == "unknown"


def test_profile_field_bound():
    desc = {
        "feature_count": 1,
        "geometry_types": ["Point"],
        "field_schema": {f"f{i}": {"type": "number"} for i in range(200)},
    }
    p = DatasetProfile.from_ref_descriptor(desc)
    assert len(p.fields) == MAX_PROFILE_FIELDS


def test_profile_from_spatial_profile_camel_case():
    prof = {
        "featureCount": 42,
        "geometryTypes": ["Polygon"],
        "bbox": [0, 0, 1, 1],
        "crs": "EPSG:4326",
        "fields": {"value": {"type": "number"}, "kind": {"type": "string"}},
    }
    p = DatasetProfile.from_spatial_profile(prof)
    assert p.geometry_kind == "polygon"
    assert p.to_resolver_profile()["featureCount"] == 42
    assert p.to_resolver_profile()["crs"] == "EPSG:4326"


def test_profile_geometry_kind_priority_matches_resolver():
    # 与 resolver._dominant_geometry 同一归约口径：point > polygon > line。
    assert DatasetProfile.from_spatial_profile(
        {"geometryTypes": ["Point", "Polygon", "LineString"]}
    ).geometry_kind == "point"
    assert DatasetProfile.from_spatial_profile(
        {"geometryTypes": ["LineString", "Polygon"]}
    ).geometry_kind == "polygon"


# ── ArtifactTypeRegistry 词表不漂移 ────────────────────────────────────

def test_service_level_types_registered():
    """infer_artifact_type 可能产出的每个 artifact_type 都必须是注册词。"""
    from app.services.artifact_registry import infer_artifact_type

    reg = get_artifact_type_registry()
    produced = {
        infer_artifact_type("ref:geojson-x"),
        infer_artifact_type("ref:heatmap-x"),
        infer_artifact_type("ref:raster/x"),
        infer_artifact_type("ref:chart-x"),
        infer_artifact_type("ref:whatever-x"),  # 兜底 → feature_collection
    }
    for t in produced:
        assert reg.has(t), f"service-layer artifact_type {t!r} not registered"


def test_registry_counts_and_no_dupes():
    reg = get_artifact_type_registry()
    assert len(reg.all_ids) == reg.count
    assert reg.validate() == []


# ── validate_output_contract ───────────────────────────────────────────

def _point_profile(n=10):
    return DatasetProfile.from_ref_descriptor(
        {"feature_count": n, "geometry_types": ["Point"], "bbox": [0, 0, 1, 1],
         "crs": "EPSG:4326"}
    )


def test_contract_geometry_mismatch_detected():
    findings = validate_output_contract(["stats_table"], _point_profile())
    codes = {f.code for f in findings}
    assert C_GEOMETRY_KIND_MISMATCH in codes  # table 声明 vs point 实况


def test_contract_compatible_output_no_finding():
    # admin_aggregate_table 是 polygon 族，实况 polygon → 无几何错配。
    poly = DatasetProfile.from_ref_descriptor(
        {"feature_count": 3, "geometry_types": ["Polygon"], "crs": "EPSG:4326"}
    )
    assert validate_output_contract(["admin_aggregate_table"], poly) == []


def test_contract_unknown_geometry_not_fatal():
    # descriptor-only 画像无 geometry_types → unknown 不判死。
    p = DatasetProfile.from_ref_descriptor({"feature_count": 3, "crs": "EPSG:4326"})
    findings = validate_output_contract(["polygon_feature_set"], p)
    assert all(f.code != C_GEOMETRY_KIND_MISMATCH for f in findings)


def test_contract_empty_and_crs_disclosed():
    p = DatasetProfile.from_ref_descriptor(
        {"feature_count": 0, "geometry_types": ["Point"]}
    )
    codes = {f.code for f in validate_output_contract(["point_feature_set"], p)}
    assert C_EMPTY_ARTIFACT in codes
    assert C_CRS_UNDECLARED in codes


def test_contract_unregistered_declared_type():
    findings = validate_output_contract(["school_points"], _point_profile())
    assert any(f.code == C_UNREGISTERED_TYPE for f in findings)


def test_contract_no_declaration_no_findings():
    assert validate_output_contract([], _point_profile()) == []
    assert validate_output_contract(["point_feature_set"], None) == []


def test_contract_findings_bounded():
    findings = validate_output_contract(
        ["unregistered_a", "unregistered_b"], _point_profile()
    )
    assert len(findings) <= 8


# ── Resolver input_type_mismatch ───────────────────────────────────────

def test_resolver_rejects_input_type_mismatch():
    """声明输入词表的算法：画像携带已知但不在词表内的 artifactType → 拒绝。"""
    res = AlgorithmResolver().resolve(
        "admin_aggregation",
        profile={
            "featureCount": 100,
            "geometryTypes": ["Point"],
            "artifactType": "stats_table",  # 聚合算法不吃 stats_table
            "fields": {"a": {"type": "number"}},
        },
    )
    assert res.status == "unavailable"
    assert any(r.startswith("input_type_mismatch:") for r in res.rejected)


def test_resolver_passes_compatible_input_type():
    res = AlgorithmResolver().resolve(
        "admin_aggregation",
        profile={
            "featureCount": 100,
            "geometryTypes": ["Point"],
            "artifactType": "poi_feature_set",
        },
    )
    assert res.status == "resolved"
    assert not any(r.startswith("input_type_mismatch") for r in res.rejected)


def test_resolver_unknown_artifact_type_not_fatal():
    """未知/未注册 artifactType ≠ mismatch（诚实缺省，不虚构拒绝）。"""
    res = AlgorithmResolver().resolve(
        "admin_aggregation",
        profile={
            "featureCount": 100,
            "geometryTypes": ["Point"],
            "artifactType": "school_points",
        },
    )
    assert res.status == "resolved"


# ── registry.validate() 契约一致性 ─────────────────────────────────────

def test_builtin_registry_contract_consistent():
    """内建注册表：单位词封闭、无契约漂移。"""
    issues = get_algorithm_registry().validate()
    units = [i for i in issues if "unit_requirements" in i]
    assert units == []


def test_validate_flags_unknown_unit_vocabulary():
    reg = get_algorithm_registry()
    reg.register(AlgorithmDescriptor(
        id="test.bad.unit", name="bad", capabilities=["poi_query"],
        unit_requirements="furlongs", tool_candidates=["search_poi"],
    ))
    try:
        issues = reg.validate()
        assert any("test.bad.unit" in i and "unit_requirements" in i for i in issues)
    finally:
        reg._by_id.pop("test.bad.unit", None)
        reg._by_capability["poi_query"].remove("test.bad.unit")


def test_approximate_and_deterministic_orthogonal():
    """approximate（精度折衷）与 deterministic（可复现）正交 —— 二者组合
    均合法，不做静态矛盾判定（§27 随机性披露由声明者负责）。"""
    reg = get_algorithm_registry()
    reg.register(AlgorithmDescriptor(
        id="test.ortho.approx", name="ok", capabilities=["poi_query"],
        approximate=True, deterministic=True, tool_candidates=["search_poi"],
    ))
    try:
        assert reg.validate() == []
    finally:
        reg._by_id.pop("test.ortho.approx", None)
        reg._by_capability["poi_query"].remove("test.ortho.approx")


def test_capability_registry_artifact_refs_valid():
    assert get_capability_registry().validate() == []
