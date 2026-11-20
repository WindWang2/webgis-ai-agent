"""ArcGIS tokens must not ride on URLs that requests copies into errors."""

import logging

import pytest
import requests

from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from app.services.data_fabric.adapters.arcgis_adapter import ArcGISAdapter
from app.services.data_fabric.errors import (
    SourceBadResponseError,
    SourceUnreachableError,
)
from tests.unit.test_ogc_adapters_753 import ARCGIS_LAYER

_TOKEN = "super-secret-arcgis-token"
_URL = "https://example.com/arcgis/rest/services/Hosted/FS/FeatureServer"


def _adapter(*, token: str = _TOKEN, url: str = _URL) -> ArcGISAdapter:
    profile = ConnectionProfile(
        provider_type="arcgis",
        endpoint=url,
        options={"token": token},
    )
    return ArcGISAdapter(profile)


def test_arcgis_token_uses_header_not_query(caplog):
    adapter = _adapter()
    assert adapter.session.headers["X-Esri-Authorization"] == f"Bearer {_TOKEN}"
    assert "token" not in adapter._base_params(None)

    calls = []

    def _get(url, params=None, timeout=None, headers=None, **kwargs):
        calls.append({"url": url, "params": dict(params or {})})
        from urllib.parse import urlencode

        full = url if not params else f"{url}?{urlencode(params)}"
        raise requests.HTTPError(f"500 Server Error: boom for url: {full}")

    adapter.session.get = _get
    adapter._layer_meta_cache["0"] = dict(ARCGIS_LAYER)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(SourceBadResponseError) as raised:
            adapter.query("0", QuerySpec(limit=1))
    assert calls
    assert "token" not in calls[-1]["params"]
    assert _TOKEN not in calls[-1]["url"]
    blob = " ".join(f"{k}={v}" for k, v in calls[-1]["params"].items())
    assert _TOKEN not in blob
    assert _TOKEN not in str(raised.value)
    assert _TOKEN not in caplog.text
    assert raised.value.__cause__ is None or _TOKEN not in str(raised.value.__cause__)


def test_arcgis_errors_redact_token_query(caplog):
    secret = "url-embedded-token-value"
    adapter = _adapter(token=secret, url=f"{_URL}?token={secret}")

    def _get(url, params=None, timeout=None, **kwargs):
        raise requests.ConnectionError(
            f"Max retries exceeded with url: {url}"
        )

    adapter.session.get = _get
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(SourceUnreachableError) as raised:
            adapter.describe("0")
        assert adapter.list_datasets() == []
        assert adapter.probe() is False
    assert secret not in str(raised.value)
    assert "token=REDACTED" in str(raised.value)
    assert raised.value.__cause__ is None
    assert secret not in caplog.text
