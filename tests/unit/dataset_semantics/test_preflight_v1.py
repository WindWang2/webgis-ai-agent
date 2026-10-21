"""G01/WP-C preflight 测试：多数据联接 metadata-only 兼容判据。

验收矩阵：
- 兼容集（同 CRS/相交 bbox/同单位/时间重叠）→ compatible；
- 混合 CRS → CRS_MISMATCH（typed: reproject）；未知 CRS → CRS_UNKNOWN；
- bbox 不相交 → SPATIAL_DISJOINT（typed: 相交数据或裁剪）；
- 时间不重叠 → TEMPORAL_DISJOINT；部分未知 → TEMPORAL unknown；
- 同名字段单位冲突 → UNIT_CONFLICT；部分未知 → UNIT_UNVERIFIED；
- density 无分母证据 → DENSITY_NEEDS_DENOMINATOR（typed: 供分母或改数量）；
- 期望角色缺席 → ROLE_MISSING；
- unknown ≠ pass：证据不足给 unknown 而非 compatible；
- 零数据扫描：全链只读 descriptor（session_data.get 调用数 = 0）。
"""

from app.lib.gis.dataset_profile import DatasetProfile
from app.services.dataset_semantics import derive_descriptor
from app.services.dataset_semantics.preflight import (
    CODE_CRS_MISMATCH,
    CODE_CRS_UNKNOWN,
    CODE_DENSITY_NEEDS_DENOMINATOR,
    CODE_ROLE_MISSING,
    CODE_SPATIAL_DISJOINT,
    CODE_TEMPORAL_DISJOINT,
    CODE_UNIT_CONFLICT,
    preflight_multi_dataset,
)


def _descriptor(*, key="ref:d", geometry=("Point",), crs="EPSG:4326",
                bbox=None, fields=None, count=10, coverage=None):
    fields = fields or {"v": "number"}
    p = DatasetProfile(
        source="ref_descriptor", feature_count=count,
        geometry_types=list(geometry), crs=crs,
        fields={k: ("number" if k in ("人口", "pop", "v", "count") else "string")
                for k in fields},
        numeric_fields=[k for k in fields
                        if k in ("人口", "pop", "v", "count")],
        fields_status="explicit",
    )
    feats = []
    for i in range(count):
        props = {k: (i if k in ("人口", "pop", "v", "count") else f"x{i}")
                 for k in fields}
        if geometry[0] in ("Point", "MultiPoint"):
            feats.append({"geometry": {"type": "Point",
                                       "coordinates": [104.0, 30.6]},
                          "properties": props})
        else:
            feats.append({"geometry": {"type": "Polygon", "coordinates": [
                [[104.0, 30.6], [104.1, 30.6], [104.1, 30.7], [104.0, 30.6]]]},
                "properties": props})
    d = derive_descriptor(p, dataset_key=key, features=feats)
    updates = {}
    if bbox:
        updates["bbox"] = list(bbox)
    if coverage:
        updates["temporal"] = d.temporal.model_copy(update={
            "has_time_field": True,
            "coverage_start": coverage[0], "coverage_end": coverage[1]})
    return d.model_copy(update=updates) if updates else d


def test_compatible_set_passes():
    a = _descriptor(key="a", bbox=[104.0, 30.6, 104.2, 30.8],
                    coverage=("2020-01", "2020-12"))
    b = _descriptor(key="b", geometry=("Polygon",), bbox=[104.1, 30.65, 104.3, 30.9],
                    fields={"区名": "str"}, coverage=("2020-06", "2020-12"))
    rep = preflight_multi_dataset(
        {"subject": a, "boundary": b},
        expected_roles=("subject", "boundary"))
    assert rep.verdict == "compatible"
    assert rep.gaps == []
    by_check = {c["check"]: c["result"] for c in rep.checks}
    assert by_check["role_coverage"] == "pass"
    assert by_check["crs_compatibility"] == "pass"
    assert by_check["spatial_overlap"] == "pass"


def test_mixed_crs_gap():
    a = _descriptor(key="a", crs="EPSG:4326")
    b = _descriptor(key="b", crs="EPSG:3857")
    rep = preflight_multi_dataset({"a": a, "b": b})
    assert rep.verdict == "gaps"
    gap = next(g for g in rep.gaps if g.code == CODE_CRS_MISMATCH)
    assert gap.alternatives[0].action == "reproject"


def test_unknown_crs_is_unknown_not_pass():
    a = _descriptor(key="a", crs="")
    rep = preflight_multi_dataset({"a": a})
    assert any(g.code == CODE_CRS_UNKNOWN for g in rep.gaps)


def test_spatial_disjoint_gap():
    a = _descriptor(key="a", bbox=[104.0, 30.6, 104.1, 30.7])
    b = _descriptor(key="b", bbox=[116.0, 39.9, 116.1, 40.0])   # 北京
    rep = preflight_multi_dataset({"a": a, "b": b})
    gap = next(g for g in rep.gaps if g.code == CODE_SPATIAL_DISJOINT)
    assert set(gap.refs) == {"a", "b"}
    actions = {alt.action for alt in gap.alternatives}
    assert "provide_overlapping_data" in actions


def test_temporal_disjoint_and_partial():
    y2020 = _descriptor(key="t1", coverage=("2020-01", "2020-12"))
    y2025 = _descriptor(key="t2", coverage=("2025-01", "2025-12"))
    rep = preflight_multi_dataset({"baseline": y2020, "target_time": y2025})
    assert any(g.code == CODE_TEMPORAL_DISJOINT for g in rep.gaps)
    # 同期 → compatible（时间判据 pass）。
    y2020b = _descriptor(key="t3", coverage=("2020-06", "2020-12"))
    rep2 = preflight_multi_dataset({"baseline": y2020, "target_time": y2020b})
    assert not any(g.code == CODE_TEMPORAL_DISJOINT for g in rep2.gaps)


def test_unit_conflict_and_unverified():
    # 同名字段 v：一个描述单位 m，另一个 km → 冲突。
    a = _descriptor(key="a", fields={"v": "number"})
    a = a.model_copy(update={"fields": [
        f.model_copy(update={"unit": "m", "measurement_kind": "length"})
        for f in a.fields]})
    b = _descriptor(key="b", fields={"v": "number"})
    b = b.model_copy(update={"fields": [
        f.model_copy(update={"unit": "km", "measurement_kind": "length"})
        for f in b.fields]})
    rep = preflight_multi_dataset({"a": a, "b": b})
    gap = next(g for g in rep.gaps if g.code == CODE_UNIT_CONFLICT)
    assert gap.alternatives[0].action == "clarify_units"
    # 部分未知 → UNVERIFIED（unknown ≠ pass）。
    c = _descriptor(key="c", fields={"v": "number"})  # kind 在场、单位未知
    c = c.model_copy(update={"fields": [
        f.model_copy(update={"measurement_kind": "length"})
        for f in c.fields]})
    rep2 = preflight_multi_dataset({"a": a, "c": c})
    assert any(g.code == "PREFLIGHT_UNIT_UNVERIFIED" for g in rep2.gaps)


def test_density_needs_denominator():
    schools = _descriptor(key="schools")  # 数量字段，无分母、无面
    rep = preflight_multi_dataset(
        {"subject": schools}, analysis_kind="density")
    gap = next(g for g in rep.gaps if g.code == CODE_DENSITY_NEEDS_DENOMINATOR)
    actions = {alt.action for alt in gap.alternatives}
    assert actions == {"provide_denominator", "switch_to_count"}
    # 有人口分母 → pass。
    pop = _descriptor(key="pop", geometry=("Polygon",), fields={"人口": "number"})
    rep2 = preflight_multi_dataset(
        {"subject": schools, "denominator": pop}, analysis_kind="density")
    assert not any(g.code == CODE_DENSITY_NEEDS_DENOMINATOR
                   for g in rep2.gaps)
    # 仅有面边界（面积分母可派生）→ pass（polygon_area_denominator）。
    bounds = _descriptor(key="bounds", geometry=("Polygon",),
                         fields={"区名": "str"})
    rep3 = preflight_multi_dataset(
        {"subject": schools, "boundary": bounds}, analysis_kind="density")
    assert not any(g.code == CODE_DENSITY_NEEDS_DENOMINATOR
                   for g in rep3.gaps)


def test_missing_expected_role_gap():
    schools = _descriptor(key="schools")
    rep = preflight_multi_dataset(
        {"subject": schools},
        expected_roles=("subject", "boundary", "denominator"))
    assert rep.verdict == "gaps"
    missing = [g for g in rep.gaps if g.code == CODE_ROLE_MISSING]
    assert {g.detail.split()[1] for g in missing} >= {"boundary", "denominator"}
    assert all(g.alternatives[0].action == "provide_role_data" for g in missing)


def test_empty_input_unknown():
    rep = preflight_multi_dataset({})
    assert rep.verdict == "unknown"
    assert rep.checks[0]["result"] == "unknown"


def test_zero_data_scans(monkeypatch):
    """preflight 全链零数据载荷读：session_data 面不触达。"""
    import app.services.session_data as session_data_mod

    calls = {"n": 0}
    orig = session_data_mod.session_data_manager

    class _Counting:
        def __getattr__(self, name):
            if name in ("get", "get_ref", "get_payload", "get_shared"):
                calls["n"] += 1
            return getattr(orig, name)

    monkeypatch.setattr(
        session_data_mod, "session_data_manager", _Counting())
    a = _descriptor(key="a", bbox=[104.0, 30.6, 104.1, 30.7])
    b = _descriptor(key="b", geometry=("Polygon",), bbox=[104.05, 30.65, 104.2, 30.8])
    preflight_multi_dataset({"subject": a, "boundary": b},
                            expected_roles=("subject", "boundary"),
                            analysis_kind="density")
    assert calls["n"] == 0, "preflight 必须零数据扫描"


# ── review P1/P2 修复回归 ──────────────────────────────────────────────

def test_verdict_unknown_when_checks_lack_evidence():
    """P1：判据证据不足 → verdict unknown（不判 compatible）。"""
    a = _descriptor(key="a")  # 无 bbox/无时间证据
    b = _descriptor(key="b")
    rep = preflight_multi_dataset({"a": a, "b": b})
    assert rep.verdict == "unknown"
    by_check = {c["check"]: c["result"] for c in rep.checks}
    assert by_check["spatial_overlap"] == "unknown"
    # 静态无时间证据：时间判据 not_applicable（不误伤），unknown 来自空间。
    assert by_check["temporal_overlap"] == "not_applicable"


def test_open_ended_coverage_no_false_disjoint():
    """P1：end 不可解析（open-ended）不得塌缩成假 TEMPORAL_DISJOINT。"""
    a = _descriptor(key="a", coverage=("2020-01", "present"))
    b = _descriptor(key="b", coverage=("2021-01", "2021-12"))
    rep = preflight_multi_dataset({"a": a, "b": b})
    assert not any(g.code == "PREFLIGHT_TEMPORAL_DISJOINT"
                   for g in rep.gaps)
    assert rep.verdict == "unknown"  # 证据不足 → 不下兼容结论


def test_same_year_different_months_not_false_pass():
    """P2：同年不同月（1-2 月 vs 11-12 月）不得假 overlap。"""
    a = _descriptor(key="a", coverage=("2021-01", "2021-02"))
    b = _descriptor(key="b", coverage=("2021-11", "2021-12"))
    rep = preflight_multi_dataset({"a": a, "b": b})
    assert any(g.code == "PREFLIGHT_TEMPORAL_DISJOINT" for g in rep.gaps)
