"""Data Fabric security tests: SSRF redirect re-validation, cross-tenant
catalog guards, and credential/URL redaction on egress.
"""
from unittest.mock import MagicMock

import pytest
import requests

from app.services.data_fabric.security import (
    DataFabricSecurity,
    DataFabricSecurityError,
    SSRFSafeHTTPAdapter,
    make_safe_session,
)


# ── SSRF: per-send re-validation (closes redirect-SSRF) ──────────────────────


def test_ssrf_adapter_blocks_metadata_ip_on_send():
    """The mounted adapter re-validates the URL on every send(). requests
    re-invokes the mounted adapter for each redirect hop, so this gate
    transitively blocks redirect→metadata/private-IP bypasses."""
    adapter = SSRFSafeHTTPAdapter(allow_private=False)
    req = requests.Request("GET", "http://169.254.169.254/latest/meta-data/").prepare()
    with pytest.raises(DataFabricSecurityError):
        adapter.send(req, timeout=1)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
    ],
)
def test_ssrf_adapter_blocks_private_ranges(url):
    adapter = SSRFSafeHTTPAdapter(allow_private=False)
    req = requests.Request("GET", url).prepare()
    with pytest.raises(DataFabricSecurityError):
        adapter.send(req, timeout=1)


def test_make_safe_session_mounts_adapter_on_both_schemes():
    s = make_safe_session(allow_private=False)
    http_adapter = s.get_adapter("http://anything.example")
    https_adapter = s.get_adapter("https://anything.example")
    assert isinstance(http_adapter, SSRFSafeHTTPAdapter)
    assert isinstance(https_adapter, SSRFSafeHTTPAdapter)


# ── Credential / URL redaction on egress ─────────────────────────────────────


def test_redact_url_strips_userinfo():
    out = DataFabricSecurity.redact_url("postgres://user:secret@db.host:5432/gis")
    assert "secret" not in out
    assert "user" not in out
    assert "db.host" in out and "5432" in out


def test_redact_url_preserves_url_without_userinfo():
    u = "https://example.com/wfs"
    assert DataFabricSecurity.redact_url(u) == u


def test_redact_url_handles_s3_and_none():
    assert DataFabricSecurity.redact_url("s3://bucket/key") == "s3://bucket/key"
    assert DataFabricSecurity.redact_url(None) is None


# ── Cross-tenant catalog access guard ────────────────────────────────────────


def _mock_db_for_item(source_org_id):
    """Build a fake db session returning a catalog item → source chain."""
    src = MagicMock()
    src.id = "src_A"
    src.org_id = source_org_id
    src.owner_id = None
    item = MagicMock()
    item.id = "cat_1"
    item.source_id = "src_A"

    db = MagicMock()
    # First query() call: CatalogItemModel lookup → item
    # Second query() call: DataSourceModel lookup → src
    item_query = MagicMock()
    item_query.filter.return_value.first.return_value = item
    src_query = MagicMock()
    src_query.filter.return_value.first.return_value = src
    db.query.side_effect = [item_query, src_query]
    return db, item, src


def test_authorize_catalog_item_blocks_cross_tenant():
    """A user in org B must NOT access an item owned by org A."""
    from app.api.routes.data_fabric import _authorize_catalog_item
    from fastapi import HTTPException

    db, _item, _src = _mock_db_for_item(source_org_id="org_A")
    user = {"user_id": "u_B", "org_id": "org_B"}
    with pytest.raises(HTTPException) as ei:
        _authorize_catalog_item(db, "cat_1", user)
    assert ei.value.status_code == 404  # 404 not 403 — no existence leak


def test_authorize_catalog_item_allows_same_tenant():
    from app.api.routes.data_fabric import _authorize_catalog_item

    db, item, _src = _mock_db_for_item(source_org_id="org_A")
    user = {"user_id": "u_A", "org_id": "org_A"}
    authorized = _authorize_catalog_item(db, "cat_1", user)
    assert authorized is item


def test_authorize_catalog_item_anonymous_blocked_for_owned_source():
    """Anonymous callers must not reach org-owned catalog items."""
    from app.api.routes.data_fabric import _authorize_catalog_item
    from fastapi import HTTPException

    db, _item, _src = _mock_db_for_item(source_org_id="org_A")
    with pytest.raises(HTTPException) as ei:
        _authorize_catalog_item(db, "cat_1", user=None)
    assert ei.value.status_code == 404
