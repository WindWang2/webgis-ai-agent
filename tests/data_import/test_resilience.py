"""G17 恢复 / 取消 / 跨 owner / 预算（任务书场景 16-20）。"""
from __future__ import annotations

import asyncio
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_csv_bytes, make_geojson_bytes, point_feature  # noqa: E402

from app.core.config import settings
from app.services.data_import import budgets
from app.services.data_import.service import DataImportError, DataImportService
from app.services.session_data import session_data_manager

pytestmark = pytest.mark.heavy

SESS_A = "sess-g17-owner-a"
SESS_B = "sess-g17-owner-b"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    return DataImportService()


GEOJSON_ONE = make_geojson_bytes([point_feature({"n": 1}, 116.3, 39.8)])


async def test_preview_recovery_no_persisted_data(env, tmp_path):
    """场景 16：预览后离开页面 → 新实例恢复配置；正式数据未被创建。"""
    state = await env.create_batch(SESS_A, "user:a", [("pts.geojson", GEOJSON_ONE)])
    m = state.members[0]
    await env.save_plan(state.batch_id, SESS_A, {m.member_key: {"keep_fields": ["n"]}}, selected=[m.member_key])
    await env.preview_members(state.batch_id, SESS_A, [m.member_key], row_limit=10)

    # “重开页面” = 新服务实例从磁盘恢复
    fresh_service = DataImportService()
    restored = await fresh_service.get_batch(state.batch_id, SESS_A)
    assert restored.interpretations[m.member_key]["keep_fields"] == ["n"]
    assert restored.plan_version == 1
    assert m.member_key in restored.plan_hashes
    # 正式数据未被创建（无收据、无 ref）
    assert restored.receipts == {}
    assert restored.status.value in ("previewed",)


async def test_cancel_during_commit_no_late_success(env, tmp_path):
    """场景 17：提交中取消 → 终态不为成功；无收据成员复位；staging 可清理。"""
    csv = make_csv_bytes(
        [[116.0 + i * 0.001, 39.0 + i * 0.001] for i in range(2000)],
        ["lng", "lat"],
    )
    state = await env.create_batch(
        SESS_A, "user:a", [("pts.geojson", GEOJSON_ONE), ("big.csv", csv)]
    )
    keys = {m.display_name: m.member_key for m in state.members}
    await env.save_plan(
        state.batch_id, SESS_A,
        {
            keys["pts.geojson"]: {},
            keys["big.csv"]: {"source_crs_declared": "EPSG:4326"},
        },
        selected=list(keys.values()),
    )
    accepted = await env.start_commit(state.batch_id, SESS_A, background=True)
    assert accepted["status"] == "committing"
    out = await env.cancel_batch(state.batch_id, SESS_A)
    assert out["status"] == "cancelled"

    final = await env.get_batch(state.batch_id, SESS_A)
    assert final.status.value in ("cancelled", "partial", "failed")
    for m in final.members:
        assert m.status.value != "committing"   # 无卡死中间态
    # 已完成成员（若有）收据可读；无收据成员明确非完成
    for m in final.members:
        r = final.receipts.get(m.member_key)
        if m.status.value == "completed":
            assert r and r.status == "completed"
        else:
            assert r is None or r.status != "completed"
    # 清理未提交 staging 不影响已完成收据的事实披露
    await env.delete_batch(state.batch_id, SESS_A)


async def test_cross_owner_same_bytes(env, tmp_path):
    """场景 18：两个 owner 上传相同 bytes → 无跨 owner 复用/泄露。"""
    sa = await env.create_batch(SESS_A, "user:a", [("same.geojson", GEOJSON_ONE)])
    sb = await env.create_batch(SESS_B, "user:b", [("same.geojson", GEOJSON_ONE)])
    assert sa.batch_id != sb.batch_id

    ka = sa.members[0].member_key
    kb = sb.members[0].member_key
    await env.save_plan(sa.batch_id, SESS_A, {ka: {}}, selected=[ka])
    await env.save_plan(sb.batch_id, SESS_B, {kb: {}}, selected=[kb])
    outa = await env.start_commit(sa.batch_id, SESS_A, background=False)
    outb = await env.start_commit(sb.batch_id, SESS_B, background=False)
    refa = outa["receipts"][ka]["output_ref"]
    refb = outb["receipts"][kb]["output_ref"]
    # 各自会话的独立产物（摄入 dedup 本就会话内作用域）
    assert (await session_data_manager.get(SESS_A, refa)) is not None
    assert (await session_data_manager.get(SESS_B, refb)) is not None
    # 互相不可读对方批次
    with pytest.raises(DataImportError):
        await env.get_batch(sb.batch_id, SESS_A)
    with pytest.raises(DataImportError):
        await env.get_receipts(sa.batch_id, SESS_B)


async def test_restart_recovery_after_completed_commit(env, tmp_path):
    """场景 19：提交完成后“进程重启”（新实例）→ 产物与收据可恢复可读。"""
    state = await env.create_batch(SESS_A, "user:a", [("p.geojson", GEOJSON_ONE)])
    k = state.members[0].member_key
    await env.save_plan(state.batch_id, SESS_A, {k: {}}, selected=[k])
    out = await env.start_commit(state.batch_id, SESS_A, background=False)
    ref = out["receipts"][k]["output_ref"]

    fresh = DataImportService()
    restored = await fresh.get_batch(state.batch_id, SESS_A)
    assert restored.status.value == "completed"
    assert restored.receipts[k].output_ref == ref
    assert (await session_data_manager.get(SESS_A, ref)) is not None

    # 崩溃残留 COMMITTING：无收据成员复位 planned，绝不显示成功
    # 模拟崩溃残留：手工把状态写回 committing（写盘后进程被杀）
    from app.services.data_import.models import BatchStatus, MemberStatus
    restored.status = BatchStatus.COMMITTING
    for m in restored.members:
        m.status = MemberStatus.COMMITTING
    from app.services.data_import import staging as staging_mod
    staging_mod.save_state(restored)

    svc3 = DataImportService()
    norm = await svc3.get_batch(state.batch_id, SESS_A)
    assert norm.status.value == "completed"          # 收据在 → 保持完成
    km = norm.members[0]
    assert km.status.value == "completed"


async def test_budget_bounded_rejections(env, tmp_path, monkeypatch):
    """场景 20：文件数/zip 条目/成员数预算 → 有界拒绝，不静默继续。"""
    # 文件数超限（默认 20）
    many = [(f"f{i}.geojson", GEOJSON_ONE) for i in range(budgets.MAX_FILES_PER_BATCH + 1)]
    with pytest.raises(DataImportError) as ei:
        await env.create_batch(SESS_A, "user:a", many)
    assert ei.value.code == "TOO_MANY_FILES"

    # 空/零字节文件
    with pytest.raises(DataImportError) as ei2:
        await env.create_batch(SESS_A, "user:a", [("e.geojson", b"")])
    assert ei2.value.code == "EMPTY_FILE"

    # zip 条目超限（4096）—— 带一套合法 shapefile 组件以通过组件校验
    import geopandas as gpd
    from shapely.geometry import Point

    work0 = tmp_path / "zipbudget"
    work0.mkdir()
    g0 = gpd.GeoDataFrame({"v": [1]}, geometry=[Point(0, 0)], crs="EPSG:4326")
    g0.to_file(work0 / "ok.shp", engine="pyogrio")
    big_zip = tmp_path / "many.zip"
    with zipfile.ZipFile(big_zip, "w") as zf:
        for comp in (".shp", ".dbf", ".shx", ".prj", ".cpg"):
            zf.write(work0 / f"ok{comp}", f"ok{comp}")
        for i in range(budgets.MAX_ZIP_ENTRIES + 1):
            zf.writestr(f"e{i}.txt", "x")
    with pytest.raises(DataImportError) as ei3:
        await env.create_batch(SESS_A, "user:a", [("many.zip", big_zip.read_bytes())])
    assert ei3.value.code in ("TOO_MANY_ZIP_ENTRIES",)

    # 成员数超限（把机制阈值下调后用真实多套 shapefile 触发）
    work = tmp_path / "sets"
    work.mkdir()
    sets_zip = tmp_path / "sets.zip"
    g = gpd.GeoDataFrame({"v": [1]}, geometry=[Point(0, 0)], crs="EPSG:4326")
    with zipfile.ZipFile(sets_zip, "w") as zf:
        for i in range(4):
            g.to_file(work / f"s{i}.shp", engine="pyogrio")
            for comp in (".shp", ".dbf", ".shx", ".prj", ".cpg"):
                zf.write(work / f"s{i}{comp}", f"s{i}{comp}")
    monkeypatch.setattr(budgets, "MAX_MEMBERS_PER_CONTAINER", 3)
    with pytest.raises(DataImportError) as ei4:
        await env.create_batch(SESS_A, "user:a", [("sets.zip", sets_zip.read_bytes())])
    assert ei4.value.code == "TOO_MANY_LAYERS"
    # 有界拒绝后不残留 staging 目录（失败清理）
    assert not (tmp_path / "import_batches" / "sets.zip").exists()


async def test_expired_batch_lazy_cleanup(env, tmp_path, monkeypatch):
    """过期 → 惰性清理 + 明确 EXPIRED 状态（不静默复活）。"""
    from datetime import datetime, timedelta, timezone

    state = await env.create_batch(SESS_A, "user:a", [("x.geojson", GEOJSON_ONE)])
    from app.services.data_import import staging as staging_mod

    loaded = staging_mod.load_state(state.batch_id)
    loaded.expires_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    staging_mod.save_state(loaded)
    with pytest.raises(DataImportError) as ei:
        await env.get_batch(state.batch_id, SESS_A)
    assert ei.value.code == "EXPIRED"
    # 目录已被惰性清理
    assert not staging_mod.batch_root(state.batch_id).exists()
