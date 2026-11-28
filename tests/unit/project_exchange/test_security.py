"""安全矩阵测试（T8 —— 必过旅程 3）：篡改/越权/许可/取消无半成品。"""
import io
import json
import zipfile
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.database import Base, Engine
from app.services.data_delivery.source_resolver import DeliveryActor
from app.services.project_exchange import contracts as pc
from app.services.project_exchange.importer import (
    inspect_package, resolve_package_upload, save_package_upload,
)
from app.services.project_exchange.untrusted_zip import inspect_zip

_OWNER = "ex-sec-owner"
_OTHER = "ex-sec-other"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    Base.metadata.create_all(bind=Engine)
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    from app.services.project_artifact_promotion import (
        reset_content_store_root_cache,
    )

    reset_content_store_root_cache()
    yield


def _pkg(entries: dict, manifest_overrides: dict = None,
         member_hashes: dict = None) -> Path:
    entry = {
        "format": pc.PACKAGE_FORMAT, "format_version": 1,
        "package_id": f"pkg-{'e' * 32}", "created_at": "2026-11-28T00:00:00Z",
        "origin": {"project_id": "proj-x", "project_name": "x"},
        "project": {"name": "sec"},
        "selection": {"kind": "project"},
        "reproduction": {"level": "view_only"},
        "datasets": [{
            "logical_id": "ds-blocked", "name": "blocked-ds",
            "classification": "redacted",
            "crs": "EPSG:4326",
            "license": {"spdx": None, "note": "仅限内部使用",
                        "distributable": False},
        }],
        "workflows": [], "artifacts": [], "maps": [],
        "files": [
            {"path": p, "sha256": h, "bytes": 1, "mime": "application/json"}
            for p, h in (member_hashes or {}).items()
        ],
        "disclosure": {"not_included": [], "external_links": [],
                       "unsupported": []},
        "fidelity_receipt": {"datasets": [], "writer_versions": {}},
    }
    if manifest_overrides:
        entry.update(manifest_overrides)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(pc.MANIFEST_PATH, json.dumps(entry, ensure_ascii=False))
        for name, data in entries.items():
            zf.writestr(name, data)
    p = Path(str(settings.DATA_DIR)) / "sec.zip"
    p.write_bytes(buf.getvalue())
    return p


class TestTamperAndForgery:
    def test_manifest_hash_mismatch_rejected(self):
        # 台账声明 sha256=0*64，实际内容不同 → 解包阶段 typed 拒绝（fatal）
        from app.services.project_exchange.untrusted_zip import extract_zip

        data = json.dumps({"maps": True}).encode()
        p = _pkg({"maps/a.json": data},
                 member_hashes={"maps/a.json": "0" * 64})
        inspection = inspect_zip(p)
        staging = Path(str(settings.DATA_DIR)) / "stg"
        staging.mkdir(exist_ok=True)
        with pytest.raises(pc.PackageError) as ei:
            extract_zip(p, inspection, staging,
                        expected_hashes={"maps/a.json": "0" * 64})
        assert ei.value.error_code == pc.ERR_HASH_MISMATCH


class TestUploadOwnership:
    def test_cross_owner_upload_rejected(self):
        actor_owner = DeliveryActor(user_id=_OWNER)
        actor_other = DeliveryActor(user_id=_OTHER)
        src = Path(str(settings.DATA_DIR)) / "own.zip"
        src.write_bytes(_pkg({}).read_bytes())
        meta = save_package_upload(actor_owner, "p.wgproject.zip", src, None)
        with pytest.raises(pc.PackageError) as ei:
            resolve_package_upload(meta["upload_id"], actor_other)
        assert ei.value.error_code == pc.ERR_UNAUTHORIZED

    def test_same_owner_resolves(self):
        actor_owner = DeliveryActor(user_id=_OWNER)
        src = Path(str(settings.DATA_DIR)) / "own2.zip"
        src.write_bytes(_pkg({}).read_bytes())
        meta = save_package_upload(actor_owner, "p.wgproject.zip", src, None)
        path, _meta = resolve_package_upload(meta["upload_id"], actor_owner)
        assert path.is_file()

    def test_malformed_upload_id_rejected(self):
        actor = DeliveryActor(user_id=_OWNER)
        with pytest.raises(pc.PackageError) as ei:
            resolve_package_upload("../../etc/passwd", actor)
        assert ei.value.error_code == pc.ERR_IMPORT_SOURCE_INVALID


class TestLicenseDisclosure:
    def test_redacted_license_is_disclosed_not_silently_dropped(self):
        p = _pkg({})
        preview = inspect_package(p)
        licenses = {lic["logical_id"]: lic for lic in preview["licenses"]}
        assert licenses["ds-blocked"]["distributable"] is False
        assert licenses["ds-blocked"]["classification"] == pc.CLASS_REDACTED
        # redacted 不进 embedded counts —— 引用不冒充已打包文件
        assert preview["classification_counts"].get(
            pc.CLASS_EMBEDDED, 0) == 0


class TestCancelCleanliness:
    def test_staging_discard_leaves_no_half_package(self, tmp_path):
        """取消/失败路径：staging 目录被丢弃，exports 无半成品 ZIP。"""
        from app.services.project_exchange.exporter import (
            _discard_staging, packages_root,
        )

        package_id = pc.new_package_id()
        staging = packages_root() / f"{package_id}.staging"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "partial.geojson").write_text('{"half": true}')
        _discard_staging(package_id)
        assert not staging.exists()
        exports = Path(str(settings.DATA_DIR)) / "exports"
        if exports.exists():
            assert list(exports.glob("*.wgproject.zip")) == []


class TestProjectIsolation:
    def test_import_never_targets_existing_project(self):
        """导入面没有「覆盖现有项目」的入口 —— 服务签名层缺省即隔离。"""
        import inspect as pyinspect

        from app.services.project_exchange import importer

        sig = pyinspect.signature(importer.submit_import)
        assert "import_project_id" not in sig.parameters
        assert "overwrite" not in sig.parameters
        assert "target_project_id" not in sig.parameters


class TestFormatHonesty:
    def _pkg_with_table_payload(self, table_name: str) -> Path:
        import hashlib

        rows_bytes = json.dumps({"rows": [{"a": 1}]},
                                ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(rows_bytes).hexdigest()
        entry = {
            "format": pc.PACKAGE_FORMAT, "format_version": 1,
            "package_id": f"pkg-{'9' * 32}",
            "created_at": "2026-11-28T00:00:00Z",
            "project": {"name": "fmt"},
            "reproduction": {"level": "view_only"},
            "datasets": [{
                "logical_id": "ds-t", "name": "t",
                "classification": "embedded",
                "crs": "EPSG:4326",
                "payload": {"path": f"standard/tables/ds-t{table_name}",
                            "sha256": digest,
                            "bytes": len(rows_bytes)},
            }],
            "workflows": [], "artifacts": [], "maps": [],
            "files": [{"path": f"standard/tables/ds-t{table_name}",
                       "sha256": digest,
                       "bytes": len(rows_bytes), "mime": "text/csv"}],
            "disclosure": {"not_included": [], "external_links": [],
                           "unsupported": []},
            "fidelity_receipt": {"datasets": [], "writer_versions": {}},
        }
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(pc.MANIFEST_PATH,
                        json.dumps(entry, ensure_ascii=False))
            zf.writestr(f"standard/tables/ds-t{table_name}", rows_bytes)
        p = Path(str(settings.DATA_DIR)) / f"fmt{table_name}.zip"
        p.write_bytes(buf.getvalue())
        return p

    def test_parquet_dataset_not_falsely_labeled_csv(self):
        """UploadRecord.format 词表无 parquet —— 注册路径拒载，不伪造 csv。"""
        from app.core.database import SessionLocal

        from app.services.project_exchange.importer import _register_dataset

        import hashlib

        stub = b"PAR1stub"
        real_digest = hashlib.sha256(stub).hexdigest()
        dataset_entry = pc.ManifestDataset(
            logical_id="ds-t", name="t", classification="embedded",
            crs="EPSG:4326",
            payload=pc.DatasetPayloadRef(
                path="standard/tables/ds-t.parquet",
                sha256=real_digest, bytes=len(stub)))
        created_files: list = []
        staging = Path(str(settings.DATA_DIR))
        (staging / "standard" / "tables").mkdir(parents=True, exist_ok=True)
        (staging / "standard" / "tables" / "ds-t.parquet").write_bytes(stub)

        with SessionLocal() as db:
            result = _register_dataset(
                db, "proj-fmt", type("M", (), {"package_id": "pkg-x"})(),
                dataset_entry, staging,
                DeliveryActor(user_id=_OWNER), created_files)
        assert result is None  # parquet 拒载（诚实），绝不伪造 csv 标签
        assert created_files == []  # 未写任何 upload 文件
