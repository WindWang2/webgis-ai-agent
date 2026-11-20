"""回归：#2141 / #2142 / #2143 / #2201 / #2208（R1/R2 审计残留项）。

- #2208：data_join_runs.job_id 64 位（PG BigInteger；迁移 0109 遵循
  0101 模式）——曾存 64 位 analysis_tasks.id 于 32 位 Integer。
- #2201：list jobs 的租户/归属过滤下推 SQL 后再 LIMIT（曾先全租户
  LIMIT 再 Python 过滤 → 新任务多时静默截断）。
- #2141：revise 的 first_step_preview 走有界窗口读 + 路由过
  cube_json_response/413 闸（曾整幅全波段 .tolist() → OOM）。
- #2142：rs_cube 完整性校验到 (var,time,label) 粒度（曾只查
  var_times，缺失波段时间片被 np.zeros 静默填 0 冒充完整 cube）。
- #2143：N27 revise 截断/time_index 缺省/band 空 → typed 拒绝；
  N28/N33 复用键按顺序指纹 + 波段映射；N29 事件循环内 zarr IO 入线程；
  N30 virtual budget_exceeded 假绿；N34 check_crs 仅 ImportError 降级；
  N35 0 长度维度 typed 拒绝。
"""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

# ── #2208 ────────────────────────────────────────────────────────────────


def test_2208_job_id_is_bigint_postgres_integer_sqlite():
    from sqlalchemy import BigInteger, Integer
    from sqlalchemy.dialects import postgresql, sqlite

    from app.models.data_join_authoring import DataJoinRun

    col = DataJoinRun.__table__.c.job_id
    assert col.nullable
    assert isinstance(col.type.dialect_impl(postgresql.dialect()), BigInteger)
    assert isinstance(col.type.dialect_impl(sqlite.dialect()), Integer)


def test_2208_migration_alters_job_id_on_0107_chain():
    src = open(
        "migrations/versions/0109_data_join_runs_job_id_bigint.py"
    ).read()
    assert "0108_layers_org_fk_ondelete" in src and "job_id" in src
    assert "BigInteger" in src and '"job_id"' in src
    assert "batch_alter_table" in src


# ── #2201 ────────────────────────────────────────────────────────────────


def _seed_delivery_task(db, *, creator, project=None, session=None,
                        token=None, minutes_old=0, org=None):
    from datetime import datetime, timedelta, timezone

    from app.models.db_model import AnalysisTask

    rec = AnalysisTask(
        task_type="data_delivery", parameters={},
        creator_id=creator, project_id=project, session_id=session,
        owner_token=token, org_id=org,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_old),
    )
    db.add(rec)
    db.flush()
    return rec


def test_2201_limit_applies_after_visibility_filter():
    """跨租户新任务把可见的旧任务挤出 LIMIT 窗口的回归：过滤必须先于
    LIMIT（此前先 LIMIT 50 再 Python 过滤 → 返回截断/空）。"""
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from app.services.data_delivery.service import _list_delivery_jobs_sync
    from app.services.data_delivery.source_resolver import DeliveryActor

    actor = DeliveryActor(user_id="user-me", org_id=None, owner_token=None)
    with SessionLocal() as db:
        # 60 条**更新的**他人任务（挤满 LIMIT 窗口）+ 1 条我的旧任务。
        for i in range(60):
            _seed_delivery_task(db, creator=f"other-{i}", minutes_old=0)
        mine = _seed_delivery_task(db, creator="user-me", minutes_old=600)
        db.commit()
        mine_id = mine.id
        try:
            views = _list_delivery_jobs_sync(actor, None, 50)
        finally:
            db.query(AnalysisTask).filter(
                AnalysisTask.task_type == "data_delivery").delete()
            db.commit()
    ids = [v.job_id for v in views]
    assert str(mine_id) in ids, (
        "visible job older than the flood must survive LIMIT — filter must "
        "be pushed into SQL before LIMIT"
    )
    assert len(ids) == 1
    assert all(str(mine_id) == i for i in ids)


def test_2201_project_filter_pushed_to_sql():
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from app.services.data_delivery.service import _list_delivery_jobs_sync
    from app.services.data_delivery.source_resolver import DeliveryActor

    actor = DeliveryActor(user_id="user-me", org_id=None, owner_token=None)
    with SessionLocal() as db:
        _seed_delivery_task(db, creator="user-me", project="proj-A")
        _seed_delivery_task(db, creator="user-me", project="proj-B")
        db.commit()
        try:
            views = _list_delivery_jobs_sync(actor, "proj-B", 50)
        finally:
            db.query(AnalysisTask).filter(
                AnalysisTask.task_type == "data_delivery").delete()
            db.commit()
    assert len(views) == 1


def test_2201_session_and_project_visibility_sql_equivalent():
    """会话归属（owner_token 匿名行）与项目归属（owner/org/legacy 公开）
    的下推条件与 _job_authorize 语义一致。"""
    from app.core.database import SessionLocal
    from app.core.owner_tokens import owner_token_digest
    from app.models.db_model import AnalysisTask, Conversation
    from app.models.project import Project
    from app.services.data_delivery.service import _list_delivery_jobs_sync
    from app.services.data_delivery.source_resolver import DeliveryActor

    token = "tok-2201-secret"
    with SessionLocal() as db:
        # OwnerTokenColumn 自行 digest 绑定值 —— 种子存原始 token（与生产
        # 写入路径一致），预 digest 会造成双哈希失配。
        conv = Conversation(id="conv-2201", user_id=None, owner_token=token)
        proj_owned = Project(id="proj-own", name="p", status="active",
                             owner_id="user-me", org_id=None)
        proj_org = Project(id="proj-org", name="p", status="active",
                           owner_id=None, org_id=7)
        proj_legacy = Project(id="proj-pub", name="p", status="active",
                              owner_id=None, org_id=None)
        db.add_all([conv, proj_owned, proj_org, proj_legacy])
        via_token = _seed_delivery_task(
            db, creator=None, session="conv-2201", token=token)
        via_owner = _seed_delivery_task(
            db, creator=None, project="proj-own")
        via_org = _seed_delivery_task(
            db, creator=None, project="proj-org", org=None)
        via_legacy = _seed_delivery_task(
            db, creator=None, project="proj-pub")
        stranger = _seed_delivery_task(
            db, creator="someone-else", project="proj-own")
        db.commit()
        ids = [via_token.id, via_owner.id, via_org.id, via_legacy.id,
               stranger.id]
        try:
            anon = DeliveryActor(user_id=None, org_id=None,
                                 owner_token=token)
            views = _list_delivery_jobs_sync(anon, None, 50)
            got = {v.job_id for v in views}
            # 匿名 + token：仅会话 token 行 + legacy 公开项目行。
            assert str(via_token.id) in got
            assert str(via_legacy.id) in got
            assert str(via_owner.id) not in got
            assert str(via_org.id) not in got
            assert str(stranger.id) not in got

            org_actor = DeliveryActor(user_id=None, org_id=7,
                                      owner_token=None)
            got = {v.job_id for v in _list_delivery_jobs_sync(
                org_actor, None, 50)}
            assert str(via_org.id) in got, "same-org ownerless project row"
            assert str(via_token.id) not in got
        finally:
            db.query(AnalysisTask).filter(AnalysisTask.id.in_(ids)).delete(
                synchronize_session=False)
            db.query(Conversation).filter(Conversation.id == "conv-2201").delete()
            for pid in ("proj-own", "proj-org", "proj-pub"):
                db.query(Project).filter(Project.id == pid).delete()
            db.commit()


# ── #2141 / #2143 N27 ───────────────────────────────────────────────────


@pytest.fixture()
def cube_env(tmp_path, monkeypatch):
    pytest.importorskip("zarr")
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    src = tmp_path / "data" / "src"
    src.mkdir(parents=True, exist_ok=True)
    return src


def _write_slice(path, value, h=48, w=64):
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=1,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(0, h, 1, 1), nodata=-9999.0,
    ) as dst:
        dst.write(np.full((h, w), value, dtype="float32"), 1)
    return str(path)


@pytest.mark.asyncio
async def test_2141_preview_bounded_and_revise_response_gate(cube_env):
    """小 cube：预览值与形状正确（happy path 不回退）。"""
    from app.services.lakehouse.cube_service import (
        build_session_cube,
        revise_session_cube,
    )

    t0 = _write_slice(cube_env / "t0.tif", 1.0)
    built = await build_session_cube(
        "sess-2141", time_sources=[{"time": "2024-01", "source": t0}],
        title="base",
    )
    t1 = _write_slice(cube_env / "t1.tif", 9.0)
    rev = await revise_session_cube(
        "sess-2141", built["ref"],
        updates=[{"band": "b1", "time_index": 0, "source": t1}],
        title="rev",
    )
    assert rev["first_step_preview"] is not None
    assert float(rev["first_step_preview"]["b1"][0][0][0]) == 9.0
    assert rev["preview_shape"]["b1"] == [1, 48, 64]


@pytest.mark.asyncio
async def test_2141_preview_degrades_honestly_on_budget(cube_env):
    """3000×3000 单时间步 = 9M cells > 8M 预算：修订成功，预览诚实降级
    为 None（此前整幅 .tolist() 在路由 JSON 化前已 OOM）。"""
    from app.services.lakehouse.cube_service import (
        build_session_cube,
        revise_session_cube,
    )

    h = w = 3000  # 1 band × 1 time × 9M cells > CUBE_WINDOW_MAX_CELLS(8M)
    t0 = _write_slice(cube_env / "big0.tif", 1.0, h=h, w=w)
    built = await build_session_cube(
        "sess-2141b", time_sources=[{"time": "2024-01", "source": t0}],
        title="big",
    )
    t1 = _write_slice(cube_env / "big1.tif", 2.0, h=h, w=w)
    rev = await revise_session_cube(
        "sess-2141b", built["ref"],
        updates=[{"band": "b1", "time_index": 0, "source": t1}],
        title="big-rev",
    )
    assert rev["status"] == "success"
    assert rev["first_step_preview"] is None, (
        "over-budget preview must degrade to None, never OOM"
    )
    assert rev["preview_shape"] in (None, {})


@pytest.mark.asyncio
async def test_2143_n27_revise_rejects_truncation_and_defaults(cube_env):
    from app.services.lakehouse.cube_service import (
        CubeServiceError,
        build_session_cube,
        revise_session_cube,
    )

    t0 = _write_slice(cube_env / "n27.tif", 1.0)
    built = await build_session_cube(
        "sess-n27", time_sources=[{"time": "2024-01", "source": t0}],
        title="base",
    )
    t1 = _write_slice(cube_env / "n27b.tif", 2.0)
    updates = [
        {"band": "b1", "time_index": 0, "source": t1},
    ] * 65
    with pytest.raises(CubeServiceError, match="bounded budget 64"):
        await revise_session_cube("sess-n27", built["ref"],
                                  updates=updates, title="x")
    with pytest.raises(CubeServiceError, match="missing 'time_index'"):
        await revise_session_cube(
            "sess-n27", built["ref"],
            updates=[{"band": "b1", "source": t1}], title="x")
    with pytest.raises(CubeServiceError, match="missing 'band'"):
        await revise_session_cube(
            "sess-n27", built["ref"],
            updates=[{"time_index": 0, "source": t1}], title="x")
    with pytest.raises(CubeServiceError, match="missing 'source'"):
        await revise_session_cube(
            "sess-n27", built["ref"],
            updates=[{"band": "b1", "time_index": 0}], title="x")


# ── #2142 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_2142_missing_band_time_plane_rejected(cube_env):
    """B1(t1,t2) + B2(t1) 曾通过 (variable,time) 校验、(t2,B2) 平面被
    np.zeros 静默填 0 发布 —— 现 typed 拒绝（RS_CUBE_INCOMPLETE）。"""
    from app.services.lakehouse.rs_cube import RSCubeError, build_rs_cube

    sources = [
        {"time": "t1", "role": "optical", "band": "B1",
         "source": _write_slice(cube_env / "b1_t1.tif", 1.0)},
        {"time": "t2", "role": "optical", "band": "B1",
         "source": _write_slice(cube_env / "b1_t2.tif", 2.0)},
        {"time": "t1", "role": "optical", "band": "B2",
         "source": _write_slice(cube_env / "b2_t1.tif", 3.0)},
    ]
    with pytest.raises(RSCubeError, match=r"missing \(time, label\) planes"):
        await build_rs_cube("sess-2142", sources=sources, title="gap")
    try:
        await build_rs_cube("sess-2142", sources=sources, title="gap")
    except RSCubeError as e:
        assert e.code == "RS_CUBE_INCOMPLETE"


# ── #2143 N28/N33/N34/N35 ────────────────────────────────────────────────


def test_2143_n28_fingerprint_sequence_distinguishes_repeats():
    """[A,A,B] 与 [A,B,B] 曾得到同一复用键（path 键 dict 折叠重复源）。"""
    from app.services.lakehouse.cube_service import _cube_input_fingerprint
    from app.services.lakehouse.rs_cube import _rs_input_fingerprint

    fp_a, fp_b = "a" * 64, "b" * 64
    k1 = _cube_input_fingerprint([fp_a, fp_a, fp_b], ["t1", "t2", "t3"], None)
    k2 = _cube_input_fingerprint([fp_a, fp_b, fp_b], ["t1", "t2", "t3"], None)
    assert k1 != k2

    planes_a = [(fp_a, "reflectance", "t1", "B1"),
                (fp_a, "reflectance", "t2", "B2")]
    planes_b = [(fp_a, "reflectance", "t1", "B2"),
                (fp_a, "reflectance", "t2", "B1")]
    # N33：同文件不同波段分配必须产生不同复用键。
    assert _rs_input_fingerprint(planes_a, ["t1", "t2"]) != \
        _rs_input_fingerprint(planes_b, ["t1", "t2"])


def test_2143_n34_check_crs_no_fake_regex_fallback():
    from app.services.lakehouse.cube_schema import (
        CubeSchemaError,
        check_crs,
    )

    assert check_crs("EPSG:4326")["crs_checked"] == "rasterio"
    pytest.importorskip("rasterio")
    # rasterio 在场且拒绝（无效 EPSG）→ typed 拒绝，绝不回落正则冒充降级。
    with pytest.raises(CubeSchemaError):
        check_crs("EPSG:999999")


def test_2143_n35_zero_length_dim_typed_rejection():
    from app.services.lakehouse.cube_schema import (
        CubeSchemaError,
        validate_labeled_schema,
    )

    with pytest.raises(CubeSchemaError, match="zero-length dim"):
        validate_labeled_schema(
            dims=["time", "y", "x"],
            shape=[0, 4, 4],
            coordinates={"time": [], "y": [4.0, 3.0, 2.0, 1.0],
                         "x": [1.0, 2.0, 3.0, 4.0]},
            crs="EPSG:4326", dtype="float32",
        )


# ── #2143 N30 ────────────────────────────────────────────────────────────


def test_2143_n30_deep_verify_reports_budget_exceeded(tmp_path):
    """深度超预算（9 层菱形）曾只对已展开叶子校验 → 假绿 "verified"。"""
    from app.services.durable_blob_store import FilesystemBlobStore
    from app.services.lakehouse.data_object import (
        normalize_owner_scope,
        publish_data_object,
    )
    from app.services.lakehouse.virtual_object import (
        publish_virtual_object,
        verify_data_object_deep,
    )

    store = FilesystemBlobStore(tmp_path)
    scope = normalize_owner_scope(session_id="sess-budget")

    leaf = publish_data_object(
        {"d.bin": b"x"}, kind="cog_raster", owner_scope=scope, store=store,
    ).data_object_id
    current = [leaf]
    for _ in range(9):
        left = publish_virtual_object(
            current, kind_label="l", owner_scope=scope, store=store,
        )["data_object_id"]
        right = publish_virtual_object(
            current, kind_label="r", owner_scope=scope, store=store,
        )["data_object_id"]
        current = [left, right]
    deep = publish_virtual_object(
        current, kind_label="root", owner_scope=scope, store=store,
    )["data_object_id"]

    assert verify_data_object_deep(deep, store=store) == \
        "virtual_budget_exceeded"


# ── #2143 N29（无断言回归路径：事件循环阻塞行为）─────────────────────


@pytest.mark.asyncio
async def test_2143_n29_labeled_window_read_smoke(cube_env):
    """read_session_labeled_window 在 threaded open_cube/coords 重构后
    行为不变（labeled 窗口读 happy path）。"""
    from app.services.lakehouse.cube_service import (
        read_session_labeled_window,
    )
    from app.services.lakehouse.rs_cube import build_rs_cube

    sources = [
        {"time": "t1", "role": "optical", "band": "B1",
         "source": _write_slice(cube_env / "n29.tif", 5.0)},
    ]
    rs = await build_rs_cube("sess-n29", sources=sources, title="n29")
    out = await read_session_labeled_window(
        "sess-n29", rs["ref"],
        selection={"time": ["t1"]},
    )
    assert out["variables"]["reflectance"].shape == (1, 1, 48, 64)
    assert float(out["variables"]["reflectance"][0, 0, 0, 0]) == 5.0
