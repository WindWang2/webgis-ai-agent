"""中英文高风险语义 corpus 测试（ADR-0215 V8 验收矩阵）。

corpus：tests/fixtures/dataset_semantic_corpus/cases.json
覆盖：数量vs密度 / 比率vs总量 / 百分比vs分数 / 带符号变化 / 类别 /
时间字段 / 坐标字段 / 未知单位 / 冲突单位 —— 每个风险维度 zh/en 双语
对称用例。期望值锁定 #1488（ADR-0207）推导的实测行为，驱动全链
（derive → descriptor → compare）断言，防止词表/规则静默漂移。
"""
import json
from pathlib import Path

import pytest

from app.lib.gis.dataset_profile import DatasetProfile
from app.services.dataset_semantics import derive_descriptor

CORPUS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / \
    "dataset_semantic_corpus" / "cases.json"


def _load_corpus():
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def _case_descriptor(case):
    fields = {f["name"]: f["dtype"] for f in case["fields"]}
    numeric = [f["name"] for f in case["fields"] if f["dtype"] == "number"]
    p = DatasetProfile(
        source="ref_descriptor",
        feature_count=max(len(f["samples"]) for f in case["fields"]),
        geometry_types=["Polygon"],
        crs="EPSG:4326",
        fields=fields,
        numeric_fields=numeric,
        fields_status="explicit",
    )
    max_len = max(len(f["samples"]) for f in case["fields"])
    features = [
        {"properties": {
            f["name"]: (f["samples"][i % len(f["samples"])]
                        if f["samples"] else None)
            for f in case["fields"]}}
        for i in range(max_len)
    ]
    overrides = case.get("unit_overrides") or {}
    if isinstance(overrides, dict) and not overrides:
        overrides = None
    return derive_descriptor(
        p, dataset_key=f"corpus:{case['id']}", features=features,
        unit_overrides=overrides)


def _entry(dsd, name):
    for f in dsd.fields:
        if f.name == name:
            return f
    raise AssertionError(f"field {name!r} missing in descriptor")


def _assert_expectation(entry, expect):
    if "measurement_kind" in expect:
        assert entry.measurement_kind == expect["measurement_kind"], (
            f"{entry.name}: kind {entry.measurement_kind!r} != "
            f"{expect['measurement_kind']!r}")
    if "unit_dimension" in expect:
        assert entry.unit_dimension == expect["unit_dimension"], (
            f"{entry.name}: dim {entry.unit_dimension!r} != "
            f"{expect['unit_dimension']!r}")
    if "unit" in expect:
        assert entry.unit == expect["unit"], (
            f"{entry.name}: unit {entry.unit!r} != {expect['unit']!r}")
    if "roles" in expect:
        for role in expect["roles"]:
            assert role in entry.roles, (
                f"{entry.name}: role {role!r} not in {entry.roles}")
    if "center_hint" in expect:
        assert entry.center_hint == expect["center_hint"]
    if "domain_hint" in expect:
        assert entry.domain_hint == expect["domain_hint"], (
            f"{entry.name}: domain {entry.domain_hint} != {expect['domain_hint']}")
    for code in expect.get("checks", []):
        codes = {c["code"] for c in entry.checks}
        assert code in codes, f"{entry.name}: check {code} not in {codes}"


def test_corpus_schema_version():
    corpus = _load_corpus()
    assert corpus["schema_version"] == 1
    assert len(corpus["cases"]) >= 12


@pytest.mark.parametrize("case", _load_corpus()["cases"],
                         ids=[c["id"] for c in _load_corpus()["cases"]])
def test_corpus_case(case):
    dsd = _case_descriptor(case)
    assert dsd.descriptor_fingerprint.startswith("dsd-v1:")
    for field_spec in case["fields"]:
        entry = _entry(dsd, field_spec["name"])
        _assert_expectation(entry, field_spec["expect"])


@pytest.mark.parametrize("case_id_zh,case_id_en", [
    ("count_vs_density_zh", "count_vs_density_en"),
    ("percentage_vs_fraction_zh", "percentage_vs_fraction_en"),
    ("signed_change_zh", "signed_change_en"),
    ("category_zh", "category_en"),
    ("unknown_unit_zh", "unknown_unit_en"),
])
def test_corpus_bilingual_symmetry(case_id_zh, case_id_en):
    """双语对称：同风险维度的 zh/en 用例得出同一 measurement kind。

    注意 ratio_vs_total 不进对称表是**有意的**：zh「老龄化率」按名称证据
    归 ratio（fraction），en「unemployment_rate」的 _rate$ 词干归 rate
    （并触发 RATE_MISSING_TEMPORAL）—— 中英文率类命名的推导差异是
    corpus 记录的事实之一，由 per-case 测试分别锁定。
    """
    corpus = _load_corpus()
    cases = {c["id"]: c for c in corpus["cases"]}
    zh, en = cases[case_id_zh], cases[case_id_en]
    kinds_zh = {f["name"]: _entry(_case_descriptor(zh), f["name"]).measurement_kind
                for f in zh["fields"]}
    kinds_en = {f["name"]: _entry(_case_descriptor(en), f["name"]).measurement_kind
                for f in en["fields"]}
    # 按用例内顺序对齐（同风险维度的字段顺序一致）。
    assert list(kinds_zh.values()) == list(kinds_en.values())


def test_corpus_determinism():
    """同 corpus 输入恒同指纹（确定性回归锁）。"""
    corpus = _load_corpus()
    for case in corpus["cases"][:6]:
        d1 = _case_descriptor(case)
        d2 = _case_descriptor(case)
        assert d1.descriptor_fingerprint == d2.descriptor_fingerprint


def test_corpus_conflicting_units_fail_closed_codes():
    """冲突单位：角色期望维度 ≠ 单位维度 → 稳定检查码在场（不静默猜测）。"""
    corpus = _load_corpus()
    cases = {c["id"]: c for c in corpus["cases"]}
    for cid in ("conflicting_units_zh", "conflicting_units_en"):
        dsd = _case_descriptor(cases[cid])
        found = any(
            any(c["code"] == "UNIT_DIMENSION_MISMATCH" for c in f.checks)
            for f in dsd.fields
        )
        assert found, f"{cid}: UNIT_DIMENSION_MISMATCH check missing"
