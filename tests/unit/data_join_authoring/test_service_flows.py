"""G38 服务编排面验收（计划/预览/执行/幂等/恢复/越权）。

覆盖 J15（完整行数 vs 有界收据）、J17（源漂移 stale）、J19（并发幂等收敛）、
J21（内容发布后元数据失败 → 恢复）、J22（跨会话越权）、J28（内容损坏失效）。
"""
from __future__ import annotations


import pytest

pytestmark = pytest.mark.usefixtures("db_tables")

from app.services.data_join_authoring import outputs, service
from app.services.data_join_authoring.errors import DataJoinError
from tests.unit.data_join_authoring.conftest import seed_session_source
from tests.unit.data_join_authoring.fixtures import attr_plan


def _flagship_plan(seeded_sources) -> dict:
    return attr_plan(
        left={"kind": "session_ref", "session_id": seeded_sources["session_id"],
              "ref": seeded_sources["left_ref"]},
        right={"kind": "table_rows", "session_id": seeded_sources["session_id"],
               "ref": seeded_sources["right_ref"]},
    )


async def test_plan_crud_cas(seeded_sources):
    plan_data = _flagship_plan(seeded_sources)
    view = await service.create_plan(seeded_sources["session_id"], plan_data,
                                     actor=seeded_sources["actor"])
    pid, rev1 = view["plan_id"], view["revision"]
    assert rev1 == 1 and view["status"] == "draft"
    # CAS：错修订号 → stale
    plan_data["keep_side"] = "inner"
    with pytest.raises(DataJoinError) as ei:
        await service.update_plan(seeded_sources["session_id"], pid, plan_data,
                                  expected_revision=99, actor=seeded_sources["actor"])
    assert ei.value.code == "stale_plan_revision"
    # 正确 CAS → revision 2，预览证据清空
    view2 = await service.update_plan(seeded_sources["session_id"], pid, plan_data,
                                      expected_revision=rev1,
                                      actor=seeded_sources["actor"])
    assert view2["revision"] == 2
    # 越权：另一会话读不到（not_owned）
    with pytest.raises(DataJoinError) as ei:
        await service.get_plan("sess-other", pid)
    assert ei.value.code == "not_owned"


async def test_preview_full_diagnostics_and_evidence(seeded_sources):
    view = await service.create_plan(seeded_sources["session_id"], _flagship_plan(seeded_sources),
                                     actor=seeded_sources["actor"])
    result = await service.preview_plan(
        seeded_sources["session_id"], view["plan_id"], expected_revision=1,
        actor=seeded_sources["actor"])
    assert result["status"] == "ready"
    d = result["diagnostics"]
    # 全量计数（J01/J02 同源 oracle）：4 区、4 人口行、1 无匹配右行
    assert d["left_rows"] == 4 and d["right_rows"] == 4
    assert d["matched_pairs"] == 3
    assert d["unmatched_left_rows"] == 1
    assert d["unmatched_right_rows"] == 1
    assert d["basis"] == "full"
    assert 0 < result["sample_row_count"] <= 50
    # 预览证据已落计划行且绑定 digest+指纹
    stored = await service.get_plan(seeded_sources["session_id"], view["plan_id"])
    assert stored["preview"]["status"] == "ready"
    assert stored["left_fingerprint"] and stored["right_fingerprint"]
    assert stored["preview_digest"]


async def test_preview_runs_join_off_event_loop(seeded_sources, monkeypatch):
    """预览的全量 join 不在事件循环线程执行（与执行体 asyncio.to_thread 一致）。"""
    import threading

    view = await service.create_plan(
        seeded_sources["session_id"], _flagship_plan(seeded_sources),
        actor=seeded_sources["actor"])
    loop_ident = threading.get_ident()
    seen = {}
    real_run = service.JoinExecutor.run_attribute

    def recording_run(self, left, right):
        seen["ident"] = threading.get_ident()
        return real_run(self, left, right)

    monkeypatch.setattr(service.JoinExecutor, "run_attribute", recording_run)
    result = await service.preview_plan(
        seeded_sources["session_id"], view["plan_id"], expected_revision=1,
        actor=seeded_sources["actor"])
    assert result["status"] == "ready"
    assert seen["ident"] != loop_ident


async def test_execute_inline_completes_with_full_receipt(seeded_sources):
    view = await service.create_plan(seeded_sources["session_id"], _flagship_plan(seeded_sources),
                                     actor=seeded_sources["actor"])
    run = await service.execute_plan(
        seeded_sources["session_id"], view["plan_id"], expected_revision=1,
        actor=seeded_sources["actor"], background=False)
    assert run["status"] == "completed"
    assert run["output_rows"] == 4                 # 完整行数（非 200 行截断）
    receipt = run["receipt"]
    assert receipt["output_kind"] == "enriched_layer"
    assert receipt["counts"]["matched_pairs"] == 3
    assert receipt["left_version"]["fingerprint"]
    assert receipt["plan_digest"] == view["normalized_digest"]
    # 输出 ref 可读回：分页 + 总数
    page = await service.run_result_page(seeded_sources["session_id"], run["run_id"], 0, 2)
    assert page["total_rows"] == 4 and len(page["rows"]) == 2
    assert page["next_cursor"] == 2
    # 渲染投影（≤10k）可铸
    rr = await service.render_ref_for_run(seeded_sources["session_id"], run["run_id"])
    assert rr["rows"] == 4 and rr["render_ref"].startswith("ref:")


async def test_j19_idempotent_reexecute_converges_same_run(seeded_sources):
    view = await service.create_plan(seeded_sources["session_id"], _flagship_plan(seeded_sources),
                                     actor=seeded_sources["actor"])
    run1 = await service.execute_plan(
        seeded_sources["session_id"], view["plan_id"], expected_revision=1,
        actor=seeded_sources["actor"], background=False)
    run2 = await service.execute_plan(
        seeded_sources["session_id"], view["plan_id"], expected_revision=1,
        actor=seeded_sources["actor"], background=False)
    # 同幂等键 → 复用同一已完成 run（无双 ref 副作用）
    assert run2["run_id"] == run1["run_id"]
    assert run2.get("idempotent_reuse") is True


async def test_j17_source_drift_after_submit_blocks_stale_execute(
        seeded_sources, monkeypatch):
    """源在「提交冻结」与「执行体复核」之间漂移 → typed stale_source。

    提交与执行体在同一内联调用中无法自然竞态 —— 用确定性的解析器包装
    模拟窗口：第 3 次起（执行体阶段）右源返回不同内容指纹。
    """
    view = await service.create_plan(seeded_sources["session_id"],
                                     _flagship_plan(seeded_sources),
                                     actor=seeded_sources["actor"])
    real_resolve = service._resolve_source_view
    calls = {"n": 0}

    async def drifting(session_id, source, actor, side):
        view_src, ev = await real_resolve(session_id, source, actor, side)
        calls["n"] += 1
        if calls["n"] >= 3 and side == "right":
            # 执行体阶段：同一 ref 的内容版本前进（漂移）
            view_src.content_fingerprint = "drifted" + view_src.content_fingerprint[7:]
        return view_src, ev

    monkeypatch.setattr(service, "_resolve_source_view", drifting)
    with pytest.raises(DataJoinError) as ei:
        await service.execute_plan(seeded_sources["session_id"], view["plan_id"],
                                   expected_revision=1,
                                   actor=seeded_sources["actor"], background=False)
    assert ei.value.code == "stale_source"
    # run 行落 failed（无公开半成品）
    runs = await service.list_runs(seeded_sources["session_id"],
                                   plan_id=view["plan_id"])
    assert runs["runs"][0]["status"] == "failed"
    assert runs["runs"][0]["error_code"] == "stale_source"


async def test_j21_recovery_converges_published_content_without_run_row(seeded_sources):
    view = await service.create_plan(seeded_sources["session_id"], _flagship_plan(seeded_sources),
                                     actor=seeded_sources["actor"])
    run = await service.execute_plan(
        seeded_sources["session_id"], view["plan_id"], expected_revision=1,
        actor=seeded_sources["actor"], background=False)
    # 模拟「内容已发布、run 行未落终态」窗口：把 run 行掰回 running（无活跃 job）
    from app.core.database import SessionLocal
    from app.models.data_join_authoring import DataJoinRun

    with SessionLocal() as db:
        row = db.get(DataJoinRun, run["run_id"])
        row.status = "running"
        row.receipt = None
        db.commit()
    from app.services.data_join_authoring import recovery

    result = recovery.recover_runs(seeded_sources["session_id"])
    assert run["run_id"] in result["completed"]      # 收据+内容可验 → 补登记
    after = await service.inspect_run(seeded_sources["session_id"], run["run_id"])
    assert after["status"] == "completed"
    assert after["receipt"]["output_rows"] == 4
    # 反例：收据缺失 → failed（不假 completed）
    run2 = await service.execute_plan(  # noqa: F841 — 不同计划避免幂等命中
        seeded_sources["session_id"],
        (await service.create_plan(
            seeded_sources["session_id"],
            _flagship_plan(seeded_sources) | {"title": "second", "output_name": "第二次"},
            actor=seeded_sources["actor"]))["plan_id"],
        expected_revision=1, actor=seeded_sources["actor"], background=False)
    (outputs.final_dir(run2["run_id"]) / "receipt.json").unlink()
    with SessionLocal() as db:
        row = db.get(DataJoinRun, run2["run_id"])
        row.status = "running"
        db.commit()
    result2 = recovery.recover_runs(seeded_sources["session_id"])
    assert run2["run_id"] in result2["failed"]


async def test_j22_cross_session_denied(seeded_sources):
    view = await service.create_plan(seeded_sources["session_id"], _flagship_plan(seeded_sources),
                                     actor=seeded_sources["actor"])
    other = {"trusted_session_id": "sess-other"}
    for fn, args in (
        (service.get_plan, ("sess-other", view["plan_id"])),
        (service.inspect_run, ("sess-other", "dj-none")),
    ):
        with pytest.raises(DataJoinError) as ei:
            await fn(*args)
        assert ei.value.code in ("not_owned", "run_not_found")
    # 跨会话源解析：计划引用他人 session 的 ref → 拒绝（trusted 回退仅限本会话）
    bad = _flagship_plan(seeded_sources)
    bad["left"]["session_id"] = "sess-other"
    with pytest.raises(DataJoinError) as ei:
        await service.describe_source("sess-other", bad["left"], other)
    assert ei.value.code == "source_unresolvable"


async def test_j28_deleted_content_invalidates_reuse(seeded_sources):
    view = await service.create_plan(seeded_sources["session_id"], _flagship_plan(seeded_sources),
                                     actor=seeded_sources["actor"])
    run = await service.execute_plan(
        seeded_sources["session_id"], view["plan_id"], expected_revision=1,
        actor=seeded_sources["actor"], background=False)
    assert run["status"] == "completed"
    # 内容被删 → inspect typed 失效（不凭历史收据返回成功）
    outputs.discard_final(run["run_id"])
    after = await service.inspect_run(seeded_sources["session_id"], run["run_id"])
    assert after["status"] == "failed"
    assert after["error_code"] == "receipt_missing"
    assert after.get("content_invalidated") is True
    # 复用请求同样不再返回旧成功
    again = await service.execute_plan(
        seeded_sources["session_id"], view["plan_id"], expected_revision=1,
        actor=seeded_sources["actor"], background=False)
    assert again["status"] == "completed"           # 授权下新执行可恢复
    assert again["run_id"] != run["run_id"]         # 新 run（旧键已失效）


async def test_j15_bounded_receipt_full_rows_via_pages(seeded_sources):
    """1000+ 行经服务执行：收据有界、成果完整（分页到尾对账）。"""
    big_pop = {"rows": [
        {"adcode": f"1101{i:02d}", "pop": i, "year": 2020} for i in range(1200)
    ]}
    right_ref = await seed_session_source(seeded_sources["session_id"], big_pop,
                                          prefix="g38big")
    plan = attr_plan(
        left={"kind": "session_ref", "session_id": seeded_sources["session_id"],
              "ref": seeded_sources["left_ref"]},
        right={"kind": "table_rows", "session_id": seeded_sources["session_id"],
               "ref": right_ref},
        field_map=[{"side": "left", "field": "adcode"},
                   {"side": "right", "field": "pop"}],
        keep_side="inner",
    )
    view = await service.create_plan(seeded_sources["session_id"], plan,
                                     actor=seeded_sources["actor"])
    run = await service.execute_plan(
        seeded_sources["session_id"], view["plan_id"], expected_revision=1,
        actor=seeded_sources["actor"], background=False)
    assert run["status"] == "completed"
    # 大表完整性：右侧 1200 行；边界 4 码（110101/02/05/07）都在表内
    # （f"1101{i:02d}" 在 i=1/2/5/7 处命中）→ 匹配右行 4、未匹配右 1196
    receipt = run["receipt"]
    assert receipt["counts"]["right_rows"] == 1200
    assert receipt["counts"]["matched_pairs"] == 4
    assert receipt["counts"]["unmatched_right_rows"] == 1196
    total = 0
    cursor = 0
    while True:
        page = await service.run_result_page(seeded_sources["session_id"],
                                             run["run_id"], cursor, 200)
        total += len(page["rows"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert total == receipt["output_rows"] == 4
