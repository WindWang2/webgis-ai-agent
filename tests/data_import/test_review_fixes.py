"""G17 评审修复回归（P1-1/P1-2/P2-1/P2-2/P2-3/P2-4/P2-8/P2-9 + 复审 P1 波次）。

每条对应独立评审 finding；修复前这些测试必须失败（防回归）。
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from .fixtures import make_csv_bytes, make_geojson_bytes, point_feature  # noqa: E402

from app.core.config import settings
from app.services.data_import import budgets, staging
from app.services.data_import.models import BatchStatus
from app.services.data_import.service import DataImportError, DataImportService
from app.services.session_data import session_data_manager

pytestmark = pytest.mark.heavy

SESSION = "sess-g17-fixes-0001"
GEOJSON_ONE = make_geojson_bytes([point_feature({"n": 1}, 116.3, 39.8)])


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    return DataImportService()


async def test_p1_1_output_crs_is_4326_after_transform(env, tmp_path, monkeypatch):
    """P1-1：重投影后摄入收到的 crs 必须是输出 CRS（EPSG:4326）。"""
    import geopandas as gpd
    from shapely.geometry import LineString

    gpkg = tmp_path / "l.gpkg"
    g = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[LineString([(500000, 4400000), (500500, 4400500)])],
        crs="EPSG:32650",
    )
    g.to_file(gpkg, layer="l", driver="GPKG", engine="pyogrio")
    state = await env.create_batch(SESSION, "u", [("l.gpkg", gpkg.read_bytes())])
    key = state.members[0].member_key
    await env.save_plan(state.batch_id, SESSION, {key: {}}, selected=[key])

    captured: dict = {}
    import app.services.data_import.commit as commit_mod

    orig = commit_mod.feature_collection

    def spy(result, *, to_wgs84):
        fc, transformed = orig(result, to_wgs84=to_wgs84)
        captured["transformed"] = transformed
        return fc, transformed

    monkeypatch.setattr(commit_mod, "feature_collection", spy)

    # 捕获 ingest 收到的 crs 参数（spy，不绕过管线）
    import app.services.data_ingest.pipeline as pipe_mod

    orig_ingest = pipe_mod.get_ingest_pipeline

    class _Pipe:
        async def ingest(self, sid, fc, **kw):
            captured["crs_arg"] = kw.get("crs")
            return await orig_ingest().ingest(sid, fc, **kw)

    monkeypatch.setattr(pipe_mod, "get_ingest_pipeline", lambda: _Pipe())

    out = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out["receipts"][key]
    assert r["status"] == "completed"
    assert captured["transformed"] is True
    assert captured["crs_arg"] == "EPSG:4326"        # 输出 CRS
    assert r["crs_evidence"]["crs"] == "EPSG:32650"  # 源 CRS 在收据证据中
    assert r["crs_evidence"]["transform_target"] == "EPSG:4326"


async def test_p1_2_table_over_commit_limit_fails(env, tmp_path, monkeypatch):
    """P1-2：表格超 COMMIT_ROW_LIMIT → ROW_BUDGET_EXCEEDED（不静默截断）。"""
    monkeypatch.setattr(budgets, "COMMIT_ROW_LIMIT", 10)
    csv = make_csv_bytes([[f"{116 + i * 0.001}", f"{39 + i * 0.001}"] for i in range(50)],
                         ["lng", "lat"])
    state = await env.create_batch(SESSION, "u", [("b.csv", csv)])
    key = state.members[0].member_key
    await env.save_plan(
        state.batch_id, SESSION,
        {key: {"mode": "points", "x_column": "lng", "y_column": "lat",
               "source_crs_declared": "EPSG:4326"}},
        selected=[key],
    )
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out["receipts"][key]
    assert r["status"] == "failed"
    assert r["error_code"] == "ROW_BUDGET_EXCEEDED"


async def test_p2_1_columns_truncation_disclosed(env, tmp_path):
    """P2-1：>64 列 → fields_total 披露，清单有界。"""
    header = [f"c{i:03d}" for i in range(70)]
    row = [f"v{i}" for i in range(70)]
    csv = make_csv_bytes([row], header)
    state = await env.create_batch(SESSION, "u", [("wide.csv", csv)])
    m = state.members[0]
    assert m.fields_total == 70
    assert len(m.fields) == budgets.MAX_COLUMNS_SHOWN
    pv = await env.preview_members(state.batch_id, SESSION, [m.member_key], row_limit=10)
    p = pv["previews"][0]
    assert p.get("columns_total") == 70


async def test_p2_2_xlsx_header_row_honored(env, tmp_path):
    """P2-2：XLSX header_row=1 → 表头取第二行（与 CSV 语义一致）。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "s"
    ws.append(["垃圾行1", "垃圾行2"])
    ws.append(["a", "b"])
    ws.append(["1", "2"])
    p = tmp_path / "h.xlsx"
    wb.save(p)
    state = await env.create_batch(SESSION, "u", [("h.xlsx", p.read_bytes())])
    key = state.members[0].member_key
    await env.save_plan(state.batch_id, SESSION, {key: {"mode": "table", "header_row": 1}},
                        selected=[key])
    pv = await env.preview_members(state.batch_id, SESSION, [key], row_limit=10)
    cols = pv["previews"][0]["columns"]
    assert cols == ["a", "b"], cols


async def test_p2_3_zipset_colon_keys_distinct(env, tmp_path):
    """P2-3：容器内目录/basename 含 ':' 时成员不串键。"""
    import geopandas as gpd
    from shapely.geometry import Point

    work = tmp_path / "z"
    work.mkdir()
    a = gpd.GeoDataFrame({"v": [1]}, geometry=[Point(0, 0)], crs="EPSG:4326")
    b = gpd.GeoDataFrame({"v": [2]}, geometry=[Point(1, 1)], crs="EPSG:4326")
    # Windows 文件名不能含 ':' —— 冒号只出现在 zip 条目名（合法）
    a.to_file(work / "src_a.shp", engine="pyogrio")
    b.to_file(work / "src_b.shp", engine="pyogrio")
    zp = tmp_path / "colon.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        # dir='x', base='y:z' 与 dir='x:y', base='z' 在旧格式下同键 —— 新格式必须区分
        for comp in (".shp", ".dbf", ".shx", ".prj", ".cpg"):
            zf.write(work / f"src_a{comp}", f"x/y:z{comp}")
        for comp in (".shp", ".dbf", ".shx", ".prj", ".cpg"):
            zf.write(work / f"src_b{comp}", f"x:y/z{comp}")
    # 追加：真实 sanitize 碰撞对（同目录 'a:b' 与 'a_b' → 无哈希时同名 stem/目录）
    a.to_file(work / "src_c.shp", engine="pyogrio")
    c = gpd.GeoDataFrame({"v": [3]}, geometry=[Point(2, 2)], crs="EPSG:4326")
    c.to_file(work / "src_c.shp", engine="pyogrio")
    d = gpd.GeoDataFrame({"v": [4]}, geometry=[Point(3, 3)], crs="EPSG:4326")
    d.to_file(work / "src_d.shp", engine="pyogrio")
    with zipfile.ZipFile(zp, "a") as zf:
        for comp in (".shp", ".dbf", ".shx", ".prj", ".cpg"):
            zf.write(work / f"src_c{comp}", f"x/a:b{comp}")
            zf.write(work / f"src_d{comp}", f"x/a_b{comp}")
    state = await env.create_batch(SESSION, "u", [("colon.zip", zp.read_bytes())])
    members = {m.member_key: m for m in state.members}
    assert len(members) == 4
    # 提取目录互不共享（防组件互相覆盖；无哈希时碰撞对会共享）
    dirs = [m.container_ref for m in state.members]
    assert len(set(dirs)) == 4
    await env.save_plan(state.batch_id, SESSION, {k: {} for k in members}, selected=list(members))
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    assert out["status"] == "completed"
    # 每个成员读到自己的 .shp（无哈希时碰撞对读到同一份 → v 值会重复/缺失）
    from app.services.session_data import session_data_manager

    seen = set()
    for k, r in out["receipts"].items():
        assert r["status"] == "completed"
        data = await session_data_manager.get(SESSION, r["output_ref"])
        seen.add(data["geojson"]["features"][0]["properties"]["v"])
    assert seen == {1, 2, 3, 4}


async def test_p2_4_diagnostics_readable(env, tmp_path):
    """P2-4：>100 拒绝行 → diag 产物可经 staging 读取。"""
    rows = [["abc", "39.8"] for _ in range(150)]  # x 列全部非法
    csv = make_csv_bytes(rows, ["lng", "lat"])
    state = await env.create_batch(SESSION, "u", [("bad.csv", csv)])
    key = state.members[0].member_key
    await env.save_plan(
        state.batch_id, SESSION,
        {key: {"mode": "points", "x_column": "lng", "y_column": "lat",
               "source_crs_declared": "EPSG:4326"}},
        selected=[key],
    )
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out["receipts"][key]
    assert r["rejected_rows"] == 150
    assert r["diagnostics_ref"] and r["diagnostics_ref"].endswith("-rejected.jsonl")
    from app.services.data_import import staging

    path = staging.diag_dir(state) / r["diagnostics_ref"]
    assert path.exists()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 150
    first = json.loads(lines[0])
    assert first["reason"] == "invalid_coordinate" and first["row"] == 1


async def test_p2_8_batch_expanded_budget(env, tmp_path, monkeypatch):
    """P2-8：批次级展开预算累计（阈值下调 + 真实 zip 验证拒绝与清理）。"""
    import geopandas as gpd
    from shapely.geometry import Point

    work = tmp_path / "z2"
    work.mkdir()
    g = gpd.GeoDataFrame({"v": [1]}, geometry=[Point(0, 0)], crs="EPSG:4326")
    g.to_file(work / "a.shp", engine="pyogrio")
    g.to_file(work / "b.shp", engine="pyogrio")
    # 两个独立 zip：每个展开 ~940B（低于 per-zip 50MB 检查与批阈值），
    # 但批次累计超过 MAX_BATCH_EXPANDED_BYTES → 只有批次级检查能拦下。
    zp1 = tmp_path / "one.zip"
    zp2 = tmp_path / "two2.zip"
    for zp, name in ((zp1, "a"), (zp2, "b")):
        with zipfile.ZipFile(zp, "w") as zf:
            for comp in (".shp", ".dbf", ".shx", ".prj", ".cpg"):
                zf.write(work / f"{name}{comp}", f"{name}{comp}")
    # 单套展开 ~471B < 700 < 双套 942B：只有批次级累计能拦下（per-zip 检查不触发）
    monkeypatch.setattr(budgets, "MAX_BATCH_EXPANDED_BYTES", 700)
    with pytest.raises(DataImportError) as ei:
        await env.create_batch(SESSION, "u", [("one.zip", zp1.read_bytes()), ("two2.zip", zp2.read_bytes())])
    assert ei.value.code == "ZIP_EXPANSION_TOO_LARGE"
    # 有界拒绝 → staging 清理（不留孤儿目录）
    left = list((tmp_path / "import_batches").iterdir()) if (tmp_path / "import_batches").exists() else []
    assert left == []


async def test_p2_9_cancel_intent_persisted(env, tmp_path):
    """P2-9：取消意图落盘；终态写服从（不因晚到结果翻回 completed）。"""
    csv = make_csv_bytes([[116.0 + i * 0.001, 39.0] for i in range(5000)], ["lng", "lat"])
    state = await env.create_batch(SESSION, "u", [("c.csv", csv)])
    key = state.members[0].member_key
    await env.save_plan(
        state.batch_id, SESSION,
        {key: {"mode": "points", "x_column": "lng", "y_column": "lat",
               "source_crs_declared": "EPSG:4326"}},
        selected=[key],
    )
    await env.start_commit(state.batch_id, SESSION, background=True)
    await env.cancel_batch(state.batch_id, SESSION)
    from app.services.data_import import staging

    st = staging.load_state(state.batch_id)
    assert st is not None and st.cancel_requested is True
    final = await env.get_batch(state.batch_id, SESSION)
    assert final.status.value in ("cancelled", "partial", "failed")


async def test_p1_commit_keeps_over_cap_columns(env, tmp_path):
    """P1（数据完整性）：>64 列 CSV 提交不得静默丢列（预览帽只适用预览）。"""
    n_cols = budgets.SAMPLE_COLUMNS_IN_PREVIEW + 6
    header = [f"c{i:03d}" for i in range(n_cols)]
    row = [f"v{i}" for i in range(n_cols)]
    csv = make_csv_bytes([row], header)
    state = await env.create_batch(SESSION, "u", [("wide.csv", csv)])
    m = state.members[0]
    assert m.fields_total == n_cols
    await env.save_plan(state.batch_id, SESSION, {m.member_key: {"mode": "table"}},
                        selected=[m.member_key])
    # 预览仍按预览帽有界（SAMPLE_COLUMNS_IN_PREVIEW，行为不变）
    pv = await env.preview_members(state.batch_id, SESSION, [m.member_key], row_limit=10)
    assert len(pv["previews"][0]["columns"]) == budgets.SAMPLE_COLUMNS_IN_PREVIEW
    # 提交产物必须含全部列（超帽列不得从 table 产物中静默消失）
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out["receipts"][m.member_key]
    assert r["status"] == "completed"
    d = await session_data_manager.get(SESSION, r["output_ref"])
    cols = [c["name"] for c in d["table"]["columns"]]
    assert cols == header
    assert d["table"]["rows"][0][-1] == f"v{n_cols - 1}"


async def test_p1_delete_batch_rechecks_committing_inside_lock(env, tmp_path, monkeypatch):
    """P1（并发）：delete_batch 锁外快照陈旧时，锁内必须重读状态 ——
    在飞 durable job 的 staging 不得被 rmtree（该 run 会以 NOT_FOUND 中断）。"""
    state = await env.create_batch(SESSION, "u", [("x.geojson", GEOJSON_ONE)])
    # 另一副本的在飞提交：磁盘状态 COMMITTING + 活跃 job 证据
    committing = staging.load_state(state.batch_id)
    committing.status = BatchStatus.COMMITTING
    committing.active_job_id = 424242
    staging.save_state(committing)
    monkeypatch.setattr(
        env, "_active_commit_job",
        lambda st: {"job_id": int(st.active_job_id or 424242), "status": "running"},
    )
    # 竞态窗口：锁外快照读于状态推进之前（陈旧：仍为 configured）
    real_load = staging.load_state
    stale = staging.load_state(state.batch_id)
    stale.status = BatchStatus.CONFIGURED
    stale.active_job_id = None
    seen = {"n": 0}

    def fake_load(batch_id, **kw):
        seen["n"] += 1
        return stale if seen["n"] == 1 else real_load(batch_id, **kw)

    monkeypatch.setattr(staging, "load_state", fake_load)

    with pytest.raises(DataImportError) as ei:
        await env.delete_batch(state.batch_id, SESSION)
    assert ei.value.code == "COMMIT_IN_PROGRESS"
    assert staging.batch_root(state.batch_id).exists()


async def test_p1_session_cap_protects_committing_batch(env, tmp_path, monkeypatch):
    """P1（数据完整性）：会话批次帽不得淘汰 COMMITTING 批次 ——
    它可能正被另一副本的 durable job 提交（staging 被删 → 该 run NOT_FOUND）。"""
    monkeypatch.setattr(budgets, "MAX_BATCHES_PER_SESSION", 2)
    states = []
    for i, name in enumerate(("a", "b", "c")):
        st = staging.stage_files(SESSION, "u", [(f"{name}.geojson", GEOJSON_ONE)])
        # 显式 created_at 保证「最旧」判定确定（isoformat 秒级精度会打平）
        st.created_at = f"2026-01-0{i + 1}T00:00:00+00:00"
        staging.save_state(st)
        states.append(st)
    # 最旧批次置为在飞提交（跨副本证据）
    states[0].status = BatchStatus.COMMITTING
    states[0].active_job_id = 7
    staging.save_state(states[0])

    listed = await env.list_batches(SESSION)
    assert states[0].batch_id in {b["batch_id"] for b in listed}
    # 在飞批次 staging 必须保留；帽仍对空闲批次生效（淘汰最旧 1 个）
    surviving = [p.name for p in staging.batches_root().iterdir() if p.is_dir()]
    assert states[0].batch_id in surviving
    assert len(surviving) == budgets.MAX_BATCHES_PER_SESSION


async def test_p2_commit_vector_read_is_bounded(env, tmp_path, monkeypatch):
    """P2（perf）：提交读矢量图层必须带 max_features 上界，不得整读后才判预算。"""
    import pyogrio

    geo = make_geojson_bytes(
        [point_feature({"n": i}, 116.3 + i * 0.001, 39.8) for i in range(3)]
    )
    state = await env.create_batch(SESSION, "u", [("bounded.geojson", geo)])
    key = state.members[0].member_key
    await env.save_plan(state.batch_id, SESSION, {key: {}}, selected=[key])

    captured: dict = {}
    orig = pyogrio.read_dataframe

    def spy(*args, **kwargs):
        if "bounded.geojson" in str(args[0]):
            captured.update(kwargs)
        return orig(*args, **kwargs)

    monkeypatch.setattr(pyogrio, "read_dataframe", spy)
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    assert out["receipts"][key]["status"] == "completed"
    assert captured.get("max_features") == budgets.COMMIT_ROW_LIMIT + 1


async def test_p2_gpkg_attribute_layer_typed_error(env, tmp_path):
    """P2（error-handling）：GPKG 无几何图层必须 typed 拒绝，不得裸 AttributeError。"""
    import pandas as pd
    import pyogrio

    gpkg = tmp_path / "attr.gpkg"
    pyogrio.write_dataframe(
        pd.DataFrame({"a": [1, 2]}), gpkg, layer="attr", driver="GPKG"
    )
    state = await env.create_batch(SESSION, "u", [("attr.gpkg", gpkg.read_bytes())])
    member = next(m for m in state.members if m.container_ref == "attr")
    await env.save_plan(
        state.batch_id, SESSION, {member.member_key: {}}, selected=[member.member_key]
    )
    pv = await env.preview_members(
        state.batch_id, SESSION, [member.member_key], row_limit=10
    )
    assert pv["previews"][0]["error"]["code"] == "VECTOR_GEOMETRY_MISSING"
    out = await env.start_commit(state.batch_id, SESSION, background=False)
    r = out["receipts"][member.member_key]
    assert r["status"] == "failed"
    assert r["error_code"] == "VECTOR_GEOMETRY_MISSING"
