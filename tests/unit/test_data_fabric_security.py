"""Data Fabric security tests: SSRF redirect re-validation, cross-tenant
catalog guards, and credential/URL redaction on egress.
"""
from copy import deepcopy
import json
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


# ── #1694 SEC-11：阿里云/腾讯云 IMDS 字面量 ──────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        # 100.64/10 CGNAT 不在 BLOCKED_NETWORKS —— 补进 BLOCKED_IPS_EXPLICIT
        # 之前，阿里云 IMDS 字面量连 allow_private=False 都能过门。
        "http://100.100.100.200/latest/meta-data/",
        "http://169.254.0.23/latest/meta-data/",  # 腾讯云 IMDS（link-local）
    ],
)
def test_cloud_imds_literal_ips_blocked(url):
    with pytest.raises(DataFabricSecurityError):
        DataFabricSecurity.validate_url(url)


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


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://reader:dsn-pass@DB.example:5432/gis?sslmode=require",
         "postgresql://DB.example:5432/gis?sslmode=require"),
        ("https://reader:http-pass@EXAMPLE.com:443/wfs?x=1#layer",
         "https://EXAMPLE.com:443/wfs?x=1#layer"),
        ("postgres://reader:dsn-pass@[2001:db8::1]:5432/gis",
         "postgres://[2001:db8::1]:5432/gis"),
        ("https://reader:p%40ss@example.com/wfs", "https://example.com/wfs"),
        ("https://reader:p@ss@example.com/wfs", "https://example.com/wfs"),
        ("https://token-only@example.com/wfs", "https://example.com/wfs"),
        # Invalid legacy endpoints must neither leak nor make egress raise.
        ("https://reader:http-pass@example.com:invalid/wfs",
         "https://example.com:invalid/wfs"),
        ("https://reader:http-pass@[invalid/wfs", "https://[invalid/wfs"),
        ("https://reader:http-pass@[not-ip]/wfs", "https://[not-ip]/wfs"),
        ("https://reader:http-pass@example.com:0/wfs", "https://example.com:0/wfs"),
        (" \tHTTPS://reader:http-pass@example.com/wfs", " \tHTTPS://example.com/wfs"),
        ("ht\ttps:\r/\n/reader:http-pass@example.com/wfs", "ht\ttps:\r/\n/example.com/wfs"),
    ],
)
def test_redact_url_preserves_everything_except_userinfo(url, expected):
    assert DataFabricSecurity.redact_url(url) == expected


@pytest.mark.parametrize(
    "value",
    [
        None, 42, False, "", "reader@example.com", "label:owner@example.com",
        "https://EXAMPLE.com:443/wfs?contact=reader@example.com#layer",
        "https://example.com/path@label", "s3://bucket/key", "/data/roads.csv",
        "https://[invalid/wfs",
    ],
)
def test_redact_url_preserves_values_without_authority_userinfo(value):
    assert DataFabricSecurity.redact_url(value) == value


def test_sanitize_profile_redacts_url_aliases_and_nested_strings_without_mutation():
    from app.schemas.data_fabric_schema import ConnectionProfile

    dsn = "postgresql://reader:dsn-pass@db.example:5432/gis"
    http_url = "https://reader:http-pass@example.com/wfs"
    profile = ConnectionProfile(
        source_type="postgis", url=dsn,
        options={
            "mirrors": [{"endpoint": http_url}, [dsn, None, 3]],
            "password": "option-pass", "label": "reader@example.com",
            "public_url": "https://example.com/public?contact=reader@example.com",
        },
    ).model_dump()
    before = deepcopy(profile)

    sanitized = DataFabricSecurity.sanitize_profile_dict(profile)

    for alias in ("url", "endpoint", "endpoint_url"):
        assert sanitized[alias] == "postgresql://db.example:5432/gis"
    assert sanitized["options"]["mirrors"] == [
        {"endpoint": "https://example.com/wfs"},
        ["postgresql://db.example:5432/gis", None, 3],
    ]
    assert sanitized["options"]["password"] == "********"
    assert sanitized["options"]["label"] == before["options"]["label"]
    assert sanitized["options"]["public_url"] == before["options"]["public_url"]
    for password in ("dsn-pass", "http-pass", "option-pass"):
        assert password not in json.dumps(sanitized)
    assert profile == before


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
