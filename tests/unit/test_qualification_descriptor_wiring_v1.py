"""Qualification descriptor 接线测试（H08/C6）。

- 基线不变：descriptor 缺省 → compile 输出与基线逐键一致（additive 红线）；
- descriptor 在场 → qualification 消费 descriptor 投影事实（决策面不再
  依赖 profile 二次推导）；
- freshness guard 接线：expected 指纹 ≠ 当前 → DESCRIPTOR_STALE_* 降级
  贯穿到 compilation.data_qualifications；
- primary_session_descriptor：确定性首个命中 / 空会话 None。
"""
import uuid

import pytest

from app.lib.gis.dataset_profile import DatasetProfile
from app.services.dataset_semantics import (
    DatasetSemanticStore,
    derive_descriptor,
    primary_session_descriptor,
    reset_dataset_semantic_store,
)
from app.services.dataset_semantics import store as store_mod
from app.services.gis_harness.workflow_compiler import compile_workflow
from app.services.gis_harness.workflow_v4.compiler_v4 import compile_workflow_v4

_QUERY = "用克里金插值生成污染物浓度表面"
_PROFILE = {"featureCount": 60, "geometryTypes": ["Point"],
            "fields": {"pm25": {"type": "number"}},
            "numericFields": ["pm25"], "crs": "EPSG:4326"}


def _descriptor(value: int = 1, crs: str = "EPSG:4326"):
    p = DatasetProfile(
        source="ref_descriptor", feature_count=60,
        geometry_types=["Point"], crs=crs,
        fields={"pm25": "number"}, numeric_fields=["pm25"],
        fields_status="explicit",
    )
    return derive_descriptor(
        p, dataset_key="ref:t",
        features=[{"properties": {"pm25": i + value}} for i in range(10)])


@pytest.fixture()
def sem_store(tmp_path, monkeypatch):
    store = DatasetSemanticStore(base_dir=tmp_path)
    monkeypatch.setattr(store_mod, "_store", store)
    yield store
    reset_dataset_semantic_store()


def test_baseline_unchanged_without_descriptor():
    """缺省 → 输出与显式 None/"" 完全一致（纯函数纪律）。"""
    base = compile_workflow(_QUERY, profile=_PROFILE)
    explicit = compile_workflow(_QUERY, profile=_PROFILE,
                                descriptor=None,
                                expected_descriptor_fingerprint="")
    assert base.to_bounded_dict() == explicit.to_bounded_dict()


def test_descriptor_consistent_keeps_qualification(sem_store):
    """descriptor 与 profile 同语义（CRS 一致）→ 资格裁决照常，不误伤。"""
    d = _descriptor(crs="EPSG:4326")
    c = compile_workflow(_QUERY, profile=_PROFILE,
                         recipe_id="kriging_interpolation_workflow",
                         descriptor=d,
                         expected_descriptor_fingerprint=d.descriptor_fingerprint)
    c0 = compile_workflow(_QUERY, profile=_PROFILE,
                          recipe_id="kriging_interpolation_workflow")
    states0 = {q["role"]: q["state"] for q in c0.data_qualifications}
    states = {q["role"]: q["state"] for q in c.data_qualifications}
    assert states == states0


def test_stale_fingerprint_degrades_qualification(sem_store):
    """expected ≠ 当前 → DESCRIPTOR_STALE_* 贯穿到 data_qualifications。"""
    d = _descriptor()
    wrong_fp = "dsd-v1:" + "0" * 64
    c = compile_workflow(_QUERY, profile=_PROFILE,
                         recipe_id="kriging_interpolation_workflow",
                         descriptor=d,
                         expected_descriptor_fingerprint=wrong_fp)
    codes = {str(q.get("reason_code") or "") for q in c.data_qualifications}
    assert any(x == "DESCRIPTOR_FINGERPRINT_MISMATCH"
               or x.startswith("DESCRIPTOR_STALE") for x in codes), codes


def test_v4_compiler_threads_descriptor(sem_store):
    d = _descriptor()
    c = compile_workflow_v4(_QUERY, profile=_PROFILE,
                            recipe_id="kriging_interpolation_workflow",
                            descriptor=d,
                            expected_descriptor_fingerprint=d.descriptor_fingerprint)
    assert c.base.data_qualifications is not None
    c_stale = compile_workflow_v4(_QUERY, profile=_PROFILE,
                                  recipe_id="kriging_interpolation_workflow",
                                  descriptor=d,
                                  expected_descriptor_fingerprint="dsd-v1:" + "f" * 64)
    stale_codes = {str(q.get("reason_code") or "")
                   for q in c_stale.base.data_qualifications}
    assert any(x == "DESCRIPTOR_FINGERPRINT_MISMATCH"
               or x.startswith("DESCRIPTOR_STALE") for x in stale_codes), stale_codes


@pytest.mark.asyncio
async def test_primary_session_descriptor_first_hit(sem_store):
    from app.services.session_data import session_data_manager

    sid = f"h08-c6-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    try:
        ref_a = await session_data_manager.store(
            sid, {"type": "FeatureCollection", "features": []}, prefix="a")
        d = _descriptor()
        await sem_store.put(sid, ref_a, d.model_copy(update={"dataset_key": ref_a}))
        await session_data_manager.store(
            sid, {"type": "FeatureCollection", "features": []}, prefix="b")
        got = await primary_session_descriptor(sid)
        assert got is not None
        assert got.descriptor_fingerprint == d.descriptor_fingerprint
    finally:
        await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_primary_session_descriptor_empty_session_none(sem_store):
    from app.services.session_data import session_data_manager

    sid = f"h08-c6-e-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    try:
        assert await primary_session_descriptor(sid) is None
        assert await primary_session_descriptor("") is None
    finally:
        await session_data_manager.clear_session(sid)
