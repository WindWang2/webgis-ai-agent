"""#2044: upload attach stays inside the caller's session/org, and GeoAI roots
are the upload directory under DATA_DIR/uploads."""
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import Conversation, Organization, User
from app.models.upload import UploadRecord
from app.schemas.project_schema import DatasetAttach
from app.services.project_service import ProjectService


def _db():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    import app.models  # noqa: F401
    import app.models.upload  # noqa: F401

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    db.add_all([
        Organization(id=1, name="A", slug="org-a"),
        Organization(id=2, name="B", slug="org-b"),
    ])
    db.flush()
    for uid, org, role in (
        ("owner-a", 1, "editor"),
        ("mate-a", 1, "editor"),
        ("owner-b", 2, "editor"),
    ):
        db.add(User(
            id=uid, username=uid, email=f"{uid}@e.test", org_id=org,
            role=role, is_active=True,
        ))
    db.flush()
    db.add(Conversation(id="sess-b", user_id="owner-b"))
    db.add(Conversation(id="sess-a", user_id="owner-a"))
    db.flush()
    foreign = UploadRecord(
        filename="data/uploads/foreign-uuid/original.geojson",
        original_name="foreign.geojson", file_type="vector", format="geojson",
        file_size=4, session_id="sess-b",
    )
    own = UploadRecord(
        filename="data/uploads/own-uuid/original.geojson",
        original_name="own.geojson", file_type="vector", format="geojson",
        file_size=4, session_id="sess-a",
    )
    db.add_all([foreign, own])
    db.commit()
    proj_a = ProjectService.create_project(db, name="a", owner_id="owner-a", org_id=1)
    proj_mate = ProjectService.create_project(db, name="mate", owner_id="mate-a", org_id=1)
    return foreign, own, proj_a, proj_mate


def test_cross_tenant_upload_attach_is_denied():
    db = _db()
    foreign, _own, proj_a, _mate = _seed(db)
    attached = ProjectService.attach_dataset(
        db, proj_a.id,
        DatasetAttach(name="stolen", source_type="upload", source_ref=str(foreign.id)),
        user_id="owner-a", org_id=1,
    )
    assert attached is None


def test_session_owner_and_same_org_can_attach_upload():
    db = _db()
    foreign, own, proj_a, proj_mate = _seed(db)
    owned = ProjectService.attach_dataset(
        db, proj_a.id,
        DatasetAttach(name="mine", source_type="upload", source_ref=str(own.id)),
        user_id="owner-a", org_id=1,
    )
    assert owned is not None
    assert owned.source_ref == str(own.id)
    shared = ProjectService.attach_dataset(
        db, proj_mate.id,
        DatasetAttach(name="org", source_type="upload", source_ref=str(own.id)),
        user_id="mate-a", org_id=1,
    )
    assert shared is not None
    # Non-integer refs are not UploadRecord ids and stay attachable.
    legacy = ProjectService.attach_dataset(
        db, proj_a.id,
        DatasetAttach(name="legacy", source_type="upload", source_ref="up_1"),
        user_id="owner-a", org_id=1,
    )
    assert legacy is not None


def test_upload_root_is_parent_directory_under_uploads(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    folder = data / "uploads" / "uuid-1"
    folder.mkdir(parents=True)
    stored = "data/uploads/uuid-1/original.geojson"
    (folder / "original.geojson").write_text("{}")
    from app.api.routes.geoai import _upload_dir_under_data_root

    assert _upload_dir_under_data_root(Path("data"), stored) == folder.resolve()
    assert _upload_dir_under_data_root(data, str(folder / "original.geojson")) == folder.resolve()
    assert _upload_dir_under_data_root(data, "/etc/passwd") is None
    loose = data / "uploads" / "loose.geojson"
    loose.write_text("{}")
    assert _upload_dir_under_data_root(data, str(loose)) is None
