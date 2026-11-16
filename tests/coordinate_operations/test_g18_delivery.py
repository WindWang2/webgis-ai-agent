"""G45 × G18 交付集成 —— C23（GPKG 保持 CRS）/ C24（GeoJSON 走 4326 副本）。

真实 G18 submit_delivery job → 等待完成 → download 路径经 dl_store 终态包
读回（pyogrio/GPKG、json/GeoJSON）；核验 CRS/行数/坐标/ID 与原生成果一致。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services.coordinate_operations import outputs, service
from tests.coordinate_operations.conftest import make_point_features
from tests.coordinate_operations.test_execute_flow import (
    ACTOR,
    SESSION,
    _lock_best,
    _run,
    _seed_source,
)

pytestmark = pytest.mark.usefixtures("co_env")


@pytest.fixture()
def owned_session(co_env):
    """播种 Conversation 行（G18 会话守卫需要真实归属事实）。"""
    from app.core.database import SessionLocal
    from app.models.db_model import Conversation

    with SessionLocal() as db:
        db.add(Conversation(id=SESSION, user_id="u-g45", title="G45 交付"))
        db.commit()
    return SESSION


def _complete_run(seed_fc_ref, n=25):
    src = _seed_source(None, seed_fc_ref, n=n)
    plan = _run(service.create_plan(
        SESSION, {"title": "交付链", "source": {"kind": "session_ref",
                                                 "ref": src["ref"]},
                  "target_crs": "EPSG:32650",
                  "z_policy": "preserve_unconverted",
                  "empty_geometry_policy": "keep",
                  "out_of_area_policy": "report"}, actor=ACTOR))
    view, _ = _lock_best(None, {"kind": "session_ref", "ref": src["ref"]},
                         plan_id=plan["plan_id"], revision=plan["revision"])
    run = _run(service.execute_plan(
        SESSION, plan["plan_id"], expected_revision=view["revision"],
        actor=ACTOR, background=False))
    assert run["status"] == "completed"
    return run


def _deliver_and_wait(spec_dict, acks=()):
    """同事件循环内提交 + 等待终态（后台 runner task 依附该循环）。"""

    async def _go():
        from app.schemas.data_delivery_schema import DeliverySpec
        from app.services.data_delivery.service import submit_delivery
        from app.services.data_delivery.source_resolver import DeliveryActor

        spec = DeliverySpec(**spec_dict)
        out = await submit_delivery(
            spec, DeliveryActor(user_id="u-g45"),
            acknowledged_losses=list(acks))
        job_id = int(out["job_id"])
        from app.core.database import SessionLocal
        from app.services.jobs.store import DurableJobStore

        for _ in range(300):
            with SessionLocal() as db:
                rec = DurableJobStore.get_sync(db, job_id)
                status = str(rec.status) if rec else "missing"
                params = rec.parameters if rec else {}
                db.commit()
            if status in ("completed", "failed", "cancelled"):
                return status, params, job_id
            await asyncio.sleep(0.2)
        return "timeout", {}, job_id

    return _run(_go())


def _download(job_id):
    from app.services.data_delivery import store as dl_store

    _ = None  # noqa: F841 — 历史占位
    # 交付收据在 DATA_DIR/data-delivery/<id>/；用 dl_store 读终态包
    delivery_id = None
    from app.core.database import SessionLocal
    from app.services.jobs.store import DurableJobStore

    with SessionLocal() as db:
        rec = DurableJobStore.get_sync(db, int(job_id))
        delivery_id = rec.parameters.get("delivery_id") if rec else None
        db.commit()
    if delivery_id is None:
        # result_ref 形如 data-delivery/<id>
        with SessionLocal() as db:
            rec = DurableJobStore.get_sync(db, int(job_id))
            rr = rec.result_ref if rec else None
            db.commit()
        delivery_id = rr.split("/", 1)[1] if rr else None
    assert delivery_id, "delivery_id 不可得"
    d = dl_store.final_dir(delivery_id)
    # 找到输出文件（唯一非 manifest/receipt 文件）
    files = [p for p in d.iterdir() if p.is_file()
             and p.name not in ("manifest.json", "receipt.json")]
    assert len(files) == 1, [p.name for p in files]
    return files[0].read_bytes()


class TestG18Delivery:
    def test_c23_gpkg_keeps_target_crs_no_reselection(
            self, co_env, seed_fc_ref, owned_session):
        """G18 GPKG 交付：无 target_crs → 不建 transformer → CRS/坐标/行数
        与原生成果一致（无再次选操作）。"""
        run = _complete_run(seed_fc_ref, n=25)
        receipt = run["receipt"]
        fabric_ref = receipt["native_refs"]["native_fabric_ref"]
        if fabric_ref is None:
            # 无 pyarrow 环境回退 session ref 路径（同一语义）
            fabric_ref = None
        src_kind = "fabric_parquet" if fabric_ref else "session_ref"
        src_ref = fabric_ref or receipt["native_refs"]["output_session_ref"]
        assert src_ref, "主成果交付 ref 缺失"

        status, _params, job_id = _deliver_and_wait({
            "source": {"kind": src_kind, "session_id": SESSION, "ref": src_ref},
            "format": "gpkg",
            "filename": "g45-native",
        })
        assert status == "completed", (status, _params)
        blob = _download(job_id)
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tf:
            tf.write(blob)
            gpkg_path = Path(tf.name)
        import pyogrio

        info = pyogrio.read_info(str(gpkg_path))
        gdf = pyogrio.read_dataframe(str(gpkg_path))
        # CRS 保持 EPSG:32650（无再次选操作）
        crs = info["crs"]
        assert crs is not None and "32650" in str(crs), crs
        assert len(gdf) == 25
        # 坐标与原生成果一致（首点 oracle 对账）
        native = outputs.load_output_fc(run["run_id"])
        p0 = native["features"][0]

        g0 = gdf.geometry.iloc[0]
        assert abs(g0.x - p0["geometry"]["coordinates"][0]) < 1e-6
        assert abs(g0.y - p0["geometry"]["coordinates"][1]) < 1e-6
        # 稳定 ID 保真
        assert str(gdf.iloc[0]["_wfid"]) == p0["properties"]["_wfid"]

    def test_c24_geojson_uses_wgs84_display_copy(
            self, co_env, seed_fc_ref, owned_session):
        """G18 GeoJSON（RFC 7946）交付：走本域 WGS84 显示副本 —— 4326 范围、
        独立转换链证据在收据。"""
        run = _complete_run(seed_fc_ref, n=12)
        display = run["receipt"]["display"]
        assert display["state"] == "available"
        status, _params, job_id = _deliver_and_wait({
            "source": {"kind": "session_ref", "session_id": SESSION,
                       "ref": display["display_ref"]},
            "format": "geojson",
            "filename": "g45-display",
        })
        assert status == "completed", (status, _params)
        blob = _download(job_id)
        fc = json.loads(blob.decode("utf-8"))
        feats = fc["features"]
        assert len(feats) == 12
        # 4326 度范围（显示副本），不是投影米
        for f in feats:
            xy = f["geometry"]["coordinates"]
            assert -180 <= xy[0] <= 180 and -90 <= xy[1] <= 90
        # 与显示链独立证据一致：32650 → 4326 逆变换对账（解析 oracle 允差内）
        native = outputs.load_output_fc(run["run_id"])
        from tests.coordinate_operations.test_kernel_and_candidates import (
            _utm50n_analytic,
        )
        n0 = native["features"][0]["geometry"]["coordinates"][:2]
        want_lonlat_src = make_point_features(1)[0]["geometry"]["coordinates"][:2]
        want_utm = _utm50n_analytic(*want_lonlat_src)
        assert abs(n0[0] - want_utm[0]) < 0.01  # 原生成果仍是 UTM
        d0 = feats[0]["geometry"]["coordinates"]
        assert abs(d0[0] - want_lonlat_src[0]) < 1e-6  # 显示副本回到原始经纬度
        assert abs(d0[1] - want_lonlat_src[1]) < 1e-6

    def test_c21_non4326_display_mountable(self, co_env, seed_fc_ref):
        """C21：非 4326 主成果的显示层挂独立真实 4326 ref（非投影米直喂）。"""
        run = _complete_run(seed_fc_ref, n=8)
        d = run["receipt"]["display"]
        assert d["state"] == "available"
        assert d["display_ref"].startswith("ref:coord-ops-display-")
        assert d["operation"]["definition_sha256"]  # 显示链操作证据
        # ref 内载荷核验（4326 度）
        from app.services.session_data import session_data_manager

        fc = _run(session_data_manager.get(SESSION, d["display_ref"]))
        xy = fc["features"][0]["geometry"]["coordinates"]
        assert -180 <= xy[0] <= 180 and -90 <= xy[1] <= 90
