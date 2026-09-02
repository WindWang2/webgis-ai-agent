"""SSRF tests for DataFabricConnectionManager host/port-only connects (#1107).

Previously, SSRF validation ran only when ``profile.url`` was set. PostGIS
(and other) connections that supply ``host``/``port`` without a URL skipped
``DataFabricSecurity.validate_url`` and could reach private/loopback/metadata
IPs. These tests lock the host-only gate and keep the URL path unchanged.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.connection_manager import DataFabricConnectionManager
from app.services.data_fabric.security import DataFabricSecurity, DataFabricSecurityError


def _host_only_profile(host: str, port: int = 5432, allow_private: bool = False) -> ConnectionProfile:
    return ConnectionProfile(
        id=f"ssrf-{host.replace('.', '-').replace(':', '-')}",
        name="host-only SSRF fixture",
        source_type="postgis",
        url="",
        host=host,
        port=port,
        database="gis",
        username="u",
        password="p",
        allow_private=allow_private,
    )


class TestConnectionManagerHostOnlySSRF:
    """Host/port-only connects must hit the same SSRF gate as URL connects."""

    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1",
            "169.254.169.254",
            "192.168.1.10",
            "10.0.0.5",
            "172.16.0.1",
            "localhost",
        ],
    )
    def test_rejects_private_loopback_metadata_host_without_url(self, host):
        mgr = DataFabricConnectionManager()
        profile = _host_only_profile(host)
        with pytest.raises(DataFabricSecurityError):
            mgr.connect(profile)

    def test_rejects_ipv6_loopback_host_without_url(self):
        mgr = DataFabricConnectionManager()
        profile = _host_only_profile("::1")
        with pytest.raises(DataFabricSecurityError):
            mgr.connect(profile)

    @patch("app.services.data_fabric.connection_manager.create_adapter_for_profile")
    def test_allows_public_host_without_url(self, mock_factory):
        mock_adapter = MagicMock()
        mock_adapter.sync.return_value = {"status": "synced", "count": 0}
        mock_adapter.__class__.__name__ = "PostGISAdapter"
        mock_factory.return_value = mock_adapter

        mgr = DataFabricConnectionManager()
        # 8.8.8.8 is a public literal IP — SSRF gate must allow it.
        profile = _host_only_profile("8.8.8.8")
        connected, adapter = mgr.connect(profile)
        assert connected.host == "8.8.8.8"
        assert adapter is mock_adapter
        mock_factory.assert_called_once()

    def test_url_path_still_rejects_private(self):
        """URL-based connects keep the pre-existing SSRF behavior."""
        mgr = DataFabricConnectionManager()
        profile = ConnectionProfile(
            id="url-private",
            source_type="wfs",
            url="http://127.0.0.1/geoserver/wfs",
            allow_private=False,
        )
        with pytest.raises(DataFabricSecurityError):
            mgr.connect(profile)

    @patch("app.services.data_fabric.connection_manager.create_adapter_for_profile")
    def test_allow_private_permits_host_only(self, mock_factory):
        mock_adapter = MagicMock()
        mock_adapter.sync.return_value = {}
        mock_adapter.__class__.__name__ = "PostGISAdapter"
        mock_factory.return_value = mock_adapter

        mgr = DataFabricConnectionManager()
        profile = _host_only_profile("127.0.0.1", allow_private=True)
        connected, _ = mgr.connect(profile)
        assert connected.host == "127.0.0.1"


class TestValidateUrlPostgresScheme:
    """Synthetic postgresql:// URLs used by host-only validation must be accepted."""

    def test_postgresql_scheme_blocks_private(self):
        with pytest.raises(DataFabricSecurityError):
            DataFabricSecurity.validate_url("postgresql://192.168.0.1:5432")

    def test_postgresql_scheme_allows_public(self):
        url = "postgresql://8.8.8.8:5432"
        assert DataFabricSecurity.validate_url(url) == url
