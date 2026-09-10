"""Workflow Runtime V5 —— fingerprints / binding 单元测试（Wave 3+4）。"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.workflow_runtime import fingerprints as F
from app.services.workflow_runtime.binding import (
    verify_node_bindings,
    verify_port,
)
from app.services.gis_harness.workflow_v4.typed_dag import TypedPort


# ── 指纹确定性 / canonical 化 ────────────────────────────────────────────

def test_param_canonicalization_int_float_equivalence():
    assert F.canonicalize_parameters({"distance": 100}) == \
        F.canonicalize_parameters({"distance": 100.0})
    assert F.canonicalize_parameters({"b": 1, "a": 2}) == \
        F.canonicalize_parameters({"a": 2, "b": 1})
    assert F.canonicalize_parameters([1, 2]) != F.canonicalize_parameters([2, 1])


def test_reuse_fingerprint_deterministic_and_sensitive():
    ident = {"level": "content", "fp": "a" * 32, "content_revision": "3"}
    kw = dict(package_fingerprint="pkg" * 10, node_id="cap:x",
              algorithm_id="algo", params={"distance": 100}, inputs={"input": ident},
              env_fp="e" * 32)
    fp1 = F.node_reuse_fingerprint(**kw)
    fp2 = F.node_reuse_fingerprint(**kw)
    assert fp1 == fp2
    # 任一分量变化 → 指纹变化
    assert fp1 != F.node_reuse_fingerprint(**{**kw, "params": {"distance": 200}})
    assert fp1 != F.node_reuse_fingerprint(**{**kw, "algorithm_id": "algo2"})
    assert fp1 != F.node_reuse_fingerprint(**{
        **kw, "inputs": {"input": {**ident, "content_revision": "4"}}})
    assert fp1 != F.node_reuse_fingerprint(**{
        **kw, "inputs": {"input": {**ident, "level": "shape"}}})
    assert fp1 != F.node_reuse_fingerprint(**{**kw, "env_fp": "f" * 32})


def test_input_identity_levels():
    # content 级最高
    d = {"content_hash": "z" * 32, "content_revision": 7,
         "profile_digest": {"rows": 5}, "feature_count": 10}
    ident = F.input_content_identity(d)
    assert ident["level"] == "content" and ident["content_revision"] == "7"
    # profile_digest 次之
    d2 = {"profile_digest": {"rows": 5}, "content_revision": 7}
    assert F.input_content_identity(d2)["level"] == "profile_digest"
    # shape 兜底（含 ref 身份）
    ident3 = F.input_content_identity({"ref_id": "ref:geojson-1",
                                       "feature_count": 10})
    assert ident3["level"] == "shape"
    assert ident3["fp"] == F.input_content_identity(
        {"ref_id": "ref:geojson-1", "feature_count": 10})["fp"]
    # 缺 descriptor → 空身份
    assert F.input_content_identity(None)["level"] == ""


def test_env_fp_component_sensitivity():
    base = dict(compiler_version="4.0.0", execution_plan_version=2,
                methodology_fingerprint="m")
    fp = F.environment_fingerprint(**base)
    assert fp == F.environment_fingerprint(**base)
    assert fp != F.environment_fingerprint(**{**base, "compiler_version": "5.0.0"})
    assert fp != F.environment_fingerprint(**{**base, "methodology_fingerprint": "m2"})
    # geo 栈分量在位（任一真实环境至少 numpy 存在）
    assert F.geo_stack_digest()


# ── typed port 运行时校验 ────────────────────────────────────────────────

def _desc(**kw):
    base = {"artifact_type": "feature_collection", "crs": "EPSG:4326",
            "feature_count": 10, "geometry_types": ["Point"]}
    base.update(kw)
    return base


def test_missing_required_input_blocked():
    port = TypedPort(name="input", artifact_type="feature_collection",
                     required=True)
    v = verify_port(port, None, port_name="input")
    assert [x.code for x in v] == ["MISSING_INPUT"]
    # optional 缺席不违规
    port_opt = TypedPort(name="extra", required=False)
    assert verify_port(port_opt, None) == []


def test_artifact_type_mismatch_and_wide_port():
    port = TypedPort(name="input", artifact_type="density_surface")
    v = verify_port(port, _desc(), port_name="input")
    assert any(x.code == "ARTIFACT_TYPE_MISMATCH" for x in v)
    # 宽端口接受 feature_set 类（注册表真实成员）
    wide = TypedPort(name="input", artifact_type="feature_collection")
    assert verify_port(wide, _desc(artifact_type="point_feature_set")) == []
    # 非 feature_set 类（surface）不被宽端口接受
    assert any(x.code == "ARTIFACT_TYPE_MISMATCH"
               for x in verify_port(wide, _desc(artifact_type="density_surface")))
    # 未注册类型 → 双向 UNKNOWN
    assert any(x.code == "ARTIFACT_TYPE_UNKNOWN"
               for x in verify_port(port, _desc(artifact_type="bogus_type")))


def test_crs_class_mismatch_blocked():
    """PROJECTED_REQUIRED 端口 × geographic 数据 → CRS_CLASS_MISMATCH。"""
    port = TypedPort(name="input", artifact_type="feature_collection",
                     crs_requirement="PROJECTED_REQUIRED")
    v = verify_port(port, _desc(crs="EPSG:4326"), port_name="input")
    assert any(x.code == "CRS_CLASS_MISMATCH" for x in v)
    # UTM 投影放行
    assert verify_port(port, _desc(crs="EPSG:32650")) == []
    # 无 CRS 事实 = unknown = 诚实放行
    assert verify_port(port, _desc(crs=None)) == []


def test_geometry_and_empty_and_unit():
    port = TypedPort(name="input", artifact_type="feature_collection",
                     geometry_kind="polygon", unit_requirement="m")
    v = verify_port(port, _desc(), port_name="input")
    codes = {x.code for x in v}
    assert "GEOMETRY_MISMATCH" in codes       # point ≠ polygon
    assert "UNIT_UNVERIFIED" in codes
    empty = verify_port(port, _desc(feature_count=0), port_name="input")
    assert any(x.code == "EMPTY_OUTPUT" for x in empty)


def test_node_level_verdict():
    n = SimpleNamespace(
        node_id="cap:x",
        inputs=[TypedPort(name="input", artifact_type="feature_collection")],
        outputs=[],
    )
    verdict = verify_node_bindings(
        n, {"input": _desc()},
    )
    assert verdict.ok and verdict.action == "pass"
    bad = verify_node_bindings(n, {"input": None})
    assert not bad.ok and bad.action == "blocked"
    assert bad.violations[0].code == "MISSING_INPUT"
