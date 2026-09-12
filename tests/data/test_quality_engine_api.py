"""V9 P1 —— 质量评估引擎/autofix/REST 面测试（含 durable job 双路径）。

覆盖（任务书 §5）：
- evaluate 同步路径 + QualityReport 落库（persist=True → 列表/详情可查）；
- durable job 大数据集路径：eager 模式下 submit → 任务内联执行 → completed
  报告 + result_ref 回链；重复提交幂等复用；
- run_quality_evaluate 恢复语义：同 (session, ref, ruleset) 复用 completed
  报告；载荷不可读 → failed 报告行（诚实失败，绝不虚构 pass）；
- autofix：dry-run 零改动；apply 产出新载荷不覆写输入（new-ref 语义）；
- session 所有权守卫：外来 session 404。
"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, Engine
from app.main import app
from app.services.data_quality.autofix import (
    AutofixStep,
    apply_autofix,
    dry_run_autofix,
    plan_autofix,
)
from app.services.data_quality.engine import (
    evaluate_payload,
    run_quality_evaluate,
)
from app.services.session_data import session_data_manager

client = TestClient(app)

_BAD_FC = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature",
         "geometry": {"type": "Polygon",
                      "coordinates": [[[0, 0], [0, 2], [2, 2], [2, 0]]]},
         "properties": {"id": "a"}},
        {"type": "Feature",
         "geometry": {"type": "Polygon",
                      "coordinates": [[[0, 0], [0, 2], [2, 2], [2, 0]]]},
         "properties": {"id": "a"}},
    ],
}


def _auth_headers():
    from app.core.auth import create_access_token
    return {"Authorization": "Bearer " + create_access_token(
        {"sub": "dq-owner", "username": "dq-owner", "role": "viewer"})}


@pytest.fixture(autouse=True)
def setup_db():
    # 自足建表（test_lakehouse_api_v7.py 同款模式）：quality_reports /
    # quality_rule_results 定义在 app.models.data_quality，产线代码只在
    # 函数体内惰性 import —— 本模块顶层 import 链（app.main、
    # services.data_quality.engine）不会注册它们，create_all 的 metadata
    # 里没有这两张表就建不出 quality_reports；全量套件靠其他文件先跑时
    # 顺带注册/建表（跨文件共享建表状态的顺序污染），单跑/换序即
    # 「no such table: quality_reports」。显式 import 注册 + checkfirst=True
    # 幂等补建：单独运行与任意前序组合都不缺表。
    import app.models.data_quality  # noqa: F401
    import app.models.db_model  # noqa: F401

    Base.metadata.create_all(bind=Engine, checkfirst=True)
    from app.core.database import SessionLocal
    from app.models.db_model import User
    with SessionLocal() as db:
        db.merge(User(id="dq-owner", username="dq-owner",
                      email="dq-owner@example.com", password_hash="x",
                      role="viewer", is_active=True))
        db.commit()
    yield


# ── 引擎内核 ─────────────────────────────────────────────────────────


def test_evaluate_payload_shape_and_verdicts():
    report = evaluate_payload({"geojson": _BAD_FC})
    assert report["target_kind"] == "vector"
    assert report["overall_status"] in ("warn", "fail")
    # 16 类规则全部有判定行（不适用/未配置的规则行是 skipped，也是证据）
    assert len(report["results"]) >= 15
    assert report["rule_count"] >= 10  # vector 载荷上已判定的规则数
    by_id = {r["rule_id"]: r for r in report["results"]}
    assert by_id["dq.primary_key_uniqueness"]["status"] == "fail"
    assert by_id["dq.geometry_validity"]["status"] == "fail"
    assert by_id["dq.crs_validity"]["status"] == "fail"
    # 栅格规则在 vector 载荷上诚实 skipped
    assert by_id["dq.nodata_ratio"]["status"] == "skipped"


def test_evaluate_rejects_empty_payload():
    with pytest.raises(ValueError):
        evaluate_payload({"nope": 1})


def test_evaluate_custom_ruleset():
    rules = [{"rule_id": "pk", "rule_type": "primary_key_uniqueness",
              "params": {"field": "id"}}]
    report = evaluate_payload({"geojson": _BAD_FC}, rules)
    assert report["rule_count"] == 1
    assert report["results"][0]["rule_id"] == "pk"


# ── 落库 + REST 只读面 ───────────────────────────────────────────────


def test_evaluate_persist_and_detail_roundtrip():
    res = client.post("/api/v1/data-quality/evaluate", json={
        "geojson": _BAD_FC, "persist": True, "project_id": "dq-proj",
        "target_ref": "inline:test",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["success"] and body["report_id"]
    report_id = body["report_id"]

    listed = client.get("/api/v1/data-quality/reports",
                        params={"project_id": "dq-proj"})
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1
    assert any(i["id"] == report_id for i in listed.json()["items"])

    detail = client.get(f"/api/v1/data-quality/reports/{report_id}")
    assert detail.status_code == 200
    d = detail.json()
    assert d["report"]["overall_status"] in ("warn", "fail")
    assert len(d["results"]) >= d["report"]["rule_count"]
    assert any(r["autofixable"] for r in d["results"])

    assert client.get("/api/v1/data-quality/reports/no-such-id").status_code == 404


def test_rules_catalog_endpoint():
    res = client.get("/api/v1/data-quality/rules")
    assert res.status_code == 200
    rules = res.json()["rules"]
    assert len(rules) >= 15
    assert {"rule_id", "rule_type", "severity"} <= set(rules[0].keys())


def test_evaluate_rejects_bad_ruleset_with_400():
    res = client.post("/api/v1/data-quality/evaluate", json={
        "geojson": _BAD_FC,
        "rules": [{"rule_id": "x", "rule_type": "unknown_type"}],
    })
    assert res.status_code == 400


# ── durable job 路径 ─────────────────────────────────────────────────


def test_submit_report_runs_eager_and_persists():
    session_id = "dq-eager-session"
    import asyncio

    ref_id = asyncio.run(session_data_manager.store(session_id, _BAD_FC,
                                                    prefix="dq"))
    res = client.post("/api/v1/data-quality/reports", json={
        "session_id": session_id, "ref": ref_id,
    }, headers=_auth_headers())
    assert res.status_code == 200
    body = res.json()
    assert body["success"]
    assert body["status"] in ("analysis_task_started", "analysis_task_reused")

    listed = client.get("/api/v1/data-quality/reports",
                        params={"session_id": session_id})
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1
    top = listed.json()["items"][0]
    detail = client.get(f"/api/v1/data-quality/reports/{top['id']}")
    assert detail.status_code == 200
    assert detail.json()["report"]["status"] == "completed"


def test_run_quality_evaluate_reuses_completed_report():
    session_id = "dq-reuse-session"
    import asyncio

    ref_id = asyncio.run(session_data_manager.store(session_id, _BAD_FC,
                                                    prefix="dq"))
    first = run_quality_evaluate(0, session_id=session_id, ref=ref_id)
    assert first["status"] == "completed" and not first["reused"]
    second = run_quality_evaluate(0, session_id=session_id, ref=ref_id)
    assert second["status"] == "completed" and second["reused"]
    assert second["report_id"] == first["report_id"]


def test_run_quality_evaluate_missing_ref_fails_honestly():
    out = run_quality_evaluate(0, session_id="dq-missing", ref="ref:dq-none")
    assert out["status"] == "failed"
    from app.core.database import SessionLocal
    from app.models.data_quality import QualityReport

    with SessionLocal() as db:
        row = db.get(QualityReport, out["report_id"])
        assert row is not None and row.status == "failed"
        assert any("payload_unreadable" in (d or "") for d in (row.diagnostics or []))


def test_submit_rejects_bad_ruleset_before_enqueue():
    res = client.post("/api/v1/data-quality/reports", json={
        "session_id": "s", "ref": "ref:x",
        "rules": [{"rule_id": "x", "rule_type": "bogus"}],
    }, headers=_auth_headers())
    assert res.status_code == 400


def test_submit_requires_auth():
    res = client.post("/api/v1/data-quality/reports", json={
        "session_id": "s", "ref": "ref:x"})
    assert res.status_code in (401, 403)


# ── autofix ──────────────────────────────────────────────────────────


def test_autofix_dry_run_does_not_mutate():
    import copy

    snapshot = copy.deepcopy(_BAD_FC)
    report = evaluate_payload({"geojson": _BAD_FC})
    steps = plan_autofix(report)
    assert steps, "坏数据必须产出 autofix 建议"
    preview = dry_run_autofix({"geojson": _BAD_FC}, steps)
    assert preview["applicable"]
    assert _BAD_FC == snapshot, "dry-run 绝不改动输入"
    assert any("repair_geometry" in preview["would_change"] for _ in [0])


def test_autofix_apply_returns_new_payload_and_closes_rings():
    payload = {"geojson": copy.deepcopy(_BAD_FC), "crs": ""}
    new_payload, evidence = apply_autofix(payload, plan_autofix(evaluate_payload(payload)))
    assert evidence["changed"]
    # new-ref 语义：输入不被覆写
    assert _BAD_FC["features"][0]["geometry"]["coordinates"][0][0] == [0, 0]
    # 修复后：未闭合环闭合 + CRS 附着
    new_fc = new_payload["geojson"]
    ring = new_fc["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1], "apply 后环必须闭合"
    assert new_payload.get("crs"), "apply 后 CRS 必须附着"
    # 复评：geometry_validity / crs_validity 不再 fail
    after = evaluate_payload({"geojson": new_fc, "crs": new_payload.get("crs")})
    by_id = {r["rule_id"]: r for r in after["results"]}
    assert by_id["dq.geometry_validity"]["status"] != "fail"
    assert by_id["dq.crs_validity"]["status"] == "pass"


def test_autofix_apply_with_foreign_session_404():
    res = client.post("/api/v1/data-quality/autofix/apply", json={
        "geojson": _BAD_FC, "session_id": "no-such-session",
    }, headers=_auth_headers())
    assert res.status_code == 404


def test_autofix_apply_registers_new_ref_for_owned_session():
    import asyncio

    # 造一个真实会话行（verify_session_owner 需要 Conversation 行）
    from app.core.database import SessionLocal
    from app.models.db_model import Conversation

    with SessionLocal() as db:
        existing = db.get(Conversation, "dq-owned-session")
        if existing is None:
            db.add(Conversation(id="dq-owned-session", title="dq",
                                user_id="dq-owner"))
        else:
            existing.user_id = "dq-owner"
        db.commit()
    res = client.post("/api/v1/data-quality/autofix/apply", json={
        "geojson": _BAD_FC, "session_id": "dq-owned-session",
    }, headers=_auth_headers())
    assert res.status_code == 200
    body = res.json()
    assert body["success"] and body["new_ref"]
    stored = asyncio.run(
        session_data_manager.get("dq-owned-session", body["new_ref"]))
    assert isinstance(stored, dict) and stored.get("type") == "FeatureCollection"


def test_autofix_apply_normalize_redecodes_mojibake():
    bad = "ä¸­å›½åœ°å›¾"
    payload = {"geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]},
         "properties": {"name": bad}}]}}
    steps = [AutofixStep(operation="normalize")]
    new_payload, evidence = apply_autofix(payload, steps)
    fixed = new_payload["geojson"]["features"][0]["properties"]["name"]
    assert fixed == "中国地图", f"乱码应重码为原文，得到 {fixed!r}"
    assert evidence["operations"][0]["redecoded_strings"] == 1
    # 输入不被覆写
    assert payload["geojson"]["features"][0]["properties"]["name"] == bad


def test_autofix_apply_reproject_with_pyproj():
    payload = {"geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [116.0, 39.5]},
         "properties": {}}]}, "crs": "EPSG:4326"}
    steps = [AutofixStep(operation="reproject", params={"target_crs": "EPSG:3857"})]
    new_payload, evidence = apply_autofix(payload, steps)
    entry = evidence["operations"][0]
    assert entry["reprojected"] is True, "pyproj 在场时必须真实重投影"
    x, y = new_payload["geojson"]["features"][0]["geometry"]["coordinates"]
    # Web Mercator 合法域检查（精确值由 pyproj 权威，避免测试硬编码漂移）：
    # 116E → x ∈ 1.2e7~1.3e7；39.5N → y ∈ 4.7e6~4.9e6
    assert 1.2e7 < x < 1.3e7 and 4.7e6 < y < 4.9e6
    # 反变换回到经纬度（±1e-6°）
    from pyproj import Transformer

    lon, lat = Transformer.from_crs("EPSG:3857", "EPSG:4326",
                                    always_xy=True).transform(x, y)
    assert abs(lon - 116.0) < 1e-6 and abs(lat - 39.5) < 1e-6


def test_autofix_apply_filter_null_drops_rows():
    payload = {"geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {"a": 1}},
        {"type": "Feature", "geometry": None, "properties": {"a": None}}]}}
    steps = [AutofixStep(operation="filter_null", params={"field": "a"})]
    new_payload, evidence = apply_autofix(payload, steps)
    feats = new_payload["geojson"]["features"]
    assert len(feats) == 1 and feats[0]["properties"]["a"] == 1
    assert evidence["operations"][0]["dropped_features"] == 1
