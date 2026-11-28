"""R1 修复回归锁定——P0 路径包容 / P1-2 回滚补偿 / P1-4 回执契约。"""
import io
import json
import zipfile
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.database import Base, Engine, SessionLocal
from app.models.db_model import User
from app.models.project import Project
from app.services.data_delivery.source_resolver import DeliveryActor
from app.services.project_exchange import contracts as pc
from app.services.project_exchange.importer import (
    _register_all, get_import_receipt, save_package_upload,
)
from app.services.project_exchange.untrusted_zip import extract_zip, inspect_zip

_OWNER = "ex-r1fix-owner"


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
            db.add(User(id=_OWNER, username="r1", email="r1@e.com",
                        password_hash="x", role="editor", is_active=True,
                        token_version=0))
            db.commit()
    yield


def _manifest_with(dataset_payload_path: str) -> dict:
    import hashlib

    stub = b"PK\x03\x04stub"
    digest = hashlib.sha256(stub).hexdigest()
    return {
        "format": pc.PACKAGE_FORMAT, "format_version": 1,
        "package_id": f"pkg-{'7' * 32}", "created_at": "x",
        "origin": {"project_id": "proj-x", "project_name": "x"},
        "project": {"name": "r1"},
        "selection": {"kind": "project"},
        "reproduction": {"level": "view_only"},
        "datasets": [{
            "logical_id": "ds-evil", "name": "evil",
            "classification": "embedded", "crs": "EPSG:4326",
            "payload": {"path": dataset_payload_path, "sha256": digest,
                        "bytes": len(stub)},
        }],
        "workflows": [], "artifacts": [], "maps": [],
        "files": [{"path": dataset_payload_path, "sha256": digest,
                   "bytes": len(stub), "mime": "application/json"}],
        "disclosure": {"not_included": [], "external_links": [],
                       "unsupported": []},
        "fidelity_receipt": {"datasets": [], "writer_versions": {}},
    }


def _zip_with(manifest: dict, entries: dict) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(pc.MANIFEST_PATH, json.dumps(manifest, ensure_ascii=False))
        for name, data in entries.items():
            zf.writestr(name, data)
    p = Path(str(settings.DATA_DIR)) / "r1.zip"
    p.write_bytes(buf.getvalue())
    return p


class TestP0PathContainment:
    @pytest.mark.parametrize("evil_path", [
        "/etc/passwd",
        "../../../../etc/passwd",
        "standard/../../.env",
        "standard/tables/a\x00b.gpkg",
    ])
    def test_manifest_payload_path_traversal_rejected(self, tmp_path,
                                                      evil_path):
        # R1 P0：前置闸在解包后、任何 DB 写入前全量校验 manifest 引用路径
        from app.services.project_exchange.importer import (
            _validate_manifest_paths,
        )

        manifest = _manifest_with(evil_path)
        staging = tmp_path / "stg"
        staging.mkdir()
        (staging / "standard").mkdir(exist_ok=True)
        (staging / "standard" / "data.gpkg").write_bytes(b"PK\x03\x04stub")
        with pytest.raises(pc.PackageError) as ei:
            _validate_manifest_paths(pc.format_gate(manifest), staging)
        assert ei.value.error_code == pc.ERR_PATH_UNSAFE

    def test_missing_referenced_file_rejected_before_db_write(self, tmp_path):
        from app.services.project_exchange.importer import (
            _validate_manifest_paths,
        )

        manifest = _manifest_with("standard/absent.gpkg")
        staging = tmp_path / "stg"
        staging.mkdir()
        with pytest.raises(pc.PackageError) as ei:
            _validate_manifest_paths(pc.format_gate(manifest), staging)
        assert ei.value.error_code == pc.ERR_IMPORT_SOURCE_INVALID

    def test_dataset_payload_hash_enforced(self, tmp_path):
        """载荷级摘要不符 = typed 拒绝（files 台账正确、payload 声明不符
        —— 隔离 payload 级校验面；不静默顶替）。"""
        import hashlib

        manifest = _manifest_with("standard/data.gpkg")
        real = hashlib.sha256(b"PK\x03\x04stub").hexdigest()
        # files 台账正确（解包能过）；dataset payload 声明伪造（payload 级拦截）
        manifest["datasets"][0]["payload"]["sha256"] = "0" * 64
        manifest["files"] = [{"path": "standard/data.gpkg",
                              "sha256": real, "bytes": 12,
                              "mime": "application/json"}]
        p = _zip_with(manifest, {"standard/data.gpkg": b"PK\x03\x04stub"})
        inspection = inspect_zip(p)
        staging = tmp_path / "stg"
        staging.mkdir()
        extract_zip(p, inspection, staging,
                    expected_hashes={"standard/data.gpkg": real})
        from app.services.project_exchange.importer import (
            ImportMapping, _compensate_project,
        )

        from app.core.database import SessionLocal

        mapping = ImportMapping()
        with SessionLocal() as db:
            with pytest.raises(pc.PackageError) as ei:
                _register_all(db, pc.format_gate(manifest), staging,
                              DeliveryActor(user_id=_OWNER), "r1", None,
                              [], lambda: None, mapping)
            db.rollback()
        assert ei.value.error_code == pc.ERR_HASH_MISMATCH
        # P1-2 契约：mapping 已增量填充 → 补偿可用 → 无项目残留
        assert mapping.project_id
        _compensate_project(mapping, [])
        with SessionLocal() as db:
            assert db.query(Project).filter(
                Project.name == "r1").count() == 0


class TestP1_2RollbackNotDead:
    def test_mapping_filled_incrementally(self, tmp_path):
        """成功注册项目后，mapping 立即可见 project_id（补偿路径可用）。"""
        manifest = _manifest_with("standard/data.gpkg")
        p = _zip_with(manifest, {"standard/data.gpkg": b"PK\x03\x04stub"})
        inspection = inspect_zip(p)
        staging = tmp_path / "stg"
        staging.mkdir()
        extract_zip(p, inspection, staging,
                    expected_hashes={"standard/data.gpkg":
                                     manifest["files"][0]["sha256"]})
        from app.services.project_exchange.importer import ImportMapping

        mapping = ImportMapping()
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            _register_all(db, pc.format_gate(manifest), staging,
                          DeliveryActor(user_id=_OWNER), "r1", None,
                          [], lambda: None, mapping)
            db.commit()
        assert mapping.project_id, "mapping 必须边注册边填充（P1-2）"
        assert mapping.project_id.startswith("proj_")
        # 清理测试项目
        with SessionLocal() as db:
            row = db.get(Project, mapping.project_id)
            if row is not None:
                db.delete(row)
                db.commit()


class TestP1_4ReceiptSchema:
    def test_import_receipt_accepts_job_id_field(self, tmp_path):
        """回执含 job_id 不再触发 extra=forbid 500（schema 显式声明）。"""
        import_id = "imp-" + "a" * 16
        out = Path(str(settings.DATA_DIR)) / "project-packages" / "imports" \
            / import_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "receipt.json").write_text(json.dumps({
            "import_id": import_id, "job_id": "123",
            "package_id": f"pkg-{'8' * 32}", "project_id": "proj-x",
            "target_name": "t", "mapping": {},
            "session_writebacks": [], "skipped": [],
            "verification": {}, "created_at": "x",
        }, ensure_ascii=False))
        receipt = get_import_receipt(import_id)
        assert receipt["job_id"] == "123"

    def test_undeclared_entries_field_documented(self):
        """P2-7 的回执披露字段在 schema 类型中（前端可见）。"""
        from app.schemas.project_exchange_schema import (
            ImportStatusResponse,
        )

        fields = set(ImportStatusResponse.model_fields.keys())
        assert "job_id" in fields


class TestP2_12OwnerOnly:
    def test_same_org_other_user_rejected(self):
        from app.services.project_exchange.importer import (
            resolve_package_upload,
        )

        actor_a = DeliveryActor(user_id="r1-owner-a")
        actor_b = DeliveryActor(user_id="r1-owner-b", org_id=42)
        src = Path(str(settings.DATA_DIR)) / "oo.zip"
        src.write_bytes(_zip_with(_manifest_with("standard/data.gpkg"),
                                  {"standard/data.gpkg":
                                   b"PK\x03\x04stub"}).read_bytes())
        meta = save_package_upload(actor_a, "p.zip", src, None)
        with pytest.raises(pc.PackageError) as ei:
            resolve_package_upload(meta["upload_id"], actor_b)
        assert ei.value.error_code == pc.ERR_UNAUTHORIZED
