"""R1 data guards: local raster href, metadata SSRF, promotion predicate."""
import pytest

from app.services.artifact_lifecycle import _promotion_blob_protection
from app.services.data_fabric.cog_materialization import (
    CogMaterializationError,
    preview_remote_cog,
)
from app.services.data_fabric.security import DataFabricSecurity, DataFabricSecurityError


def test_remote_raster_rejects_local_and_vsi_hrefs():
    for href in ("/data/other/secret.tif", "file:///etc/passwd", "/vsizip//tmp/a.zip/a.tif", "/vsimem/x"):
        with pytest.raises(CogMaterializationError) as exc:
            preview_remote_cog(href)
        assert exc.value.reason_code == "ssrf_blocked"


def test_allow_private_still_blocks_metadata_literal():
    with pytest.raises(DataFabricSecurityError):
        DataFabricSecurity.validate_url("http://169.254.169.254/latest/meta-data/", allow_private=True)
    with pytest.raises(DataFabricSecurityError):
        DataFabricSecurity.validate_url("http://metadata.google.internal/", allow_private=True)


def test_promotion_predicate_keeps_lakehouse_content_blob():
    digest = "a" * 64
    reason = _promotion_blob_protection(
        digest,
        f"aaaa/{digest}",
        0.0,
        now=10**12,
        grace_hours=1,
        refcounts_by_location={},
        refcounts_by_sha={},
        artifact_locations=set(),
        pinned_shas=set(),
        lakehouse_shas={digest},
    )
    assert reason and reason.startswith("lakehouse")
    assert _promotion_blob_protection(
        "b" * 64,
        "bbbb/" + "b" * 64,
        0.0,
        now=10**12,
        grace_hours=1,
        refcounts_by_location={},
        refcounts_by_sha={},
        artifact_locations=set(),
        pinned_shas=set(),
        lakehouse_shas=set(),
    ) is None
