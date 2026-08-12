"""Offline fault-injection transport for Data Fabric adapter tests (Section 59).

``FakeFabricAdapter`` is a ``requests`` HTTPAdapter that returns canned responses
keyed by URL path — no network, no sockets, fully deterministic. Crucially it
subclasses ``SSRFSafeHTTPAdapter`` and runs the same per-hop SSRF validation, so
``requests``' redirect machinery exercises the real redirect→revalidate path:
a route answering ``302 → http://169.254.169.254/`` makes the next ``send()``
call ``validate_url`` on the metadata IP and raise ``DataFabricSecurityError``.

Mount via ``session_with_fake(routes)``. Routes match by path prefix; the first
matching handler wins. Handlers receive the ``PreparedRequest`` and return a
``requests.Response`` (use ``make_response``).
"""
from __future__ import annotations

import json as _json
from typing import Callable, Dict, Iterable, Optional, Tuple

import requests

from app.services.data_fabric.security import SSRFSafeHTTPAdapter

# A route handler: given the prepared request, return a response.
RouteHandler = Callable[[requests.PreparedRequest], requests.Response]
# Route table: ordered (path-prefix, handler) pairs — first match wins.
RouteTable = Tuple[Tuple[str, RouteHandler], ...]


def make_response(
    url: str,
    *,
    status: int = 200,
    json_body: Optional[object] = None,
    text: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
) -> requests.Response:
    resp = requests.Response()
    resp.url = url
    resp.status_code = status
    resp.headers.update(headers or {})
    if json_body is not None:
        resp._content = _json.dumps(json_body).encode()
        resp.headers["Content-Type"] = "application/json"
    elif text is not None:
        resp._content = text.encode()
        resp.headers["Content-Type"] = "text/plain"
    else:
        resp._content = b""
    return resp


def redirect_to(url: str, target: str) -> requests.Response:
    """Build a 302 response from ``url`` redirecting to ``target``."""
    return make_response(url, status=302, headers={"Location": target})


def _normalize_routes(routes) -> list[tuple[str, RouteHandler]]:
    """Accept either [(prefix, handler), ...] or a single (prefix, handler) pair."""
    if not routes:
        return []
    # Single (prefix, handler) pair vs list of pairs: disambiguate by the first
    # element being a string (prefix) vs a tuple (a route).
    if isinstance(routes[0], str):
        return [(routes[0], routes[1])]
    return [(r[0], r[1]) for r in routes]


class FakeFabricAdapter(SSRFSafeHTTPAdapter):
    """Canned-response adapter that still enforces per-hop SSRF validation."""

    def __init__(self, routes, allow_private: bool = False):
        super().__init__(allow_private=allow_private)
        self.routes = _normalize_routes(routes)
        self.requests: list[str] = []  # record of URLs seen (for assertions)

    def send(self, request, **kwargs):  # type: ignore[override]
        # Inherited contract: validate every hop (incl. redirect targets).
        from urllib.parse import urlparse

        from app.services.data_fabric.security import DataFabricSecurity

        DataFabricSecurity.validate_url(request.url, allow_private=self._allow_private)
        self.requests.append(request.url)
        path = urlparse(request.url).path
        for prefix, handler in self.routes:
            if path.startswith(prefix):
                resp = handler(request)
                # requests' redirect resolver reads .request and .connection to
                # rebuild/re-send the redirected request through this adapter.
                resp.request = request
                resp.connection = self
                return resp
        resp = make_response(request.url, status=404, text=f"no fake route for {path}")
        resp.request = request
        resp.connection = self
        return resp


def session_with_fake(routes: RouteTable, allow_private: bool = False) -> requests.Session:
    """A requests.Session whose every request is served by the fake routes,
    with SSRF validation enforced per hop (redirect-safe)."""
    s = requests.Session()
    adapter = FakeFabricAdapter(routes, allow_private=allow_private)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


# ── Stateful helpers for pagination / counters ──────────────────────────────


class PageCounter:
    """Helper to build multi-page OGC-style responses with a deterministic
    ``next`` link chain and request counting."""

    def __init__(self, base_url: str, pages: Iterable[list]):
        self.base_url = base_url.rstrip("/")
        self.pages = list(pages)
        self.calls = 0

    def handler(self, request):
        self.calls += 1
        idx = min(self.calls - 1, len(self.pages) - 1)
        page = self.pages[idx]
        body: dict = {"type": "FeatureCollection", "features": page}
        if self.calls < len(self.pages):
            body["links"] = {"next": f"{self.base_url}/items?page={self.calls + 1}"}
        return make_response(request.url, json_body=body)
