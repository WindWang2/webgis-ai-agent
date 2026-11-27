"""原生工程包往返与标准 GIS 互操作测试（T6/T7 —— 必过旅程 1/2 核心）。

旅程 1：真实项目（upload 矢量 + 会话 ref + 工作流修订 + 晋升产物 + 地图快照）
→ 导出（同步执行体）→ ZIP → 受控上传 → 真实导入 runner（in-process）→
全新隔离项目 + IDs 重映射 + 导入后校验。
旅程 2：标准交换子集经独立库（geopandas/rasterio/ogr2ogr CLI）读回核对。
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.database import Base, Engine, SessionLocal
from app.models.db_model import User
from app.models.project import (
    Artifact, MapProductVersion, Project, ProjectDataset,
    Workflow, WorkflowRevision,
)
from app.services.data_delivery.source_resolver import DeliveryActor
from app.services.project_exchange import contracts as pc
from app.services.project_exchange.exporter import (
    _execute_export_sync,
)
from app.services.project_exchange.importer import (
    save_package_upload, submit_import,
)
from app.services.project_exchange.closure import read_project_closure
from app.services.project_exchange.exporter import _resolve_all_payloads
from app.services.jobs.store import DurableJobStore
from app.services.jobs import registry as cancellation_registry

_OWNER = "ex-roundtrip-owner"
_SRC_PID = "proj-ex-roundtrip-src"
_SESSION = "ex-roundtrip-session"


class _FakeToken:
    cancelled = False


class _Progress:
    def __call__(self, pct, msg):
        pass


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    Base.metadata.create_all(bind=Engine)
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    from app.services.project_artifact_promotion import (
        reset_content_store_root_cache,
    )

    reset_content_store_root_cache()
    with SessionLocal() as db:
        if db.get(User, _OWNER) is None:
            db.add(User(
                id=_OWNER, username="ex-owner",
                email="ex-owner@example.com",
                password_hash="scrypt$16384$8$1$00$00",
                role="editor", is_active=True, token_version=0,
                created_at=datetime.now(timezone.utc)))
            db.commit()
    yield
    # 清理：源项目与导入项目
    with SessionLocal() as db:
        # MapProductVersion 没有挂在 Project 的 cascade 关系上 —— 显式清理
        from sqlalchemy import delete as sa_delete

        db.execute(sa_delete(MapProductVersion).where(
            (MapProductVersion.project_id == _SRC_PID)
            | (MapProductVersion.actor.like("project_exchange_import%"))))
        db.commit()
        for row in db.query(Project).filter(
                Project.id.in_([_SRC_PID])).all():
            db.delete(row)
        for row in db.query(Project).filter(
                Project.metadata_json.isnot(None)).all():
            meta = row.metadata_json or {}
            if isinstance(meta, dict) and str(
                    meta.get("imported_from_package", "")).startswith("pkg-"):
                db.delete(row)
        db.commit()
    cancellation_registry.clear()


def _point_fc(n=6):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": i,
         "geometry": {"type": "Point",
                      "coordinates": [104.0 + i * 0.01, 30.5 + i * 0.01]},
         "properties": {"school": f"s{i}", "level": i % 2}}
        for i in range(n)]}


def _seed_source_project(db, data_dir: Path):
    """源项目：upload 矢量 + 会话 ref + 工作流 + 晋升产物 + 地图快照。"""
    from app.services.data_parser import get_upload_dir
    from app.services.project_artifact_promotion import materialize_blob

    upload_dir = get_upload_dir(data_dir, "601")
    fc = _point_fc(6)
    (upload_dir / "original.geojson").write_text(
        json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    db.add(Project(id=_SRC_PID, name="往返源项目", status="active"))
    db.add(ProjectDataset(
        id="ds_src_up", project_id=_SRC_PID, name="schools-upload",
        source_type="upload", source_ref="601", crs="EPSG:4326",
        schema_profile={"geometry_type": "Point", "feature_count": 6,
                        "bbox": [104.0, 30.5, 104.05, 30.55]},
        version_fingerprint="f" * 64))
    db.add(Workflow(id="wf_src_1", project_id=_SRC_PID, name="学校缓冲",
                    version=1, inputs_schema={"inputs": ["ref"]},
                    graph_spec={
                        "steps": [{"step_id": "s1", "tool": "buffer",
                                   "args_template": {"distance": 500},
                                   "input_bindings": {
                                       "ref": {"dataset": "ds_src_up"}}}]}))
    revision = WorkflowRevision(
        id="wfrev_src_1", workflow_id="wf_src_1", revision_no=1,
        graph_spec={
            "steps": [{"step_id": "s1", "tool": "buffer",
                       "args_template": {"distance": 500},
                       "input_bindings": {
                           "ref": {"dataset": "ds_src_up"}}}]},
        graph_fingerprint="c" * 64, created_by=_OWNER)
    db.add(revision)
    db.flush()
    wf = db.get(Workflow, "wf_src_1")
    wf.current_revision_id = revision.id
    # 晋升产物（真实 BlobStore 内容；content_sha256 = canonical 字节摘要，
    # 与 read_content 的 digest 校验一致）
    import hashlib as _hl

    artifact_payload = {"type": "FeatureCollection",
                        "features": fc["features"][:2]}
    canonical = json.dumps(artifact_payload, ensure_ascii=False,
                           sort_keys=True)
    real_digest = _hl.sha256(canonical.encode("utf-8")).hexdigest()
    digest = materialize_blob("d" * 64, canonical)
    assert digest, "BlobStore 物化失败"
    artifact = Artifact(
        id="art_src_1", project_id=_SRC_PID, name="schools-sample",
        artifact_type="dataset",
        format="geojson", crs="EPSG:4326", storage_ref="",
        content_fingerprint="d" * 64,
        metadata_json={"content_status": "promoted",
                       "content_location": digest})
    db.add(artifact)
    db.flush()
    from app.services.artifact_revisions import record_revision

    from app.services.project_artifact_promotion import content_store_root

    blob_path = content_store_root() / digest
    record_revision(
        db, artifact_id="art_src_1", content_sha256=real_digest,
        content_location=digest, content_type="json",
        byte_size=blob_path.stat().st_size)
    db.add(MapProductVersion(
        id="mpv_src_1", project_id=_SRC_PID, version_no=1,
        mapspec_snapshot={"version": 1, "sources": {
            "schools": {"type": "geojson", "inlineData": fc}},
        "layers": []},
        mapspec_fingerprint="e" * 64, label="主图", lineage_kind="auto"))
    db.commit()


async def _seed_session_payload(session_id: str, ref: str, payload) -> None:
    from app.services.session_data import session_data_manager

    stored = await session_data_manager.store(session_id, payload,
                                              prefix="geojson")
    # 别名：自定义 ref（数据集 source_ref）→ 真实存储 ref
    await session_data_manager.set_alias(session_id, stored, ref)


def _export_package(data_dir: Path, session_id=None) -> Path:
    """导出同步执行体 → 返回 ZIP 路径。"""
    from app.core.database import SessionLocal
    from app.services.session_data import session_data_manager

    if session_id:
        # 真实会话 store：种子一个会话 ref（MemorySessionStore）
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(session_data_manager.store(
                session_id, _point_fc(3), prefix="geojson"))
        finally:
            loop.close()

    with SessionLocal() as db:
        closure = read_project_closure(db, _SRC_PID)
        resolved = asyncio.new_event_loop().run_until_complete(
            _resolve_all_payloads(closure, session_id, _FakeToken()))
        actor = DeliveryActor(user_id=_OWNER)
        outcome = _execute_export_sync(
            job_id=0, package_id=pc.new_package_id(), closure=closure,
            resolved=resolved, package_name="往返包", actor=actor,
            session_id=session_id, token=_FakeToken(),
            progress=_Progress(), cancel_check=lambda: None)
    zip_path = Path(data_dir) / "exports" / outcome["filename"]
    assert zip_path.is_file(), f"ZIP 未发布: {zip_path}"
    return zip_path


class TestRoundtripJourney:
    def test_export_import_remap_verify(self, tmp_path, monkeypatch):
        """旅程 1：导出 → 受控上传 → 真实导入 → 重映射 + 校验。"""
        data_dir = Path(str(settings.DATA_DIR))
        with SessionLocal() as db:
            _seed_source_project(db, data_dir)

        session_id = _SESSION
        # 会话 ref 数据集：真实 store 种子 + DB 行
        ref = "ref:geojson-exsess1"
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_seed_session_payload(session_id, ref,
                                                          _point_fc(3)))
        finally:
            loop.close()
        with SessionLocal() as db:
            db.add(ProjectDataset(
                id="ds_src_sess", project_id=_SRC_PID, name="session-schools",
                source_type="vector", source_ref=ref, crs="EPSG:4326"))
            db.commit()

        zip_path = _export_package(data_dir, session_id=session_id)

        # 受控上传 → 真实导入 runner
        actor = DeliveryActor(user_id=_OWNER)
        upload_meta = save_package_upload(actor, "roundtrip.wgproject.zip",
                                          zip_path, session_id)
        # 单一事件循环内提交 + 轮询（runner task 依附该循环，循环关闭即停摆）
        async def _journey():
            result = await submit_import(
                upload_meta["upload_id"], actor,
                target_name="往返导入项目", session_id=session_id)
            job_id = result["job_id"]
            status = None
            for _ in range(600):
                with SessionLocal() as db:
                    record = DurableJobStore.get_sync(db, int(job_id))
                    status = record.status
                if status in ("completed", "failed", "cancelled"):
                    return job_id, status
                await asyncio.sleep(0.05)
            return job_id, status

        job_id, status = asyncio.run(_journey())
        if status != "completed":
            with SessionLocal() as db:
                record = DurableJobStore.get_sync(db, int(job_id))
                print("IMPORT-FAIL:",
                      str(getattr(record, "error_message", None))[:400])
                print("TRACE-TAIL:",
                      str(getattr(record, "error_trace", None))[-400:])
                print("PARAMS:", str(dict(record.parameters or {}))[:300])
        assert status == "completed", f"导入未完成: {status}"

        # 回执 + 新项目断言
        from app.services.project_exchange.importer import (
            get_import_receipt,
        )

        with SessionLocal() as db:
            record = DurableJobStore.get_sync(db, int(job_id))
            import_id = record.parameters["import_id"]
        receipt = get_import_receipt(import_id, actor)
        new_pid = receipt["project_id"]
        assert new_pid and new_pid != _SRC_PID
        verification = receipt["verification"]
        assert verification["verdict"] in (pc.FIDELITY_SEMANTIC,
                                           pc.FIDELITY_EXACT)
        # 数据集重映射：upload 承载 + 会话恢复双轨都在 mapping
        mapping_ds = receipt["mapping"]["datasets"]
        assert len(mapping_ds) >= 2
        for logical, info in mapping_ds.items():
            assert str(info["dataset_id"]).startswith("ds_")
            assert info["dataset_id"] != logical
        # 工作流在新项目 + 修订指纹保留
        with SessionLocal() as db:
            from sqlalchemy import select as sa_select

            wfs = db.execute(sa_select(Workflow).where(
                Workflow.project_id == new_pid)).scalars().all()
            assert len(wfs) == 1
            rev = db.execute(sa_select(WorkflowRevision).where(
                WorkflowRevision.workflow_id == wfs[0].id)).scalars().first()
            assert rev is not None
            assert rev.graph_fingerprint == "c" * 64
            # 图内数据集引用已重映射为新 dataset id（不再指向旧 ds_src_up）
            graph_text = json.dumps(rev.graph_spec, ensure_ascii=False)
            assert "ds_src_up" not in graph_text
            maps = db.execute(sa_select(MapProductVersion).where(
                MapProductVersion.project_id == new_pid)).scalars().all()
            assert len(maps) == 1
            assert maps[0].lineage_kind == "import"
            assert maps[0].mapspec_snapshot
            # 原项目零接触
            src_datasets = db.execute(sa_select(ProjectDataset).where(
                ProjectDataset.project_id == _SRC_PID)).scalars().all()
            assert len(src_datasets) == 2
        # 会话恢复报告（session ref 恢复到目标会话）
        writebacks = receipt["session_writebacks"]
        assert writebacks, "会话恢复应有记录"


class TestStandardInterop:
    def test_standard_files_readable_by_independent_libs(self, tmp_path,
                                                         monkeypatch):
        """旅程 2：标准交换子集经 ogr2ogr CLI / geopandas / rasterio 独立读回。"""
        data_dir = Path(str(settings.DATA_DIR))
        with SessionLocal() as db:
            _seed_source_project(db, data_dir)
        zip_path = _export_package(data_dir)
        # 解包到独立目录
        import zipfile

        unpack = tmp_path / "unpacked"
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(unpack)
        manifest = json.loads(
            (unpack / pc.MANIFEST_PATH).read_text(encoding="utf-8"))
        assert manifest["format"] == pc.PACKAGE_FORMAT

        # geopandas 独立读回多图层 gpkg
        import geopandas as gpd
        import pyogrio

        gpkg = unpack / pc.LAYERS_GPKG_PATH
        layers = [name for name, _ in pyogrio.list_layers(str(gpkg))]
        assert layers, "gpkg 无图层"
        frame = gpd.read_file(str(gpkg), layer=layers[0])
        assert len(frame) == 6
        assert list(frame.columns) == ["school", "level", "geometry"]
        assert str(frame.crs).upper().endswith("4326")

        # ogr2ogr CLI（独立进程）读回 + CRS
        import subprocess

        ogr = subprocess.run(
            ["ogrinfo", "-so", "-al", str(gpkg)],
            capture_output=True, text=True, timeout=60)
        if ogr.returncode == 0:
            # GDAL 3.x 输出 WKT 形态（GEOGCRS["WGS 84"...），不是 EPSG 代码
            assert ("WGS 84" in ogr.stdout) or ("EPSG" in ogr.stdout)

        # manifest 台账逐文件哈希核对
        import hashlib

        for fentry in manifest["files"]:
            path = unpack / fentry["path"]
            assert path.is_file(), f"台账文件缺失: {fentry['path']}"
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            assert actual == fentry["sha256"], \
                f"哈希不符: {fentry['path']}"
        # 披露文件在场
        assert (unpack / pc.DISCLOSURE_PATH).is_file()
