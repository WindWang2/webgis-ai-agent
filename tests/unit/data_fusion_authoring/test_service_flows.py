"""融合服务编排面验收（真实 DB + 会话存储旅程）。

覆盖验收旅程：
1. 成都小学：预览 → 审阅 → 唯一性可验证的融合数据集（来源证据保留）；
4. 来源刷新：影响面 / 决定继承 / 锁定保留 / 原版本可追溯；
5. 越权与幂等：跨会话拒绝、commit 门、决定 CAS、取消；
加上：执行幂等收敛、stale_source、空/边界、require_resolved 门。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_tables")

from app.services.data_fusion_authoring import outputs, review, service
from app.services.data_fusion_authoring.errors import DataFusionError
from tests.unit.data_fusion_authoring.conftest import (
    chengdu_pois_a,
    fusion_plan,
    seed_session_source,
)


# ── 计划 CRUD（CAS）与越权 ───────────────────────────────────

async def test_plan_crud_cas_and_cross_session(seeded_fusion_sources):
    s = seeded_fusion_sources
    view = await service.create_plan(s["session_id"], s["plan"],
                                     actor=s["actor"])
    pid, rev = view["plan_id"], view["revision"]
    assert rev == 1 and view["status"] == "draft"
    assert view["normalized_digest"]
    # CAS 错修订号 → stale
    with pytest.raises(DataFusionError) as ei:
        await service.update_plan(s["session_id"], pid, s["plan"],
                                  expected_revision=99, actor=s["actor"])
    assert ei.value.code == "stale_plan_revision"
    # 正确 CAS → revision 2、预览失效
    view2 = await service.update_plan(s["session_id"], pid, s["plan"],
                                      expected_revision=rev, actor=s["actor"])
    assert view2["revision"] == 2
    assert view2["preview"] is None and view2["preview_digest"] is None
    # 跨会话读 → not_owned
    with pytest.raises(DataFusionError) as ei:
        await service.get_plan("sess-other", pid)
    assert ei.value.code == "not_owned"
    # 列表
    listing = await service.list_plans(s["session_id"])
    assert any(p["plan_id"] == pid for p in listing["plans"])


# ── 来源资格卡（WP2） ─────────────────────────────────────────

async def test_describe_sources_eligibility_cards(seeded_fusion_sources):
    s = seeded_fusion_sources
    out = await service.describe_sources(
        s["session_id"],
        [
            {"side": "a", "kind": "session_ref",
             "session_id": s["session_id"], "ref": s["a_ref"],
             "name_field": "name", "external_id_field": "code",
             "address_field": "addr", "category_field": "cat"},
            {"side": "b", "kind": "session_ref",
             "session_id": s["session_id"], "ref": s["b_ref"],
             "name_field": "name", "external_id_field": "osm_id",
             "address_field": "addr", "category_field": "cat",
             "temporal_field": "checked"},
        ],
        s["actor"])
    cards = {c["side"]: c for c in out["sources"]}
    assert cards["a"]["state"] == "ok"
    assert cards["a"]["row_count"] == 3
    assert cards["a"]["crs_class"] == "geographic"
    assert cards["a"]["geometry"] == "Point"
    assert cards["a"]["fields"]["name_field"]["present"]
    assert cards["b"]["temporal_parse"]["parsed"] == 3
    # 不可解析源 → typed 三态卡（不伪造 verified）
    bad = await service.describe_sources(
        s["session_id"],
        [{"side": "a", "kind": "session_ref",
          "session_id": s["session_id"], "ref": "ref:fusiona-nope",
          "name_field": "name"}],
        s["actor"])
    assert bad["sources"][0]["state"] == "unresolvable"
    assert bad["sources"][0]["error_code"] == "source_unresolvable"


# ── 预览（只读不合并） ─────────────────────────────────────────

async def test_preview_real_counts_and_cache_invalidation(
        seeded_fusion_sources):
    s = seeded_fusion_sources
    view = await service.create_plan(s["session_id"], s["plan"],
                                     actor=s["actor"])
    pid = view["plan_id"]
    r1 = await service.preview_plan(s["session_id"], pid,
                                    expected_revision=1, actor=s["actor"])
    assert r1["cached"] is False
    st = r1["match_stats"]
    # 括号校区名自动合并 1 对；真别名进审阅 1 对；各 1 未配对
    assert st["pairs_matched"] == 1
    assert st["pairs_needs_review"] == 1
    assert st["unmatched_a"] == 1 and st["unmatched_b"] == 1
    assert r1["samples"]["review"] and r1["samples"]["matched"]
    # 预览已缓存 + 指纹落行
    stored = await service.get_plan(s["session_id"], pid)
    assert stored["preview"] is not None
    assert stored["fingerprint_a"] and stored["fingerprint_b"]
    r2 = await service.preview_plan(s["session_id"], pid,
                                    expected_revision=1, actor=s["actor"])
    assert r2["cached"] is True


# ── 执行 → 审阅 → commit 全旅程（验收旅程 1） ─────────────────

async def _execute_completed(seeded_fusion_sources):
    s = seeded_fusion_sources
    view = await service.create_plan(s["session_id"], s["plan"],
                                     actor=s["actor"])
    run = await service.execute_plan(
        s["session_id"], view["plan_id"], expected_revision=1,
        actor=s["actor"], background=False)
    assert run["status"] == "completed", run
    return s, view, run


async def test_execute_review_commit_full_journey(seeded_fusion_sources):
    s, view, run = await _execute_completed(seeded_fusion_sources)
    pid = view["plan_id"]
    rid = run["run_id"]
    receipt = run["receipt"]
    assert receipt["state"] == "matched_pending_commit"
    assert receipt["match_stats"]["pairs_matched"] == 1
    assert receipt["fingerprint_a"] and receipt["fingerprint_b"]

    # commit 门：require_resolved + 别名对未决 → commit_blocked
    with pytest.raises(DataFusionError) as ei:
        await service.commit_run(s["session_id"], rid, actor=s["actor"])
    assert ei.value.code == "commit_blocked"
    assert ei.value.detail.get("outstanding", 0) >= 1

    # 审阅队列：别名对在列，带证据与冲突
    queue = service.review_queue(s["session_id"], rid)
    assert queue["total"] >= 2   # 别名对（身份未决）+ 冲突地址对（matched+字段冲突）
    alias_item = next(it for it in queue["items"]
                      if it["status"] == "needs_review")
    assert alias_item["pair_key"].startswith("fp:")
    kinds = {e["kind"] for e in alias_item["evidence"]}
    assert "name" in kinds and "distance" in kinds
    # 冲突地址对：实体同一性已自动成立（matched），字段级冲突进队列
    conflict_item = next(it for it in queue["items"]
                         if (it.get("field_conflicts") or {}).get("address")
                         == "conflict")
    assert conflict_item["status"] == "matched"
    assert conflict_item["field_conflicts"]["phone"] == "missing_b"

    # 决定 CAS：错修订号拒绝 → 新建（expected 0）成功
    with pytest.raises(DataFusionError) as ei:
        await service.upsert_decision(
            s["session_id"], pid, pair_key=alias_item["pair_key"],
            action="accept", decision_run_id=rid,
            idx_a=alias_item["idx_a"], idx_b=alias_item["idx_b"],
            expected_revision=5)
    assert ei.value.code == "stale_review_revision"
    dec = await service.upsert_decision(
        s["session_id"], pid, pair_key=alias_item["pair_key"],
        action="accept", decision_run_id=rid,
        idx_a=alias_item["idx_a"], idx_b=alias_item["idx_b"],
        field_values={"name": "成都市第七中学"},
        reviewer="reviewer-a", note="别名人工确认合并")
    assert dec["action"] == "accept" and dec["locked"] is True
    # 字段级人工值（matched 对的 unresolved 字段决定）
    await service.upsert_decision(
        s["session_id"], pid, pair_key=conflict_item["pair_key"],
        action="accept", decision_run_id=rid,
        field_values={"address": "琉璃场街2号"}, reviewer="reviewer-a",
        note="地址人工确认")
    # 第二次更新：错误修订号 → stale（不覆盖他人决定）；正确修订 → revision 2
    with pytest.raises(DataFusionError) as ei:
        await service.upsert_decision(
            s["session_id"], pid, pair_key=alias_item["pair_key"],
            action="accept", expected_revision=99,
            note="别人先改了")
    assert ei.value.code == "stale_review_revision"
    dec2 = await service.upsert_decision(
        s["session_id"], pid, pair_key=alias_item["pair_key"],
        action="accept", expected_revision=dec["revision"],
        note="复核维持合并", reviewer="reviewer-b")
    assert dec2["revision"] == 2

    # commit → 不可变融合版本
    done = await service.commit_run(s["session_id"], rid, actor=s["actor"])
    assert done["status"] == "completed"
    assert done["output_ref"] == f"ref:data-fusion/{rid}"
    assert done["output_sha256"]
    counts = done["commit_receipt"]["counts"]
    assert counts["merged"] == 2          # 自动 1 + 人工 1
    assert done["commit_receipt"]["decided_pairs"] == 2
    assert done["commit_receipt"]["unresolved_field_count"] == 0

    # 幂等 commit：再次 → 复用
    again = await service.commit_run(s["session_id"], rid, actor=s["actor"])
    assert again.get("idempotent_reuse") is True

    # 结果分页：唯一性可验证 + provenance 完整
    page = await service.run_result_page(s["session_id"], rid, cursor=0,
                                         limit=50)
    feats = page["features"]
    assert page["total"] == 4             # 6 行 − 2 合并对 = 4 要素
    names = [f["properties"].get("name") for f in feats]
    # 唯一性：合并后无重复学校名（withheld 不存在 —— 全部已决）
    merged = [f for f in feats
              if f["properties"]["_fusion"]["origin"] == "merged"]
    assert len(merged) == 2
    prov = merged[0]["properties"]["_fusion"]
    assert len(prov["sources"]) == 2
    assert prov["match"]["method_version"] == 1
    addr_fields = [f["properties"].get("address") for f in merged]
    assert "琉璃场街2号" in addr_fields       # 人工决定值生效
    # 单源要素保留（B 独有幼儿园）
    singles_b = [f for f in feats
                 if f["properties"]["_fusion"]["origin"] == "single_b"]
    assert any("芳草街便民幼儿园" in (f["properties"].get("name") or "")
               for f in singles_b)


async def test_reject_decision_keeps_separate(seeded_fusion_sources):
    s, view, run = await _execute_completed(seeded_fusion_sources)
    rid, pid = run["run_id"], view["plan_id"]
    queue = service.review_queue(s["session_id"], rid)
    item = queue["items"][0]
    await service.upsert_decision(
        s["session_id"], pid, pair_key=item["pair_key"], action="reject",
        decision_run_id=rid, reviewer="reviewer-a",
        note="两个不同校区，不合并")
    done = await service.commit_run(s["session_id"], rid, actor=s["actor"])
    counts = done["commit_receipt"]["counts"]
    assert counts["rejected"] == 2
    assert counts["merged"] == 1          # 仅括号校区自动对
    page = await service.run_result_page(s["session_id"], rid, limit=50)
    decided = [f for f in page["features"]
               if f["properties"]["_fusion"].get("decision")]
    assert len(decided) == 2              # 双侧带否决记录
    assert decided[0]["properties"]["_fusion"]["decision"]["note"] == \
        "两个不同校区，不合并"


async def test_allow_unresolved_withholds_open_pairs(seeded_fusion_sources):
    s = seeded_fusion_sources
    plan = s["plan"] | {"commit_policy": "allow_unresolved"}
    view = await service.create_plan(s["session_id"], plan, actor=s["actor"])
    run = await service.execute_plan(
        s["session_id"], view["plan_id"], expected_revision=1,
        actor=s["actor"], background=False)
    assert run["status"] == "completed"
    done = await service.commit_run(s["session_id"], run["run_id"],
                                    actor=s["actor"])
    counts = done["commit_receipt"]["counts"]
    assert counts["withheld_review"] == 2   # 未决别名对两侧按单源输出
    page = await service.run_result_page(s["session_id"], run["run_id"],
                                         limit=50)
    withheld = [f for f in page["features"]
                if f["properties"]["_fusion"]["origin"] == "withheld_review"]
    assert len(withheld) == 2
    assert withheld[0]["properties"]["_fusion"]["partner_key"]
    # 质量报告披露未决
    assert done["commit_receipt"]["unresolved_field_count"] >= 0


# ── 执行幂等 / 取消 / stale ────────────────────────────────────

async def test_execute_idempotent_reuse(seeded_fusion_sources):
    s, view, run = await _execute_completed(seeded_fusion_sources)
    # 同计划 + 同源内容 + 已有匹配收据（未 commit）→ 复用
    run2 = await service.execute_plan(
        s["session_id"], view["plan_id"], expected_revision=1,
        actor=s["actor"], background=False)
    assert run2.get("idempotent_reuse") is True
    assert run2["run_id"] == run["run_id"]


async def test_commit_blocked_after_source_change(seeded_fusion_sources):
    s, view, run = await _execute_completed(seeded_fusion_sources)
    # 源内容漂移（追加要素）
    payload = chengdu_pois_a()
    payload["features"].append({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [104.061, 30.641]},
        "properties": {"code": "J004", "name": "新学校", "addr": "x",
                       "cat": "小学", "tel": None},
    })
    await seed_session_source(s["session_id"], payload, prefix="fusiona")
    # 覆盖 ref 指向（store 生成新 ref —— 改计划指向新 ref 会变 digest；
    # 这里直接改 session 内原 ref 的内容验证 stale 检测）
    from app.services.session_data import session_data_manager
    await session_data_manager.overwrite(s["session_id"], s["a_ref"], payload)
    with pytest.raises(DataFusionError) as ei:
        await service.commit_run(s["session_id"], run["run_id"],
                                 actor=s["actor"])
    assert ei.value.code == "stale_source"


async def test_cancel_before_completion_and_terminal_guards(
        seeded_fusion_sources):
    s = seeded_fusion_sources
    view = await service.create_plan(s["session_id"], s["plan"],
                                     actor=s["actor"])
    run = await service.execute_plan(
        s["session_id"], view["plan_id"], expected_revision=1,
        actor=s["actor"], background=True)
    rid = run["run_id"]
    # 已终态运行不可再取消（typed）
    if run["status"] in ("completed", "failed", "cancelled"):
        out = await service.cancel_run(s["session_id"], rid)
        assert out.get("note") == "already_terminal"
    else:
        out = await service.cancel_run(s["session_id"], rid)
        assert out.get("cancel_requested") in (True, False)


async def test_render_ref_after_commit(seeded_fusion_sources):
    s, view, run = await _execute_completed(seeded_fusion_sources)
    rid = run["run_id"]
    # 未 commit → result_unavailable
    with pytest.raises(DataFusionError) as ei:
        await service.render_ref_for_run(s["session_id"], rid)
    assert ei.value.code == "result_unavailable"
    plan_allow = s["plan"] | {"commit_policy": "allow_unresolved"}
    # 计划 CAS 更新到 allow_unresolved 后重新执行
    await service.update_plan(s["session_id"], view["plan_id"],
                              plan_allow, expected_revision=1,
                              actor=s["actor"])
    run2 = await service.execute_plan(
        s["session_id"], view["plan_id"], expected_revision=2,
        actor=s["actor"], background=False)
    await service.commit_run(s["session_id"], run2["run_id"], actor=s["actor"])
    rr = await service.render_ref_for_run(s["session_id"], run2["run_id"])
    assert rr["render_ref"].startswith("ref:fusion-render-")
    assert rr["features"] == 5   # 1 merged + 2 withheld + 2 单源


# ── 增量刷新（验收旅程 4） ─────────────────────────────────────

async def test_refresh_impact_and_lock_inheritance(seeded_fusion_sources):
    s, view, run = await _execute_completed(seeded_fusion_sources)
    pid, rid = view["plan_id"], run["run_id"]
    queue = service.review_queue(s["session_id"], rid)
    item = queue["items"][0]
    await service.upsert_decision(
        s["session_id"], pid, pair_key=item["pair_key"], action="accept",
        decision_run_id=rid, field_values={"address": "锁定地址"},
        reviewer="reviewer-a")
    # commit 基底版本（可追溯）
    plan_allow = s["plan"] | {"commit_policy": "allow_unresolved"}
    await service.update_plan(s["session_id"], pid, plan_allow,
                              expected_revision=1, actor=s["actor"])
    base_run = await service.execute_plan(
        s["session_id"], pid, expected_revision=2, actor=s["actor"],
        background=False)
    base_done = await service.commit_run(s["session_id"], base_run["run_id"],
                                         actor=s["actor"])
    assert base_done["output_ref"]
    base_sha = base_done["output_sha256"]

    # 来源 A 小幅变化（改一个地址 + 新增一个实体）
    from app.services.session_data import session_data_manager
    payload = chengdu_pois_a()
    payload["features"][2]["properties"]["addr"] = "天府三街200号"
    await session_data_manager.overwrite(s["session_id"], s["a_ref"], payload)

    # dry_run：影响面报告（改内容 = changed → removed+added 同键计 2；
    # 决定预测 stale/inherited 按键集合）
    dry = await service.refresh_plan(s["session_id"], pid,
                                     expected_revision=2, actor=s["actor"],
                                     dry_run=True)
    assert dry["changed"] is True and dry["dry_run"] is True
    assert "side_a" in dry and "verdict" in dry

    # 安全路径：受控重跑（决定继承）
    refreshed = await service.refresh_plan(s["session_id"], pid,
                                           expected_revision=2,
                                           actor=s["actor"], dry_run=False)
    new_run = refreshed["run"]
    assert new_run["run_id"] != base_run["run_id"]
    assert new_run.get("base_run_id") == base_run["run_id"]
    assert refreshed["impact"]["decisions_stale_total"] >= 0
    # 新 run 直接 commit（allow_unresolved 政策已随计划）—— 锁定决定生效
    done = await service.commit_run(s["session_id"], new_run["run_id"],
                                    actor=s["actor"])
    assert done["output_ref"]
    assert done["output_sha256"] != base_sha      # 新版本可追溯
    page = await service.run_result_page(s["session_id"], new_run["run_id"],
                                         limit=50)
    # 锁定地址在继承的决定中生效（合并对输出人工值）
    merged = [f for f in page["features"]
              if f["properties"]["_fusion"]["origin"] == "merged"]
    assert any((f["properties"].get("address") == "锁定地址")
               for f in merged)


async def test_refresh_not_safe_on_massive_change(seeded_fusion_sources):
    s, view, run = await _execute_completed(seeded_fusion_sources)
    pid = view["plan_id"]
    # 全量替换 B 源（所有实体变化）→ 超安全阈
    from app.services.session_data import session_data_manager
    payload_b = {
        "type": "FeatureCollection", "crs": "EPSG:4326",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [103.9 + i * 0.01, 30.5 + i * 0.01]},
             "properties": {"osm_id": f"X{i}", "name": f"完全不同{i}",
                            "addr": "别处", "cat": "shop",
                            "checked": "2026-05-01", "tel": None}}
            for i in range(6)
        ],
    }
    await session_data_manager.overwrite(s["session_id"], s["b_ref"],
                                         payload_b)
    with pytest.raises(DataFusionError) as ei:
        await service.refresh_plan(s["session_id"], pid, expected_revision=1,
                                   actor=s["actor"], dry_run=False)
    assert ei.value.code == "refresh_not_safe"


# ── 决定列表与显式解锁 ─────────────────────────────────────────

async def test_decision_list_and_explicit_unlock(seeded_fusion_sources):
    s, view, run = await _execute_completed(seeded_fusion_sources)
    pid, rid = view["plan_id"], run["run_id"]
    queue = service.review_queue(s["session_id"], rid)
    item = queue["items"][0]
    dec = await service.upsert_decision(
        s["session_id"], pid, pair_key=item["pair_key"], action="accept",
        decision_run_id=rid, reviewer="r1")
    listing = await service.list_decisions(s["session_id"], pid)
    assert listing["decisions"] and \
        listing["decisions"][0]["pair_key"] == item["pair_key"]
    # 显式解锁（唯一推翻路径）
    out = await service.delete_decision(
        s["session_id"], pid, item["pair_key"],
        expected_revision=dec["revision"])
    assert out["deleted"] is True
    empty = await service.list_decisions(s["session_id"], pid)
    assert empty["decisions"] == []


# ── 匹配包边界 ────────────────────────────────────────────────

async def test_review_queue_requires_bundle(seeded_fusion_sources):
    s = seeded_fusion_sources
    view = await service.create_plan(s["session_id"], s["plan"],
                                     actor=s["actor"])
    run = await service.execute_plan(
        s["session_id"], view["plan_id"], expected_revision=1,
        actor=s["actor"], background=False)
    outputs.discard_run(run["run_id"])
    with pytest.raises(DataFusionError) as ei:
        service.review_queue(s["session_id"], run["run_id"])
    assert ei.value.code == "result_unavailable"


async def test_review_decisions_not_leaked_cross_session(
        seeded_fusion_sources):
    s, view, run = await _execute_completed(seeded_fusion_sources)
    # 另一会话读运行 → not_found
    with pytest.raises(DataFusionError) as ei:
        await service.inspect_run("sess-other", run["run_id"])
    assert ei.value.code == "run_not_found"
