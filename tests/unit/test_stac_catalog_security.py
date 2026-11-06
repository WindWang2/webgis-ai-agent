"""Exercise real STAC IO: every catalog/search/redirect destination is guarded."""
import json
import socket

import pytest
import requests
from pystac_client.exceptions import APIError

from app.core import egress
from app.services.rs import stac_client


@pytest.fixture
def catalog_transport(monkeypatch):
    calls = []
    monkeypatch.setattr(stac_client, "_catalog_url", lambda: "https://catalog.example/v1")
    monkeypatch.setattr(egress, "current_policy", lambda: egress.EgressPolicy.from_params(
        profile="cloud", mode="unrestricted", allow_private=False,
    ))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ])
    # Requests inherits local proxy environment; production rejects proxy DNS
    # explicitly. These tests exercise direct-transport target validation.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)
    root = {
        "type": "Catalog", "id": "review", "description": "review catalog",
        "stac_version": "1.0.0",
        "conformsTo": ["https://api.stacspec.org/v1.0.0/item-search"],
        "links": [
            {"rel": "self", "href": "https://catalog.example/v1"},
            {"rel": "root", "href": "https://catalog.example/v1"},
            {"rel": "search", "href": "http://169.254.169.254/search", "type": "application/geo+json", "method": "GET"},
        ],
    }

    def transport(adapter, request, **kwargs):
        calls.append(request.url)
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response._content = json.dumps(root).encode()
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", transport)
    stac_client._catalog_for_url.cache_clear()
    yield calls, root
    stac_client._catalog_for_url.cache_clear()


def test_catalog_dns_private_address_rejected_before_transport(monkeypatch, catalog_transport):
    calls, _ = catalog_transport
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    with pytest.raises(APIError, match="SSRF"):
        stac_client.StacClientPrimitive()._get_catalog()
    assert calls == []


def test_catalog_respects_airgap_allowlist(monkeypatch, catalog_transport):
    calls, _ = catalog_transport
    monkeypatch.setattr(egress, "current_policy", lambda: egress.EgressPolicy.from_params(
        profile="air_gapped", mode="allowlist", allow_raw="approved.example", allow_private=False,
    ))
    with pytest.raises(APIError, match="egress policy"):
        stac_client.StacClientPrimitive()._get_catalog()
    assert calls == []


def test_stac_search_link_cannot_reach_metadata(catalog_transport):
    calls, _ = catalog_transport
    catalog = stac_client.StacClientPrimitive()._get_catalog()
    with pytest.raises(APIError, match="SSRF"):
        list(catalog.search(method="GET", max_items=1).items())
    assert calls == ["https://catalog.example/v1"]


def test_catalog_redirect_is_revalidated(monkeypatch, catalog_transport):
    calls, _ = catalog_transport

    def redirect(adapter, request, **kwargs):
        calls.append(request.url)
        response = requests.Response()
        response.status_code = 302
        response.url = request.url
        response.request = request
        response.headers["Location"] = "http://169.254.169.254/latest/meta-data"
        response._content = b""
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", redirect)
    with pytest.raises(APIError, match="SSRF"):
        stac_client.StacClientPrimitive()._get_catalog()
    assert calls == ["https://catalog.example/v1"]
