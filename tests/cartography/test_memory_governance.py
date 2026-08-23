"""Spec 开放问题落点：阈值可配置 + 校准工具 + 记忆治理 API。

1. 阈值可运维调参（env → settings → 规则模块常量，import 期一次解析）；
2. ``scripts/calibrate_cartography_thresholds.py`` 从实测证据给建议值，
   low_bad 指标的语义方向必须正确（fail 阈 < warn 阈）；
3. 项目制图记忆治理 API（list / retire / activate），授权与项目域隔离。
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core.database import Base
from app.models.project import Project
from app.main import app

from tests.unit.test_project_api import _auth_headers  # noqa: E402

client = TestClient(app)


def _load_calibration_module():
    spec = importlib.util.spec_from_file_location(
        "calibrate_cartography_thresholds",
        ROOT / "scripts" / "calibrate_cartography_thresholds.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ─── 1. 阈值可配置 ───────────────────────────────────────────────────────

def test_thresholds_come_from_settings_env(monkeypatch):
    """env 覆盖 → settings → 模块常量（import 期解析）。进程内 reload 验证，
    测试结束完整还原，避免污染其它测试的常量。"""
    from app.lib.cartography import semantic_checks as sc
    from app.services.cartography import distribution_drift as dd
    import app.core.config as cfg_mod

    # 污染治理：reload 会铸造新的 settings 单例——全部持旧引用的模块
    # （execution_engine 等）与新 import 的测试将看到两个不同的对象，
    # 后续任何 monkeypatch.setattr(settings, ...) 都打在对方看不见的
    # 那个上（全量套件顺序污染，复现：本文件 + test_chat_engine_planning
    # 两文件共跑）。先捕获原单例，teardown 时回填并二次 reload 依赖模块。
    original_settings = cfg_mod.settings

    for key, value in (
        ("CARTO_LOAD_WARN_RATIO", "0.05"),
        ("CARTO_VISUALVAR_FAIL_COUNT", "6"),
        ("CARTO_DRIFT_RELATIVE_THRESHOLD", "0.5"),
    ):
        monkeypatch.setenv(key, value)
    try:
        importlib.reload(cfg_mod)
        importlib.reload(sc)
        importlib.reload(dd)
        assert sc._LOAD_WARN_RATIO == 0.05
        assert sc._VISUALVAR_FAIL_COUNT == 6
        assert dd.DRIFT_RELATIVE_THRESHOLD == 0.5
    finally:
        for key in (
            "CARTO_LOAD_WARN_RATIO",
            "CARTO_VISUALVAR_FAIL_COUNT",
            "CARTO_DRIFT_RELATIVE_THRESHOLD",
        ):
            monkeypatch.delenv(key, raising=False)
        importlib.reload(cfg_mod)
        cfg_mod.settings = original_settings  # 恢复全进程共享的单例身份
        importlib.reload(sc)
        importlib.reload(dd)
    assert sc._LOAD_WARN_RATIO == 0.15
    assert dd.DRIFT_RELATIVE_THRESHOLD == 0.15


def test_threshold_module_constants_drive_rules(monkeypatch):
    """规则读模块常量（monkeypatch 常量即改判定），不每次读全局配置。"""
    from app.lib.cartography import semantic_checks as sc

    profile = {
        "featureCount": 100, "bbox": [116.0, 39.8, 116.8, 40.0],
        "geometryTypes": ["Point"], "crs": "EPSG:4326", "crs_status": "explicit",
        "fields": {},
    }
    spec = {
        "version": "1.0",
        "view": {"center": [116.4, 39.9], "zoom": 8},
        "layout": {"legend": {"visible": True}},
        "sources": {"s1": {"type": "geojson", "ref": "ref:geojson-x", "profile": profile}},
        "layers": [{"id": "l1", "source": "s1", "type": "circle",
                    "paint": {"circle-radius": 5}}],
    }
    before = [c for c in sc.evaluate_cartography_semantics(spec).to_dict()["checks"]
              if c["rule"] == "carto.load.ratio"][0]["status"]
    monkeypatch.setattr(sc, "_LOAD_WARN_RATIO", 0.0)
    monkeypatch.setattr(sc, "_LOAD_FAIL_RATIO", 0.0)
    after = [c for c in sc.evaluate_cartography_semantics(spec).to_dict()["checks"]
             if c["rule"] == "carto.load.ratio"][0]
    # 默认阈值下此负载应远低于 0.15；清零后任何非零负载都 fail。
    assert before == "pass"
    assert after["status"] == "fail"
    assert after["evidence"]["thresholds"]["warn"] == 0.0


# ─── 2. 校准工具 ─────────────────────────────────────────────────────────

def _write_reviews(path: Path):
    reviews = []
    for i in range(50):
        reviews.append({"checks": [
            {"rule": "carto.load.ratio", "evidence": {"load_ratio": 0.01 + i * 0.02}},
            {"rule": "carto.color.separability",
             "evidence": {"min_adjacent_delta_e": 2.0 + i * 0.8}},
        ]})
    path.write_text(json.dumps({"reviews": reviews}), encoding="utf-8")


def test_calibration_script_reports_and_orders_directions(tmp_path):
    _write_reviews(tmp_path / "run.json")
    calib = _load_calibration_module()
    out = calib.render(
        calib.calibrate(
            calib.collect_observations(
                calib.iter_review_payloads([str(tmp_path / "run.json")])
            )
        )
    )
    assert "load_ratio" in out and "min_adjacent_delta_e" in out
    assert "CARTO_LOAD_WARN_RATIO=" in out
    # 建议行是注释形态：脚本只建议，绝不改配置。
    assert "# CARTO_LOAD_WARN_RATIO=" in out
    # 方向语义：high_bad → warn < fail；low_bad → fail < warn。
    env = {
        line.removeprefix("# ").split("=", 1)[0]:
            line.removeprefix("# ").split("=", 1)[1]
        for line in out.splitlines()
        if line.startswith("# CARTO_") and "=" in line
    }
    assert float(env["CARTO_LOAD_WARN_RATIO"]) < float(env["CARTO_LOAD_FAIL_RATIO"])
    assert float(env["CARTO_COLOR_SEP_FAIL_DELTA_E"]) < float(env["CARTO_COLOR_SEP_WARN_DELTA_E"])


def test_calibration_script_fails_closed_on_no_evidence(tmp_path, capsys):
    (tmp_path / "empty.json").write_text('{"reviews": [{"checks": []}]}', encoding="utf-8")
    calib = _load_calibration_module()
    assert calib.main([str(tmp_path / "empty.json")]) == 1
    assert "没有" in capsys.readouterr().err


# ─── 3. 记忆治理 API ─────────────────────────────────────────────────────

@pytest.fixture()
def project_with_facts():
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Project(id="pm-1", name="Memory Project"))
    from app.services.cartography.project_memory import (
        classification_fingerprint,
        record_fact,
    )
    cls = {"type": "graduated", "field": "pop", "breaks": [0, 10, 100], "class_count": 2}
    active = record_fact(session, "pm-1", "shared_classification", "pop", cls,
                         fingerprint=classification_fingerprint(cls))
    pref = record_fact(session, "pm-1", "preference", "palette", {"value": "dark"})
    stale = record_fact(session, "pm-1", "data_profile", "pop",
                        {"quantiles": [1, 2], "null_ratio": 0.1})
    stale.status = "stale"
    session.commit()
    try:
        yield session, {"cls": active.id, "pref": pref.id, "stale": stale.id}
    finally:
        session.close()


def _patched_route(monkeypatch, db_session):
    """让路由的 get_db 依赖指向测试内存库（dependency_overrides——
    FastAPI 在装饰期绑定依赖函数，事后 patch 模块属性无效）。"""
    from app.core.database import get_db

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        "app.services.project_service.ProjectService.get_project_with_auth",
        classmethod(lambda cls, db, project_id, user_id=None, org_id=None:
                    db.get(Project, project_id)),
    )


@pytest.fixture()
def _clean_overrides():
    yield
    app.dependency_overrides.clear()


def test_list_retire_activate_roundtrip(monkeypatch, project_with_facts, _clean_overrides):
    db, ids = project_with_facts
    _patched_route(monkeypatch, db)

    res = client.get("/api/v1/projects/pm-1/carto-memory", headers=_auth_headers())
    assert res.status_code == 200
    body = res.json()
    assert body["counts"]["active"] == 2
    assert body["counts"]["stale"] == 1
    kinds = {f["kind"] for f in body["facts"]}
    assert kinds == {"shared_classification", "preference", "data_profile"}

    # 撤销偏好（开放问题 2 的入口）。
    res = client.delete(
        f"/api/v1/projects/pm-1/carto-memory/{ids['pref']}", headers=_auth_headers(),
    )
    assert res.status_code == 200
    assert res.json()["fact"]["status"] == "retired"

    # 显式激活 stale 的数据画像（裁决入口）。
    res = client.post(
        f"/api/v1/projects/pm-1/carto-memory/{ids['stale']}/activate",
        headers=_auth_headers(),
    )
    assert res.status_code == 200
    assert res.json()["fact"]["status"] == "active"

    res = client.get("/api/v1/projects/pm-1/carto-memory", headers=_auth_headers())
    counts = res.json()["counts"]
    assert counts["retired"] == 1 and counts["stale"] == 0 and counts["active"] == 2


def test_memory_api_is_project_scoped(monkeypatch, project_with_facts, _clean_overrides):
    db, ids = project_with_facts
    _patched_route(monkeypatch, db)
    db.add(Project(id="pm-other", name="Other"))
    db.commit()

    # 另一项目的 fact_id 在本项目路由下不可见。
    res = client.delete(
        f"/api/v1/projects/pm-other/carto-memory/{ids['pref']}", headers=_auth_headers(),
    )
    assert res.status_code == 404


def test_memory_api_requires_auth(project_with_facts):
    res = client.delete("/api/v1/projects/pm-1/carto-memory/whatever")
    # 匿名请求被认证层拦下（写路径要求登录，与 #501/#528 一致）。
    assert res.status_code in (401, 403)
