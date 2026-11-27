"""复现校验测试（T11）——分级判定、环境漂移、显式重算对比。"""
from app.services.project_exchange import contracts as pc
from app.services.project_exchange.verify import (
    verify_import, verify_reproduction,
)


def _manifest(level="view_only", datasets=None, workflows=None):
    return pc.PackageManifest(
        package_id=f"pkg-{'f' * 32}", created_at="2026-11-28T00:00:00Z",
        project=pc.ProjectInfo(name="p"),
        reproduction=pc.ReproductionSummary(level=level),
        datasets=datasets or [], workflows=workflows or [],
    )


class TestVerifyImport:
    def test_all_semantic_when_everything_matches(self):
        manifest = _manifest(datasets=[
            pc.ManifestDataset(
                logical_id="ds-a", name="a", classification="embedded",
                crs="EPSG:4326", feature_count=5,
                payload=pc.DatasetPayloadRef(
                    path=pc.LAYERS_GPKG_PATH, member="ds-a",
                    sha256="a" * 64, bytes=1)),
        ])
        mapping = type("M", (), {"project_id": "proj-v"})()
        mapping.datasets = {"ds-a": {"dataset_id": "ds_real_1",
                                     "upload_id": "1",
                                     "session_restore": None}}
        mapping.workflows = {}
        mapping.artifacts = {}
        mapping.maps = {}
        report = verify_import(manifest, mapping, None)
        # ds_real_1 不在隔离 DB → 诚实 partial（行不存在）
        assert report["verdict"] == pc.FIDELITY_PARTIAL

    def test_environment_records_version_drift(self):
        manifest = _manifest()
        manifest.fidelity_receipt.writer_versions = {"pyogrio": "0.0.1"}
        report = verify_import(manifest, type("M", (), {"project_id": "p"})(),
                               None)
        env = report["environment"]
        assert "pyogrio" in env["current_writer_versions"]
        assert env["version_drift"]["pyogrio"]["exported"] == "0.0.1"


class TestVerifyReproduction:
    def test_exact_metrics_grade_semantic(self):
        manifest = _manifest(datasets=[
            pc.ManifestDataset(
                logical_id="ds-a", name="a", classification="embedded",
                crs="EPSG:4326", feature_count=5,
                bbox=[104.0, 30.0, 104.1, 30.1]),
        ])
        report = verify_reproduction(manifest, {
            "ds-a": {"feature_count": 5, "crs": "EPSG:4326",
                     "bbox": [104.0, 30.0, 104.1, 30.1]},
        })
        assert report["verdict"] == pc.FIDELITY_SEMANTIC
        item = report["items"][0]
        assert item["feature_count_match"] is True
        assert item["crs_match"] is True

    def test_count_mismatch_is_partial(self):
        manifest = _manifest(datasets=[
            pc.ManifestDataset(
                logical_id="ds-a", name="a", classification="embedded",
                crs="EPSG:4326", feature_count=5),
        ])
        report = verify_reproduction(manifest, {
            "ds-a": {"feature_count": 4, "crs": "EPSG:4326"},
        })
        assert report["verdict"] == pc.FIDELITY_PARTIAL
        assert report["items"][0]["feature_count_match"] is False

    def test_missing_metrics_not_evaluated(self):
        manifest = _manifest(datasets=[
            pc.ManifestDataset(
                logical_id="ds-a", name="a", classification="embedded",
                crs="EPSG:4326", feature_count=5),
        ])
        report = verify_reproduction(manifest, {})
        assert report["items"][0]["verdict"] == pc.FIDELITY_NOT_EVALUATED
        assert report["verdict"] == pc.FIDELITY_PARTIAL
