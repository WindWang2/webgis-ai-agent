"""AtlasJobSpec 契约测试：版本字面量/语义校验/幂等参数投影。"""
import pytest

from app.lib.cartography.atlas_job_spec import (
    ATLAS_JOB_SPEC_VERSION,
    AtlasJobSpec,
    ClassificationScope,
)
from app.lib.cartography.mapspec_schema import MapSpecSchemaError
from app.lib.cartography.publication_ir import AtlasPolicy

pytestmark = pytest.mark.cartography


def _spec(**overrides):
    base = {
        "spec_version": ATLAS_JOB_SPEC_VERSION,
        "session_id": "sess-1",
        "atlas": AtlasPolicy(driver="category", category_property="zone"),
        "classification_scope": ClassificationScope.shared,
        "target_dpi": 300,
        "title": "t",
    }
    base.update(overrides)
    return AtlasJobSpec(**base)


def test_version_literal_and_defaults():
    assert ATLAS_JOB_SPEC_VERSION == "atlas_job.v1"
    spec = _spec()
    assert spec.classification_scope is ClassificationScope.shared
    assert spec.simulated_data is False
    spec.validate_semantics()  # 合法基线不抛


def test_local_scope_requires_frames_driver():
    spec = _spec(classification_scope=ClassificationScope.local)
    with pytest.raises(MapSpecSchemaError) as ei:
        spec.validate_semantics()
    assert ei.value.code == "atlas_local_requires_frames"


def test_local_scope_with_frames_driver_ok():
    spec = _spec(
        classification_scope=ClassificationScope.local,
        atlas=AtlasPolicy(driver="frames"),
    )
    spec.validate_semantics()


def test_blank_session_rejected():
    spec = _spec(session_id="   ")
    with pytest.raises(MapSpecSchemaError) as ei:
        spec.validate_semantics()
    assert ei.value.code == "atlas_session_missing"


def test_frozen_and_extra_forbid():
    spec = _spec()
    with pytest.raises(Exception):
        spec.session_id = "x"  # frozen
    with pytest.raises(Exception):
        AtlasJobSpec(spec_version="atlas_job.v1", session_id="s",
                     atlas={"driver": "category", "category_property": "z"},
                     rogue_field=1)


def test_job_params_projection_is_idempotency_stable():
    a = _spec().job_params()
    b = _spec().job_params()
    assert a == b
    assert a["spec_version"] == "atlas_job.v1"
    assert a["atlas"]["driver"] == "category"
    assert "simulated_data" in a
    # 语义字段变化 → 幂等键输入变化
    assert _spec(target_dpi=150).job_params() != a
