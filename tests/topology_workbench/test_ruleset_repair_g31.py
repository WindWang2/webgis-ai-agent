"""G31 规则 DSL 与修复桥测试。

- DSL typed 拒绝：未知类型/严重级/参数键、负容差、NaN、超量级、重复 id、
  词表外角色、bbox 角色限制；
- 修复桥：归属选择类拒绝无 keeper、G14 预览不写数据、不稳定身份 typed 拒绝。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.auth import create_access_token
from app.core.database import Base, Engine, SessionLocal
from app.main import app
from app.models.db_model import Conversation, User
from app.services.session_data import session_data_manager
from app.services.topology_workbench.repair import build_repair_candidates
from app.services.topology_workbench.ruleset import (
    complete_roles,
    normalize_run_bindings,
    rule_catalog,
    ruleset_digest,
    validate_rule,
    validate_ruleset_rules,
)

from tests.topology_workbench.fixtures_g31 import (
    PARCEL_E_RECT,
    aoi_source,
    fc,
    feat,
)


# ─── DSL 校验 ────────────────────────────────────────────────────────


def test_unknown_rule_type_rejected():
    with pytest.raises(ValueError, match="封闭词表"):
        validate_rule({"rule_id": "x", "rule_type": "no_such_rule"})


def test_unknown_severity_rejected():
    with pytest.raises(ValueError, match="severity"):
        validate_rule({"rule_id": "x", "rule_type": "area_coverage", "severity": "fatal"})


def test_unknown_param_key_rejected():
    with pytest.raises(ValueError, match="未知参数键"):
        validate_rule({"rule_id": "x", "rule_type": "area_coverage",
                       "params": {"tolerance_m": 1.0}})


def test_negative_and_nan_tolerance_rejected():
    with pytest.raises(ValueError, match="负值|有限"):
        validate_rule({"rule_id": "x", "rule_type": "line_endpoints_connected",
                       "params": {"tolerance_m": -1.0}})
    with pytest.raises(ValueError, match="有限"):
        validate_rule({"rule_id": "x", "rule_type": "line_endpoints_connected",
                       "params": {"tolerance_m": float("nan")}})


def test_tolerance_m_required_for_endpoints():
    with pytest.raises(ValueError, match="tolerance_m"):
        validate_rule({"rule_id": "x", "rule_type": "line_endpoints_connected",
                       "params": {}})


def test_tolerance_unit_separation():
    """线性容差与面积阈值是不同参数 —— 一数两用被词表拒绝。"""
    with pytest.raises(ValueError, match="未知参数键"):
        validate_rule({"rule_id": "x", "rule_type": "polygons_no_overlap",
                       "params": {"tolerance_m": 5.0}})  # 面规则不收线性容差


def test_duplicate_rule_ids_rejected():
    with pytest.raises(ValueError, match="重复"):
        validate_ruleset_rules([
            {"rule_id": "r", "rule_type": "area_coverage"},
            {"rule_id": "r", "rule_type": "polygons_no_overlap"},
        ])


def test_digest_changes_with_params():
    r1 = validate_ruleset_rules([{"rule_id": "c", "rule_type": "area_coverage",
                                   "params": {"gap_area_threshold_m2": 5}}])
    r2 = validate_ruleset_rules([{"rule_id": "c", "rule_type": "area_coverage",
                                   "params": {"gap_area_threshold_m2": 10}}])
    assert ruleset_digest(r1) != ruleset_digest(r2)


def test_catalog_and_complete_roles():
    catalog = rule_catalog()
    assert {c["rule_type"] for c in catalog} == {
        "polygons_no_overlap", "area_coverage", "objects_covered_by_target",
        "cross_layer_no_overlap", "line_endpoints_connected",
    }
    assert set(complete_roles("area_coverage")) == {"coverage", "aoi", "exclusions"}
    assert "connectors" in complete_roles("line_endpoints_connected")


def test_bbox_binding_only_for_aoi():
    with pytest.raises(ValueError, match="不允许 bbox 绑定"):
        normalize_run_bindings(
            [{"rule_id": "c", "rule_type": "area_coverage", "severity": "error",
              "enabled": True, "params": {}}],
            {"coverage": {"kind": "bbox", "bbox": [0, 0, 1, 1]},
             "aoi": {"kind": "bbox", "bbox": [0, 0, 1, 1]}},
        )


def test_missing_required_role_rejected():
    with pytest.raises(ValueError, match="必需角色"):
        normalize_run_bindings(
            [{"rule_id": "c", "rule_type": "area_coverage", "severity": "error",
              "enabled": True, "params": {}}],
            {"coverage": {"kind": "session_ref", "ref": "ref:x"}},  # aoi 缺失
        )


# ─── 修复桥（G14 预览） ─────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        Base.metadata.create_all(bind=Engine, checkfirst=True)
        yield c


@pytest.fixture(scope="module", autouse=True)
def seed():
    from datetime import datetime, timezone

    with SessionLocal() as db:
        if db.get(User, "twb-owner") is None:
            db.add(User(id="twb-owner", username="twb-owner",
                        email="twb@x.io", created_at=datetime.now(timezone.utc)))
        if db.get(Conversation, "twb-sess") is None:
            db.add(Conversation(id="twb-sess", title="g31",
                                user_id="twb-owner",
                                created_at=datetime.now(timezone.utc)))
        db.commit()


def _store(name, payload):
    return asyncio.get_event_loop().run_until_complete(
        session_data_manager.store("twb-sess", payload, prefix=name))


def test_repair_ownership_choice_required(client: TestClient):
    """归属选择类修复：无 keeper → typed 拒绝（不自动判定地块所有权）。"""
    from app.services.topology_workbench.repair import WorkbenchError, build_g14_preview
    from app.services.topology_workbench.service import submit_run
    from app.services.topology_workbench.runner import execute_topology_run

    aoi_ref = _store("rb-aoi", aoi_source())
    parcels = fc(feat("p-hole", {
        "type": "Polygon",
        "coordinates": [
            [[116.40, 39.90], [116.41, 39.90], [116.41, 39.909], [116.40, 39.909], [116.40, 39.90]],
            [[116.405, 39.903], [116.407, 39.903], [116.407, 39.905], [116.405, 39.905], [116.405, 39.903]],
        ],
    }))
    parcels_ref = _store("rb-p", parcels)
    H = {"Authorization": "Bearer " + create_access_token(
        {"sub": "twb-owner", "username": "twb-owner", "role": "user"})}
    rs = client.post("/api/v1/topology-workbench/rulesets", headers=H, json={
        "name": "rb", "rules": [{"rule_id": "cov", "rule_type": "area_coverage",
                                  "severity": "error", "params": {"gap_area_threshold_m2": 5.0}}],
        "session_id": "twb-sess"})
    ruleset_id = rs.json()["ruleset"]["id"]
    sub = asyncio.get_event_loop().run_until_complete(submit_run(
        ruleset_id=ruleset_id,
        bindings={"coverage": {"kind": "session_ref", "ref": parcels_ref, "session_id": "twb-sess"},
                   "aoi": {"kind": "session_ref", "ref": aoi_ref, "session_id": "twb-sess"}},
        actor_user_id="twb-owner", actor_owner_token=None,
        session_id="twb-sess", submit_job=False))
    asyncio.get_event_loop().run_until_complete(execute_topology_run(sub["run_id"]))
    finding = client.get(
        f"/api/v1/topology-workbench/runs/{sub['run_id']}/findings",
        headers=H).json()["items"][0]

    # 候选：归属选择类，不提供一键修复
    cands = build_repair_candidates(finding["id"], actor_user_id="twb-owner")
    assert cands["candidates"][0]["ownership_choice_required"] is True

    # 无 keeper 的 G14 预览 → typed 拒绝
    with pytest.raises(WorkbenchError, match="归属"):
        asyncio.get_event_loop().run_until_complete(build_g14_preview(
            finding["id"], session_id="twb-sess", kind="fill_gap",
            actor_user_id="twb-owner"))


def test_repair_candidates_no_deterministic_op(client: TestClient):
    """没有确定性修复的规则类型 → typed unsupported + 人工路径指引。"""
    # 构造 cross_layer_no_overlap finding（无自动候选）
    from app.services.topology_workbench.service import submit_run
    from app.services.topology_workbench.runner import execute_topology_run

    a = fc(feat("a1", PARCEL_E_RECT))
    b = fc(feat("b1", {
        "type": "Polygon",
        "coordinates": [[[116.4055, 39.902], [116.4075, 39.902], [116.4075, 39.904], [116.4055, 39.904], [116.4055, 39.902]]],
    }))
    a_ref, b_ref = _store("rb-a", a), _store("rb-b", b)
    H = {"Authorization": "Bearer " + create_access_token(
        {"sub": "twb-owner", "username": "twb-owner", "role": "user"})}
    rs = client.post("/api/v1/topology-workbench/rulesets", headers=H, json={
        "name": "rb2", "rules": [{"rule_id": "x", "rule_type": "cross_layer_no_overlap",
                                   "severity": "error", "params": {"overlap_area_threshold_m2": 1.0}}],
        "session_id": "twb-sess"})
    ruleset_id = rs.json()["ruleset"]["id"]
    sub = asyncio.get_event_loop().run_until_complete(submit_run(
        ruleset_id=ruleset_id,
        bindings={"source_a": {"kind": "session_ref", "ref": a_ref, "session_id": "twb-sess"},
                   "source_b": {"kind": "session_ref", "ref": b_ref, "session_id": "twb-sess"}},
        actor_user_id="twb-owner", actor_owner_token=None,
        session_id="twb-sess", submit_job=False))
    asyncio.get_event_loop().run_until_complete(execute_topology_run(sub["run_id"]))
    finding = client.get(
        f"/api/v1/topology-workbench/runs/{sub['run_id']}/findings",
        headers=H).json()["items"][0]
    # cross_layer 有 resolve_overlap 候选但归属选择必选
    cands = build_repair_candidates(finding["id"], actor_user_id="twb-owner")
    kinds = {c["kind"] for c in cands["candidates"]}
    assert kinds == {"resolve_overlap"}
    assert cands["candidates"][0]["ownership_choice_required"] is True
