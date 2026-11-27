"""工程包合同测试（T1）——format 门、有界校验、逻辑 ID 与 slug。"""
import pytest

from app.services.project_exchange import contracts as pc


class TestFormatGate:
    def test_valid_minimal_manifest_passes(self):
        manifest = pc.PackageManifest(
            package_id=f"pkg-{'a' * 32}",
            created_at="2026-11-28T00:00:00Z",
            project=pc.ProjectInfo(name="p"),
            reproduction=pc.ReproductionSummary(level="view_only"),
        )
        assert manifest.format == pc.PACKAGE_FORMAT
        assert manifest.format_version == 1

    def test_format_gate_rejects_wrong_format(self):
        raw = {
            "format": "some-other-bundle", "format_version": 1,
            "package_id": f"pkg-{'a' * 32}", "created_at": "x",
            "project": {"name": "p"}, "reproduction": {"level": "partial"},
        }
        with pytest.raises(pc.PackageError) as ei:
            pc.format_gate(raw)
        assert ei.value.error_code == pc.ERR_MANIFEST_INVALID

    def test_format_gate_rejects_unknown_version(self):
        raw = {
            "format": pc.PACKAGE_FORMAT, "format_version": 99,
            "package_id": f"pkg-{'a' * 32}", "created_at": "x",
            "project": {"name": "p"}, "reproduction": {"level": "partial"},
        }
        with pytest.raises(pc.PackageError) as ei:
            pc.format_gate(raw)
        assert ei.value.error_code == pc.ERR_MANIFEST_INVALID

    def test_format_gate_rejects_extra_keys(self):
        raw = {
            "format": pc.PACKAGE_FORMAT, "format_version": 1,
            "package_id": f"pkg-{'a' * 32}", "created_at": "x",
            "project": {"name": "p"}, "reproduction": {"level": "partial"},
            "owner_token": "secret-leak",  # 未知键必须拒绝
        }
        with pytest.raises(pc.PackageError):
            pc.format_gate(raw)

    def test_format_gate_rejects_oversized_manifest(self, monkeypatch):
        import json

        big = {"format": pc.PACKAGE_FORMAT, "format_version": 1,
               "package_id": f"pkg-{'a' * 32}", "created_at": "x",
               "project": {"name": "p", "description": "x" * (pc.MAX_MANIFEST_BYTES + 10)},
               "reproduction": {"level": "partial"}}
        blob = json.dumps(big).encode()
        assert len(blob) > pc.MAX_MANIFEST_BYTES
        with pytest.raises(pc.PackageError) as ei:
            pc.format_gate(big)
        assert ei.value.error_code == pc.ERR_MANIFEST_TOO_LARGE


class TestBoundedModels:
    def test_bbox_must_be_quad(self):
        with pytest.raises(Exception):
            pc.ManifestDataset(
                logical_id="ds-x", name="x", classification="missing",
                bbox=[1, 2, 3])

    def test_classification_is_closed_vocabulary(self):
        with pytest.raises(Exception):
            pc.ManifestDataset(
                logical_id="ds-x", name="x", classification="embedded_and_alive")

    def test_dataset_payload_requires_hex_shape(self):
        ref = pc.DatasetPayloadRef(path="standard/layers.gpkg",
                                   member="ds-x", sha256="z" * 64, bytes=1)
        assert ref.member == "ds-x"


class TestLogicalIds:
    def test_slugify_is_ascii_and_bounded(self):
        assert pc.slugify("学校分布 Schools!!") == "schools"
        assert pc.slugify("     ") == "item"
        assert len(pc.slugify("x" * 500)) <= 48

    def test_valid_logical_id_kinds(self):
        assert pc.valid_logical_id("ds-schools")
        assert pc.valid_logical_id("wf-buffer-2")
        assert pc.valid_logical_id("art-result")
        assert pc.valid_logical_id("map-main")
        assert not pc.valid_logical_id("hack-schools")
        assert not pc.valid_logical_id("ds-")
        assert not pc.valid_logical_id("../etc/passwd")
        assert not pc.valid_logical_id("ds-..hidden")

    def test_is_hex_digest(self):
        assert pc.is_hex_digest("a" * 64)
        assert not pc.is_hex_digest("g" * 64)
        assert not pc.is_hex_digest("a" * 65)


class TestConstants:
    def test_classifications_and_levels_are_closed(self):
        assert set(pc.CLASSIFICATIONS) == {
            "embedded", "external_reference", "redacted", "missing",
            "unsupported"}
        assert set(pc.REPRO_LEVELS) == {
            "full_offline_recompute", "view_only", "partial"}

    def test_allowed_extensions_reject_archives(self):
        assert ".gpkg" in pc.ALLOWED_ENTRY_EXTENSIONS
        assert ".zip" not in pc.ALLOWED_ENTRY_EXTENSIONS
        assert ".tar" not in pc.ALLOWED_ENTRY_EXTENSIONS
        assert ".exe" not in pc.ALLOWED_ENTRY_EXTENSIONS

    def test_no_credentials_in_origin_model(self):
        fields = set(pc.OriginInfo.model_fields.keys())
        assert fields == {"system", "project_id", "project_name",
                          "app_version_hint"}
