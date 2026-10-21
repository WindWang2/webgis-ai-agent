"""Client-IP extraction shared by every rate-limit key.

SEC-F4: behind the production nginx proxy every request reaches the app with
``request.client.host`` = the nginx container IP. Rate limiters keyed on that
value collapse the whole platform into ONE shared bucket — 60 req/min for all
users combined, and a single attacker can lock everyone out. So the forwarded
headers carry the real client IP, and nginx sets ``X-Real-IP $remote_addr``
(deploy/nginx/nginx.conf) while appending the real peer as the LAST
``X-Forwarded-For`` hop (``$proxy_add_x_forwarded_for``). The leftmost XFF
value is attacker-controlled and must never key a limiter.

#1675: those headers are only trustworthy when the app is actually behind the
configured number of trusted proxy hops. When uvicorn is reached directly
(exposed port, internal service mesh, or an ingress that passes
``$http_x_real_ip`` through), every client can rotate its limiter bucket by
sending a fresh ``X-Real-IP`` per request — unbounded online password
brute-force. ``TRUSTED_PROXY_HOPS`` (default 1, matching the production nginx
single hop) declares how many proxy layers sit in front of the app:

- ``0`` — no trusted proxy (direct exposure): all forwarded headers are
  attacker-controlled and ignored; the TCP peer keys the limiter.
- ``1`` — production nginx: ``X-Real-IP`` is set from ``$remote_addr`` and is
  trustworthy; otherwise the last XFF hop (the peer nginx appended).
- ``N>1`` — N proxy layers: ``X-Real-IP`` semantics are unreliable across
  multiple hops, so the Nth-from-last XFF hop is used; an XFF chain shorter
  than the declared hops means the topology does not match the config, and we
  fall back to the peer instead of an attacker-controlled hop.
"""
from __future__ import annotations

import os

from starlette.requests import HTTPConnection

#: 可信代理跳数默认值 —— 生产 nginx 单跳（deploy/nginx/nginx.conf）。
_DEFAULT_TRUSTED_PROXY_HOPS = 1


def _trusted_proxy_hops() -> int:
    """可信代理跳数（#1675）。

    Lazy env 读（同 ``auth._allow_public_register``），便于测试逐例重置。
    非法值退回默认 1：保留经 nginx 的生产语义（退回 0 会让整个平台塌缩成
    同一个共享限流桶，比维持默认更糟）。
    """
    raw = os.getenv("TRUSTED_PROXY_HOPS", "")
    if not raw.strip():
        return _DEFAULT_TRUSTED_PROXY_HOPS
    try:
        return int(raw.strip())
    except ValueError:
        return _DEFAULT_TRUSTED_PROXY_HOPS


def client_ip_from(conn: HTTPConnection) -> str:
    """Best-effort real client IP, gated by TRUSTED_PROXY_HOPS (#1675)."""
    hops = _trusted_proxy_hops()
    # 直连暴露：转发头一律不可信，仅认 TCP peer。
    if hops <= 0:
        return conn.client.host if conn.client else "unknown"
    real_ip = (conn.headers.get("x-real-ip") or "").strip()
    if real_ip and hops == 1:
        return real_ip
    forwarded = conn.headers.get("x-forwarded-for")
    if forwarded:
        chain = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if len(chain) >= hops:
            return chain[-hops]
    return conn.client.host if conn.client else "unknown"
