"""闭包读取与检视测试（T2/T3）——真实 DB 行 + 范围收敛 + 逻辑 ID 确定性。"""
from datetime import datetime, timezone

import pytest

from app.core.database import Base, Engine, SessionLocal
from app.models.project import (
    MapProductVersion, Project, ProjectDataset, Workflow,
)
from app.services.project_exchange import contracts as pc
from app.services.project_exchange.closure import read_project_closure
from app.services.project_exchange.inspector import (
    assign_logical_ids, inspect_project, plan_project,
)

_PID = "proj-ex-closure-01"


@pytest.fixture(autouse=True)
def db():
    Base.metadata.create_all(bind=Engine)
    with SessionLocal() as session:
        yield session
    with SessionLocal() as session:
        project = session.get(Project, _PID)
        if project is not None:
            session.delete(project)
            session.commit()


def _mk_project(db):
    db.add(Project(id=_PID, name="交换测试项目", description="d",
                   status="active"))
    db.commit()


class TestClosureClassification:
    def test_upload_embedded_and_external_and_detached(self, db, tmp_path,
                                                       monkeypatch):
        from app.services.data_parser import get_upload_dir

        # upload 数据集：真实落盘文件 → embedded
        upload_root = get_upload_dir(str(tmp_path), "77")
        (upload_root / "original.geojson").write_text(
            '{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        monkeypatch.setattr(
            "app.services.project_exchange.closure._data_dir",
            lambda: str(tmp_path))
        _mk_project(db)
        db.add(ProjectDataset(
            id="ds_up1", project_id=_PID, name="upload-ds",
            source_type="upload", source_ref="77", crs="EPSG:4326",
            version_fingerprint="a" * 64))
        db.add(ProjectDataset(
            id="ds_ex1", project_id=_PID, name="ext-ds",
            source_type="external", source_ref="https://example.com/x.geojson",
            crs="EPSG:4326"))
        db.add(ProjectDataset(
            id="ds_ly1", project_id=_PID, name="layer-ds",
            source_type="layer", source_ref="layer-9", crs="EPSG:4326"))
        db.add(ProjectDataset(
            id="ds_dt1", project_id=_PID, name="detached-ds",
            source_type="upload", source_ref="999", crs="EPSG:4326",
            detached_at=datetime.now(timezone.utc)))
        db.add(ProjectDataset(
            id="ds_sr1", project_id=_PID, name="session-ds",
            source_type="vector", source_ref="ref:geojson-abc123",
            crs="EPSG:4326"))
        db.commit()

        closure = read_project_closure(db, _PID)
        assert closure.datasets["ds_up1"].classification == pc.CLASS_EMBEDDED
        assert closure.datasets["ds_ex1"].classification == \
            pc.CLASS_EXTERNAL_REFERENCE
        assert closure.datasets["ds_ly1"].classification == \
            pc.CLASS_UNSUPPORTED
        assert closure.datasets["ds_dt1"].classification == pc.CLASS_MISSING
        assert closure.datasets["ds_sr1"].classification == "session_ref"
        counts = closure.classification_counts()
        assert counts[pc.CLASS_EMBEDDED] == 1
        assert counts[pc.CLASS_MISSING] == 1
        assert len(closure.disclosure) >= 2

    def test_scope_map_narrows_datasets(self, db, tmp_path, monkeypatch):
        from app.services.data_parser import get_upload_dir

        upload_root = get_upload_dir(str(tmp_path), "88")
        (upload_root / "original.geojson").write_text(
            '{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        monkeypatch.setattr(
            "app.services.project_exchange.closure._data_dir",
            lambda: str(tmp_path))
        _mk_project(db)
        db.add(ProjectDataset(
            id="ds_up1", project_id=_PID, name="upload-ds",
            source_type="upload", source_ref="88", crs="EPSG:4326"))
        db.add(ProjectDataset(
            id="ds_other", project_id=_PID, name="unrelated",
            source_type="external", source_ref="https://example.com/y",
            crs="EPSG:4326"))
        db.add(Workflow(id="wf_1", project_id=_PID, name="w", version=1,
                        graph_spec={"steps": [{"step_id": "s1",
                                               "tool": "noop"}]}))
        db.add(MapProductVersion(
            id="mpv_1", project_id=_PID, version_no=1,
            mapspec_snapshot={"version": 1, "sources": {}},
            label="main-map"))
        db.commit()

        closure = read_project_closure(
            db, _PID,
            selection={"kind": pc.SCOPE_MAP, "map_product_ids": ["mpv_1"]})
        assert len(closure.maps) == 1
        assert "ds_other" not in closure.datasets


class TestWorkflowRunnable:
    def test_runnable_requires_embedded_inputs(self, db, tmp_path,
                                               monkeypatch):
        from app.services.data_parser import get_upload_dir

        get_upload_dir(str(tmp_path), "91")
        (get_upload_dir(str(tmp_path), "91") / "original.geojson").write_text(
            '{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        monkeypatch.setattr(
            "app.services.project_exchange.closure._data_dir",
            lambda: str(tmp_path))
        _mk_project(db)
        db.add(ProjectDataset(
            id="ds_ok", project_id=_PID, name="ok-ds",
            source_type="upload", source_ref="91", crs="EPSG:4326"))
        db.add(ProjectDataset(
            id="ds_ref", project_id=_PID, name="ref-ds",
            source_type="vector", source_ref="ref:geojson-dead",
            crs="EPSG:4326"))
        db.add(Workflow(
            id="wf_good", project_id=_PID, name="good", version=1,
            graph_spec={
                "steps": [{"step_id": "s1", "tool": "buffer",
                           "input_bindings": {"ref": {"dataset": "ds_ok"}}}],
            }))
        db.add(Workflow(
            id="wf_bad", project_id=_PID, name="bad", version=1,
            graph_spec={
                "steps": [{"step_id": "s1", "tool": "buffer",
                           "input_bindings": {"ref": {"dataset": "ds_ref"}}}],
            }))
        db.commit()

        closure = read_project_closure(db, _PID)
        assert closure._workflow_runnable(closure.workflows["wf_good"])
        assert not closure._workflow_runnable(closure.workflows["wf_bad"])
        assert closure.repro_level() == pc.REPRO_PARTIAL


class TestInspector:
    def test_logical_ids_deterministic_and_unique(self, db):
        _mk_project(db)
        for i in range(3):
            db.add(ProjectDataset(
                id=f"ds_dup{i}", project_id=_PID, name="同名数据集",
                source_type="external", source_ref=f"https://e.com/{i}",
                crs="EPSG:4326"))
        db.commit()
        closure = read_project_closure(db, _PID)
        logical1 = assign_logical_ids(closure)
        logical2 = assign_logical_ids(read_project_closure(db, _PID))
        ids = list(logical1["datasets"].values())
        assert ids == list(logical2["datasets"].values())
        assert len(set(ids)) == len(ids)
        assert pc.valid_logical_id(ids[0])
        assert pc.valid_logical_id(ids[-1])

    def test_inspect_and_plan_shapes(self, db, tmp_path, monkeypatch):
        from app.services.data_parser import get_upload_dir

        upload_root = get_upload_dir(str(tmp_path), "95")
        (upload_root / "original.geojson").write_text(
            '{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        monkeypatch.setattr(
            "app.services.project_exchange.closure._data_dir",
            lambda: str(tmp_path))
        _mk_project(db)
        db.add(ProjectDataset(
            id="ds_up1", project_id=_PID, name="upload-ds",
            source_type="upload", source_ref="95", crs="EPSG:4326"))
        db.commit()
        info = inspect_project(db, _PID)
        assert info["totals"]["datasets"] == 1
        assert info["exportable"] is True
        assert info["classification_counts"][pc.CLASS_EMBEDDED] == 1
        plan = plan_project(db, _PID)
        assert plan["datasets"][0]["logical_id"].startswith("ds-")
        assert plan["datasets"][0]["payload"]["path"] == pc.LAYERS_GPKG_PATH
        assert plan["standard_files"]["layers_gpkg"] == pc.LAYERS_GPKG_PATH
        assert plan["limits"]["max_package_bytes"] > 0
