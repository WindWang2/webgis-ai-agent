"""End-to-end Data Fabric fault-injection tests — fully offline (Section 59/63).

Uses FakeFabricAdapter (canned responses + per-hop SSRF validation) so the real
requests redirect machinery exercises redirect-SSRF, retry, pagination, auth and
oversized-response paths without any network.
"""
import pytest

from app.services.data_fabric.errors import (
    ResultTooLargeError,
    SourceAuthFailedError,
    SourceBadResponseError,
)
from app.services.data_fabric import limits
from app.services.data_fabric.reliability import RetryPolicy, retry_call
from app.services.data_fabric.security import DataFabricSecurityError

from tests.fixtures.data_fabric.fake_server import (
    PageCounter,
    make_response,
    redirect_to,
    session_with_fake,
)

# A public-looking, unresolvable host passes validate_url's pre-flight gate
# (unresolvable hostnames are not hard-blocked), so we can reach the redirect
# logic entirely offline.
BASE = "http://fabric-fault-injection.example"


# ── SSRF: redirect to cloud metadata is blocked on the next hop ──────────────


def test_redirect_to_metadata_ip_is_blocked():
    """The P0 redirect-SSRF bypass: a 302 to 169.254.169.254 must be rejected
    when the safe session follows it (validate runs per hop)."""
    routes = (("/redirect", lambda req: redirect_to(req.url, "http://169.254.169.254/latest/meta-data/")),)
    s = session_with_fake(routes, allow_private=False)
    with pytest.raises(DataFabricSecurityError):
        s.get(f"{BASE}/redirect", timeout=2)


def test_redirect_to_loopback_is_blocked():
    routes = (("/r", lambda req: redirect_to(req.url, "http://127.0.0.1:8080/internal")),)
    s = session_with_fake(routes, allow_private=False)
    with pytest.raises(DataFabricSecurityError):
        s.get(f"{BASE}/r", timeout=2)


def test_legitimate_same_host_redirect_followed():
    """Non-SSRF redirects still work — the guard is not over-broad."""
    routes = (("/ok", lambda req: redirect_to(req.url, f"{BASE}/final")),
              ("/final", lambda req: make_response(req.url, json_body={"ok": True})))
    s = session_with_fake(routes, allow_private=False)
    resp = s.get(f"{BASE}/ok", timeout=2)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── Retry: transient retried, auth not ───────────────────────────────────────


def test_retry_then_success_on_transient_503():
    """retry_call recovers when the 3rd attempt succeeds."""
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] < 3:
            return make_response(req.url, status=503, text="down")
        return make_response(req.url, json_body={"features": []})

    s = session_with_fake(("/q", handler))

    def do_get():
        r = s.get(f"{BASE}/q", timeout=2)
        if r.status_code != 200:
            raise SourceBadResponseError(f"status {r.status_code}")
        return r

    out = retry_call(do_get, policy=RetryPolicy(max_attempts=5, base_sleep=0), sleep=lambda _s: None)
    assert out.status_code == 200
    assert state["n"] == 3


def test_auth_failure_not_retried():
    """401 is permanent — no retry."""
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        return make_response(req.url, status=401, text="unauthorized")

    s = session_with_fake(("/auth", handler))

    def do_get():
        r = s.get(f"{BASE}/auth", timeout=2)
        if r.status_code == 401:
            raise SourceAuthFailedError("401")
        return r

    with pytest.raises(SourceAuthFailedError):
        retry_call(do_get, policy=RetryPolicy(max_attempts=4, base_sleep=0), sleep=lambda _s: None)
    assert state["n"] == 1  # not retried


# ── Pagination: bounded pages, empty final ──────────────────────────────────


def test_pagination_collects_all_pages_then_stops():
    pc = PageCounter(BASE + "/items", [[{"id": 1}], [{"id": 2}], []])
    s = session_with_fake(("/items", pc.handler))

    collected = []
    url = f"{BASE}/items"
    for _ in range(10):  # bounded by the test, not the client
        r = s.get(url, timeout=2)
        body = r.json()
        collected.extend(body.get("features", []))
        nxt = (body.get("links") or {}).get("next")
        if not nxt:
            break
        url = nxt
    assert collected == [{"id": 1}, {"id": 2}]
    assert pc.calls == 3  # collected 2 pages + saw the empty terminator


# ── Oversized result guard ──────────────────────────────────────────────────


def test_oversized_response_trips_guard():
    """A server that ignores `limit` and returns a huge payload is rejected by
    enforce_result_bounds before materialization."""
    big = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"id": i}} for i in range(10)]
    with pytest.raises(ResultTooLargeError):
        limits.enforce_result_bounds(big, max_feat=3, max_bytes=10 * 1024 * 1024)


# ── Error taxonomy on HTTP status ───────────────────────────────────────────


def test_classify_http_status_mapping():
    from app.services.data_fabric.errors import classify_http_status

    assert classify_http_status(401) == "SOURCE_AUTH_FAILED"
    assert classify_http_status(404) == "SOURCE_UNREACHABLE"
    assert classify_http_status(429) == "SOURCE_RATE_LIMITED"
    assert classify_http_status(503) == "SOURCE_BAD_RESPONSE"
    assert classify_http_status(400) == "SOURCE_BAD_RESPONSE"
