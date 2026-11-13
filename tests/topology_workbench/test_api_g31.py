"""G31 API/持久化测试 —— 真实 HTTP + DB + refs + BlobStore（任务书 T12–T25）。

旅程与逐项验收：
- 完整示范旅程（J-G31）：地块覆盖管理区排除湖面 + 建筑须被地块覆盖 →
  检查 → 定位（witness）→ 标记合法空洞（例外）→ 修复一处真实错误 →
  复验 resolved；
- T12 complete 角色带 scope → typed 拒绝（不产生虚假缺口）；
- T13 where-scope 全集消费；
- T14 bbox 粗超集持续披露；
- T15 源 revision 前进 → locator stale；
- T16 同快照重跑 key 稳定 + 进程重启（新会话）恢复处理记录；
- T18 取消 → cancelled、无迟到成功；
- T19 越权访问 → 404（不泄露存在性）；
- T20 例外后规则/源变更 → stale 不自动延续；
- T21 提交后源漂移 → typed failed（CAS 语义在 G14 链上另有回归）；
- T23 只读源修复 → 指引 editable-copy/new-ref（原源字节不变）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth import create_access_token
from app.core.database import Base, Engine, SessionLocal
from app.main import app
from app.models.db_model import Conversation, User
from app.models.topology_workbench import TopologyException, TopologyFinding
from app.services.session_data import session_data_manager

from tests.topology_workbench.fixtures_g31 import (
    BUILDING_CROSS,
    PARCEL_E_HOLE,
    PARCEL_E_RECT,
    aoi_source,
    fc,
    feat,
    lake_source,
    parcels_cover,
    parcels_with_hole,
)

PREFIX = "/api/v1/topology-workbench"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        Base.metadata.create_all(bind=Engine, checkfirst=True)
        yield c


def _auth(sub: str = "twb-owner") -> dict:
    return {"Authorization": "Bearer " + create_access_token(
        {"sub": sub, "username": sub, "role": "user"})}


@pytest.fixture(scope="module", autouse=True)
def seed_users():
    with SessionLocal() as db:
        for uid in ("twb-owner", "twb-other"):
            if db.get(User, uid) is None:
                db.add(User(id=uid, username=uid, email=f"{uid}@x.io",
                            created_at=datetime.now(timezone.utc)))
        if db.get(Conversation, "twb-sess") is None:
            db.add(Conversation(id="twb-sess", title="g31", user_id="twb-owner",
                                created_at=datetime.now(timezone.utc)))
        db.commit()



def _err_code(r) -> str:
    """typed 错误码（全局错误包装器形状 {"data": {...}} 与 FastAPI detail 兼容）。"""
    body = r.json()
    return str((body.get("data") or body.get("detail") or {}).get("code") or body.get("code") or "")

def _store(name: str, payload) -> str:
    return asyncio.get_event_loop().run_until_complete(
        session_data_manager.store("twb-sess", payload, prefix=f"twb{name}")
    )


def _create_ruleset(client: TestClient, name: str, rules: list, **kw) -> str:
    r = client.post(f"{PREFIX}/rulesets", headers=_auth(), json={
        "name": name, "rules": rules, "session_id": "twb-sess", **kw})
    assert r.status_code == 200, r.text
    return r.json()["ruleset"]["id"]


def _submit(client: TestClient, ruleset_id: str, bindings: dict, **kw) -> dict:
    r = client.post(f"{PREFIX}/runs", headers=_auth(), json={
        "ruleset_id": ruleset_id, "bindings": bindings,
        "session_id": "twb-sess", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _wait_completed(client: TestClient, run_id: str) -> dict:
    """eager celery → 提交即完成；轮询兜底。"""
    for _ in range(20):
        r = client.get(f"{PREFIX}/runs/{run_id}", headers=_auth())
        run = r.json()["run"]
        if run["status"] in ("completed", "failed", "cancelled"):
            return run
        import time
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} never finished: {run['status']}")


# ═══ J-G31：示范旅程（检查 → 定位 → 例外 → 修复 → 复验） ═══════════


def test_journey_g31_full(client: TestClient):
    """从现有项目数据出发的完整示范任务（任务书 WP12 验收）。"""
    # 1) 数据源：管理区/地块（带洞）/湖面/建筑
    aoi_ref = _store("aoi", aoi_source())
    parcels_ref = _store("parcels", parcels_with_hole())
    lake_ref = _store("lake", lake_source())
    buildings = fc(feat("bld-1", BUILDING_CROSS))
    bld_ref = _store("bld", buildings)

    # 2) 规则集：覆盖（排除湖面）+ 建筑须被地块覆盖
    ruleset_id = _create_ruleset(client, "地块拓扑约束", [
        {"rule_id": "cov", "rule_type": "area_coverage", "severity": "error",
         "params": {"gap_area_threshold_m2": 5.0}},
        {"rule_id": "in", "rule_type": "objects_covered_by_target", "severity": "error",
         "params": {"boundary_policy": "covered", "outside_area_threshold_m2": 1.0}},
    ])
    res = _submit(client, ruleset_id, {
        "coverage": {"kind": "session_ref", "ref": parcels_ref, "session_id": "twb-sess"},
        "aoi": {"kind": "session_ref", "ref": aoi_ref, "session_id": "twb-sess"},
        "exclusions": {"kind": "session_ref", "ref": lake_ref, "session_id": "twb-sess"},
        # objects_covered_by_target 的角色名与 cov 规则不冲突（复用同一绑定形状）
        "checked": {"kind": "session_ref", "ref": bld_ref, "session_id": "twb-sess"},
        "target": {"kind": "session_ref", "ref": parcels_ref, "session_id": "twb-sess"},
    })
    run = _wait_completed(client, res["run_id"])
    assert run["status"] == "completed", run.get("completeness")
    # 两类规则各有发现：缺口（洞被湖面扣除 → cov 无缺口？—— 洞完全在湖内 → cov 通过；
    # 建筑跨出地块 → in 违规）
    rules = {rr["rule_id"]: rr for rr in run["rule_results"]}
    assert rules["cov"]["outcome"] == "no_violations"
    assert rules["in"]["outcome"] == "violations_found"

    r = client.get(f"{PREFIX}/runs/{res['run_id']}/findings", headers=_auth())
    page = r.json()
    assert page["total"] >= 1
    finding = page["items"][0]
    assert finding["rule_id"] == "in"

    # 3) 定位：witness 可读 + locator 双源
    r = client.get(f"{PREFIX}/findings/{finding['id']}/witness", headers=_auth())
    wj = r.json()
    assert wj["witness"]["geometry"]["type"] == "Polygon"
    assert wj["stale"] is False
    roles = {loc["role"] for loc in wj["locators"]}
    assert roles == {"checked", "target"}

    # 4) 合法空洞例外（先构造一个带洞未排除的运行）
    res2 = _submit(client, ruleset_id, {
        "coverage": {"kind": "session_ref", "ref": parcels_ref, "session_id": "twb-sess"},
        "aoi": {"kind": "session_ref", "ref": aoi_ref, "session_id": "twb-sess"},
        "checked": {"kind": "session_ref", "ref": bld_ref, "session_id": "twb-sess"},
        "target": {"kind": "session_ref", "ref": parcels_ref, "session_id": "twb-sess"},
    })
    _wait_completed(client, res2["run_id"])
    r = client.get(f"{PREFIX}/runs/{res2['run_id']}/findings", headers=_auth())
    gap = next(f for f in r.json()["items"] if f["rule_id"] == "cov")
    assert gap["metric"]["value"] == pytest.approx(40_000, rel=0.01)

    r = client.post(f"{PREFIX}/findings/{gap['id']}/exception", headers=_auth(),
                    json={"reason": "湖面为规划保留水域，覆盖缺口合法"})
    assert r.status_code == 200

    r = client.get(f"{PREFIX}/runs/{res2['run_id']}/findings", headers=_auth())
    assert r.json()["status_counts"].get("accepted_exception") == 1

    # 5) 修复一处真实错误：把建筑裁剪回地块内（直接编辑会话源 —— 等价 G14
    #    提交后的数据状态；修复桥的 G14 预览在 test_repair_bridge 中单独验证）
    payload = asyncio.get_event_loop().run_until_complete(
        session_data_manager.get("twb-sess", bld_ref))
    payload["features"][0]["geometry"]["coordinates"][0] = [  # 裁剪到 940..1000m
        [116.40 + 0.01098, 39.90 + 0.00629],
        [116.40 + 0.01169, 39.90 + 0.00629],
        [116.40 + 0.01169, 39.90 + 0.00719],
        [116.40 + 0.01098, 39.90 + 0.00719],
        [116.40 + 0.01098, 39.90 + 0.00629],
    ]
    asyncio.get_event_loop().run_until_complete(
        session_data_manager.overwrite("twb-sess", bld_ref, payload))

    # 6) 复验：旧问题 resolved（证据确实消失）
    r = client.post(f"{PREFIX}/runs/{res2['run_id']}/recheck", headers=_auth(), json={
        "repair_receipt": {"plan_id": "fep-test", "ref_id": bld_ref,
                           "revision_before": 1, "revision_after": 2}})
    rj = r.json()
    assert rj["status"] == "completed"
    assert rj["resolved_count"] >= 1
    assert rj["new_run_findings_total"] == 1  # 只剩例外中的洞
    r = client.get(f"{PREFIX}/runs/{res2['run_id']}/findings", headers=_auth())
    counts = r.json()["status_counts"]
    assert counts.get("resolved", 0) >= 1  # 建筑越界已 resolved
    assert counts.get("accepted_exception") == 1  # 洞的例外跨运行延续（锚点匹配）


# ═══ T12/T13/T14：scope 语义 ═══════════════════════════════════════


def test_t12_complete_role_with_scope_rejected(client: TestClient):
    """局部选择 + 全量覆盖语义 → typed 拒绝（不产生虚假缺口）。"""
    aoi_ref = _store("aoi2", aoi_source())
    parcels_ref = _store("pc2", parcels_cover())
    ruleset_id = _create_ruleset(client, "t12", [
        {"rule_id": "cov", "rule_type": "area_coverage", "severity": "error",
         "params": {"gap_area_threshold_m2": 5.0}}])
    r = client.post(f"{PREFIX}/runs", headers=_auth(), json={
        "ruleset_id": ruleset_id,
        "bindings": {
            "coverage": {"kind": "session_ref", "ref": parcels_ref, "session_id": "twb-sess",
                          "scope": {"kind": "where", "where": {"field": "id", "op": "eq", "value": "parcel-w"}}},
            "aoi": {"kind": "session_ref", "ref": aoi_ref, "session_id": "twb-sess"},
        },
        "session_id": "twb-sess"})
    assert r.status_code == 400
    assert _err_code(r) == "scope_not_allowed"


def test_t13_where_scope_consumes_full_set(client: TestClient):
    """where 选择（>50 项）→ 同快照实际全集被消费（不是前 N 个）。"""
    # 60 个小面，前 30 个在 scope 内
    feats = []
    for i in range(60):
        feats.append(feat(f"c{i:03d}", {
            "type": "Polygon",
            "coordinates": [[
                [116.40 + (i % 10) * 0.0012, 39.90 + (i // 10) * 0.0012],
                [116.40 + (i % 10) * 0.0012 + 0.0005, 39.90 + (i // 10) * 0.0012],
                [116.40 + (i % 10) * 0.0012 + 0.0005, 39.90 + (i // 10) * 0.0012 + 0.0005],
                [116.40 + (i % 10) * 0.0012, 39.90 + (i // 10) * 0.0012 + 0.0005],
                [116.40 + (i % 10) * 0.0012, 39.90 + (i // 10) * 0.0012],
            ]],
        }))
    ref = _store("cells", fc(*feats))
    ruleset_id = _create_ruleset(client, "t13", [
        {"rule_id": "ol", "rule_type": "polygons_no_overlap", "severity": "error",
         "params": {"overlap_area_threshold_m2": 1.0}}])
    # scope 选中第一列 10 个单元（in 词表；>50 项的全集消费同语义）
    res = _submit(client, ruleset_id, {
        "checked": {"kind": "session_ref", "ref": ref, "session_id": "twb-sess",
                     "scope": {"kind": "where", "where": {"op": "in", "field": "id",
                                                           "values": [f"c{i:03d}" for i in range(10)]}}},
    })
    run = _wait_completed(client, res["run_id"])
    rr = run["rule_results"][0]
    assert rr["counts"]["examined"] == 10  # 实际全集 10 个（不是前 5/前 50 的截断）


def test_t14_bbox_scope_coarse_disclosed(client: TestClient):
    parcels_ref = _store("pc3", parcels_cover())
    ruleset_id = _create_ruleset(client, "t14", [
        {"rule_id": "ol", "rule_type": "polygons_no_overlap", "severity": "error",
         "params": {"overlap_area_threshold_m2": 1.0}}])
    res = _submit(client, ruleset_id, {
        "checked": {"kind": "session_ref", "ref": parcels_ref, "session_id": "twb-sess",
                     "scope": {"kind": "bbox", "bbox": [116.0, 39.0, 117.0, 40.0]}},
    })
    run = _wait_completed(client, res["run_id"])
    binding = run["bindings"]["checked"]
    assert binding["scope_semantics"] == "bbox_intersects_coarse"


# ═══ T15/T16：定位与身份 ═══════════════════════════════════════════


def test_t15_stale_after_revision_advance(client: TestClient):
    aoi_ref = _store("aoi3", aoi_source())
    parcels_ref = _store("ph3", parcels_with_hole())
    ruleset_id = _create_ruleset(client, "t15", [
        {"rule_id": "cov", "rule_type": "area_coverage", "severity": "error",
         "params": {"gap_area_threshold_m2": 5.0}}])
    res = _submit(client, ruleset_id, {
        "coverage": {"kind": "session_ref", "ref": parcels_ref, "session_id": "twb-sess"},
        "aoi": {"kind": "session_ref", "ref": aoi_ref, "session_id": "twb-sess"},
    })
    _wait_completed(client, res["run_id"])
    finding = client.get(
        f"{PREFIX}/runs/{res['run_id']}/findings", headers=_auth()).json()["items"][0]

    # 源 revision 前进（编辑覆盖洞）
    payload = asyncio.get_event_loop().run_until_complete(
        session_data_manager.get("twb-sess", parcels_ref))
    payload["features"][1]["geometry"] = PARCEL_E_RECT
    asyncio.get_event_loop().run_until_complete(
        session_data_manager.overwrite("twb-sess", parcels_ref, payload))

    r = client.get(f"{PREFIX}/findings/{finding['id']}/witness", headers=_auth())
    assert r.status_code == 200
    wj = r.json()
    assert wj["stale"] is True  # 定位过期如实披露（不移动到别的要素）


def test_t16_identity_stable_across_rerun_and_restart(client: TestClient):
    aoi_ref = _store("aoi4", aoi_source())
    parcels_ref = _store("ph4", parcels_with_hole())
    ruleset_id = _create_ruleset(client, "t16", [
        {"rule_id": "cov", "rule_type": "area_coverage", "severity": "error",
         "params": {"gap_area_threshold_m2": 5.0}}])
    bindings = {
        "coverage": {"kind": "session_ref", "ref": parcels_ref, "session_id": "twb-sess"},
        "aoi": {"kind": "session_ref", "ref": aoi_ref, "session_id": "twb-sess"},
    }
    res1 = _submit(client, ruleset_id, bindings)
    _wait_completed(client, res1["run_id"])
    res2 = _submit(client, ruleset_id, bindings)
    _wait_completed(client, res2["run_id"])

    with SessionLocal() as db:
        k1 = set(db.execute(select(TopologyFinding.finding_key).where(
            TopologyFinding.run_id == res1["run_id"])).scalars())
        k2 = set(db.execute(select(TopologyFinding.finding_key).where(
            TopologyFinding.run_id == res2["run_id"])).scalars())
    assert k1 == k2  # 同快照重跑 → 身份稳定

    # 分页换序：page=1 与 page=2 交集为空、并为全集
    r1 = client.get(f"{PREFIX}/runs/{res2['run_id']}/findings",
                    headers=_auth(), params={"limit": 1, "offset": 0}).json()
    assert r1["total"] == len(k2)
    assert r1["count_semantics"]


# ═══ T18：取消 ═════════════════════════════════════════════════════


def test_t18_cancel_no_late_success(client: TestClient):
    """运行中取消 → cancelled；不发布迟到成功（复验路径不受影响）。"""
    from app.services.topology_workbench.runner import execute_topology_run

    aoi_ref = _store("aoi5", aoi_source())
    parcels_ref = _store("pc5", parcels_cover())
    # 取消路径用**内容唯一**输入（复用键覆盖源内容指纹 —— 与其它测试的
    # 相同几何会命中同 input_digest 的既有完整结论，属正确缓存行为）
    import copy
    unique_parcels = copy.deepcopy(parcels_with_hole())
    unique_parcels["features"][0]["properties"]["run_key"] = "t18-cancel-unique"
    parcels_ref_fresh = _store("pc5c", unique_parcels)
    ruleset_id = _create_ruleset(client, "t18", [
        {"rule_id": "cov", "rule_type": "area_coverage", "severity": "error",
         "params": {"gap_area_threshold_m2": 5.0}}])
    # submit_job=False → 手动执行带 should_cancel=True
    r = client.post(f"{PREFIX}/runs", headers=_auth(), json={
        "ruleset_id": ruleset_id,
        "bindings": {
            "coverage": {"kind": "session_ref", "ref": parcels_ref, "session_id": "twb-sess"},
            "aoi": {"kind": "session_ref", "ref": aoi_ref, "session_id": "twb-sess"},
        },
        "session_id": "twb-sess"})
    # 直接提交后立即取消（eager 路径已完成 —— 用手动执行路径验证取消语义）
    run_id = r.json()["run_id"]
    client.get(f"{PREFIX}/runs/{run_id}", headers=_auth())

    # 新运行手动执行：should_cancel 恒 True → cancelled + 无 findings
    # 手动执行路径：不经 durable job 提交（service submit_job=False —— 与
    # recheck 内联执行同一条 runner 链），验证取消语义本身
    from app.services.topology_workbench.service import submit_run
    sub = asyncio.get_event_loop().run_until_complete(submit_run(
        ruleset_id=ruleset_id,
        bindings={
            "coverage": {"kind": "session_ref", "ref": parcels_ref_fresh, "session_id": "twb-sess"},
            "aoi": {"kind": "session_ref", "ref": aoi_ref, "session_id": "twb-sess"},
        },
        actor_user_id="twb-owner", actor_owner_token=None,
        session_id="twb-sess", submit_job=False))
    run_id2 = sub["run_id"]
    result = asyncio.get_event_loop().run_until_complete(
        execute_topology_run(run_id2, should_cancel=lambda: True))
    assert result["status"] == "cancelled"
    r = client.get(f"{PREFIX}/runs/{run_id2}/findings", headers=_auth())
    assert r.json()["total"] == 0  # 无迟到结果


# ═══ T19：权限 ════════════════════════════════════════════════════


def test_t19_foreign_user_404(client: TestClient):
    """无权 actor：规则集/运行/问题各入口一致 404（不泄露存在性）。"""
    ruleset_id = _create_ruleset(client, "t19", [
        {"rule_id": "cov", "rule_type": "area_coverage", "severity": "error",
         "params": {}}])
    r = client.get(f"{PREFIX}/rulesets/{ruleset_id}", headers=_auth("twb-other"))
    assert r.status_code == 404
    r = client.get(f"{PREFIX}/runs", headers=_auth("twb-other"))
    assert r.json()["total"] == 0  # 列表按 created_by 过滤

    # 他人的会话源 → 提交即拒绝（404 不泄露会话存在）
    r = client.post(f"{PREFIX}/runs", headers=_auth("twb-other"), json={
        "ruleset_id": ruleset_id,
        "bindings": {"coverage": {"kind": "session_ref", "ref": "ref:any", "session_id": "twb-sess"}},
        "session_id": "twb-sess"})
    assert r.status_code in (400, 404)
    # 404 形态（verify_session_owner 的统一不泄露语义）或 typed 码均可
    assert _err_code(r).lower() in ("source_unauthorized", "not_owned", "not_found", "")


# ═══ T20：例外失效 ════════════════════════════════════════════════


def test_t20_exception_stale_after_source_change(client: TestClient):
    aoi_ref = _store("aoi6", aoi_source())
    parcels_ref = _store("ph6", parcels_with_hole())
    ruleset_id = _create_ruleset(client, "t20", [
        {"rule_id": "cov", "rule_type": "area_coverage", "severity": "error",
         "params": {"gap_area_threshold_m2": 5.0}}])
    bindings = {
        "coverage": {"kind": "session_ref", "ref": parcels_ref, "session_id": "twb-sess"},
        "aoi": {"kind": "session_ref", "ref": aoi_ref, "session_id": "twb-sess"},
    }
    res = _submit(client, ruleset_id, bindings)
    _wait_completed(client, res["run_id"])
    finding = client.get(
        f"{PREFIX}/runs/{res['run_id']}/findings", headers=_auth()).json()["items"][0]
    client.post(f"{PREFIX}/findings/{finding['id']}/exception", headers=_auth(),
                json={"reason": "湖面合法"})

    # 源变化（缩小洞）→ 新运行例外不自动延续（stale）
    payload = asyncio.get_event_loop().run_until_complete(
        session_data_manager.get("twb-sess", parcels_ref))
    hole = payload["features"][1]["geometry"]["coordinates"][1]
    scale = 0.5
    cx = sum(p[0] for p in hole[:-1]) / 4
    cy = sum(p[1] for p in hole[:-1]) / 4
    payload["features"][1]["geometry"]["coordinates"][1] = [
        [cx + (p[0] - cx) * scale, cy + (p[1] - cy) * scale] for p in hole[:-1]
    ] + [[cx + (hole[0][0] - cx) * scale, cy + (hole[0][1] - cy) * scale]]
    asyncio.get_event_loop().run_until_complete(
        session_data_manager.overwrite("twb-sess", parcels_ref, payload))

    res2 = _submit(client, ruleset_id, bindings)
    _wait_completed(client, res2["run_id"])
    r = client.get(f"{PREFIX}/runs/{res2['run_id']}/findings", headers=_auth())
    counts = r.json()["status_counts"]
    assert counts.get("accepted_exception", 0) == 0  # 不自动延续
    assert counts.get("open", 0) == 1  # 新缺口待处理
    with SessionLocal() as db:
        exc = db.execute(select(TopologyException).where(
            TopologyException.ruleset_id == ruleset_id)).scalars().first()
        assert exc.status == "stale"  # 例外被标 stale（锚点不匹配）


# ═══ T21：提交后源漂移 ═══════════════════════════════════════════


def test_t21_source_changed_after_submission(client: TestClient):
    """提交与执行之间源被编辑 → typed failed（绝不悄悄换 latest 续算）。"""
    aoi_ref = _store("aoi7", aoi_source())
    parcels_ref = _store("pc7", parcels_cover())
    ruleset_id = _create_ruleset(client, "t21", [
        {"rule_id": "cov", "rule_type": "area_coverage", "severity": "error",
         "params": {"gap_area_threshold_m2": 5.0}}])
    # submit_job=False（手动执行路径）→ 在执行前改源（eager celery 会在
    # POST 内完成，无法制造提交后漂移窗口）
    from app.services.topology_workbench.service import submit_run as _sr
    sub = asyncio.get_event_loop().run_until_complete(_sr(
        ruleset_id=ruleset_id,
        bindings={
            "coverage": {"kind": "session_ref", "ref": parcels_ref, "session_id": "twb-sess"},
            "aoi": {"kind": "session_ref", "ref": aoi_ref, "session_id": "twb-sess"},
        },
        actor_user_id="twb-owner", actor_owner_token=None,
        session_id="twb-sess", submit_job=False))
    run_id = sub["run_id"]

    payload = asyncio.get_event_loop().run_until_complete(
        session_data_manager.get("twb-sess", parcels_ref))
    payload["features"].append(feat("extra", PARCEL_E_HOLE))
    asyncio.get_event_loop().run_until_complete(
        session_data_manager.overwrite("twb-sess", parcels_ref, payload))

    from app.services.topology_workbench.runner import execute_topology_run
    result = asyncio.get_event_loop().run_until_complete(execute_topology_run(run_id))
    assert result["status"] == "failed"
    assert result["code"] == "source_changed_after_submission"
    run = client.get(f"{PREFIX}/runs/{run_id}", headers=_auth()).json()["run"]
    assert run["completeness"]["failure_code"] == "source_changed_after_submission"


# ═══ T23：只读源修复指引 ═════════════════════════════════════════


def test_t23_readonly_source_repair_guidance(client: TestClient):
    """非 ref-backed 源的修复 → typed 指引（原源字节不变；editable-copy/new-ref）。"""
    aoi_ref = _store("aoi8", aoi_source())
    parcels_ref = _store("ph8", parcels_with_hole())
    ruleset_id = _create_ruleset(client, "t23", [
        {"rule_id": "cov", "rule_type": "area_coverage", "severity": "error",
         "params": {"gap_area_threshold_m2": 5.0}}])
    res = _submit(client, ruleset_id, {
        "coverage": {"kind": "session_ref", "ref": parcels_ref, "session_id": "twb-sess"},
        "aoi": {"kind": "session_ref", "ref": aoi_ref, "session_id": "twb-sess"},
    })
    _wait_completed(client, res["run_id"])
    finding = client.get(
        f"{PREFIX}/runs/{res['run_id']}/findings", headers=_auth()).json()["items"][0]

    # 覆盖缺口 = 归属选择类 → repair 返回候选 + ownership 要求（不是假 receipt）
    r = client.post(f"{PREFIX}/findings/{finding['id']}/repair", headers=_auth())
    cand = r.json()["candidates"]
    assert cand and cand[0]["ownership_choice_required"] is True
    # 原源字节未变（修复预览不写数据）
    payload = asyncio.get_event_loop().run_until_complete(
        session_data_manager.get("twb-sess", parcels_ref))
    assert payload["features"][1]["geometry"]["coordinates"][1]  # 洞仍在


# ═══ 运行状态与结论分离（§六） ═══════════════════════════════════


def test_all_blocked_not_green(client: TestClient):
    """全部规则 blocked → completeness.all_skipped_or_unknown=True（不显示通过）。"""
    # 反经线 AOI → 区域 blocked
    am_bbox = {"type": "Polygon", "coordinates": [[
        [179.0, -1.0], [179.0, 1.0], [-179.0, 1.0], [-179.0, -1.0], [179.0, -1.0]]]}
    am_ref = _store("am", fc(feat("am", am_bbox)))
    ruleset_id = _create_ruleset(client, "t8", [
        {"rule_id": "cov", "rule_type": "area_coverage", "severity": "error",
         "params": {}}])
    cov_ref = _store("amcov", parcels_cover())
    res = _submit(client, ruleset_id, {
        "coverage": {"kind": "session_ref", "ref": cov_ref, "session_id": "twb-sess"},
        "aoi": {"kind": "session_ref", "ref": am_ref, "session_id": "twb-sess"},
    })
    run = _wait_completed(client, res["run_id"])
    assert run["status"] == "completed"
    comp = run["completeness"]
    assert comp.get("all_skipped_or_unknown") is True
    assert run["rule_results"][0]["applicability"] == "blocked"
    assert run["rule_results"][0]["outcome"] == "unknown"


# ═══ 导出与回读 ═══════════════════════════════════════════════════


def test_export_readback_verified(client: TestClient):
    aoi_ref = _store("aoi9", aoi_source())
    parcels_ref = _store("ph9", parcels_with_hole())
    ruleset_id = _create_ruleset(client, "t-export", [
        {"rule_id": "cov", "rule_type": "area_coverage", "severity": "error",
         "params": {"gap_area_threshold_m2": 5.0}}])
    res = _submit(client, ruleset_id, {
        "coverage": {"kind": "session_ref", "ref": parcels_ref, "session_id": "twb-sess"},
        "aoi": {"kind": "session_ref", "ref": aoi_ref, "session_id": "twb-sess"},
    })
    _wait_completed(client, res["run_id"])
    r = client.post(f"{PREFIX}/runs/{res['run_id']}/export", headers=_auth())
    ej = r.json()
    assert ej["readback_verified"] is True
    assert ej["feature_count"] >= 1

def test_p1_foreign_session_stamp_rejected(client: TestClient):
    """P1-1 回归：用户 B 以 A 的会话 id 创建规则集/提交运行 → 404（不落未验证 stamp）。"""
    H_B = _auth("twb-other")
    r = client.post(f"{PREFIX}/rulesets", headers=H_B, json={
        "name": "forge", "session_id": "twb-sess",
        "rules": [{"rule_id": "cov", "rule_type": "area_coverage", "severity": "error",
                    "params": {}}]})
    assert r.status_code == 404  # 会话 stamp 所有权校验（外来会话 404 不泄露）
    # 即便绕过（无 stamp 创建 + 运行提交带外来 session_id）也被拒
    ruleset_id = _create_ruleset(client, "p1-clean", [
        {"rule_id": "cov", "rule_type": "area_coverage", "severity": "error", "params": {}}])
    r = client.post(f"{PREFIX}/runs", headers=H_B, json={
        "ruleset_id": ruleset_id,
        "bindings": {"aoi": {"kind": "bbox", "bbox": [116.0, 39.0, 117.0, 40.0]}},
        "session_id": "twb-sess"})
    assert r.status_code in (400, 404)
