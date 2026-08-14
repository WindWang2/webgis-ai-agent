"""Client-IP extraction shared by every rate-limit key.

SEC-F4: behind the production nginx proxy every request reaches the app with
``request.client.host`` = the nginx container IP. Rate limiters keyed on that
value collapse the whole platform into ONE shared bucket — 60 req/min for all
users combined, and a single attacker can lock everyone out.

Production nginx sets ``X-Real-IP $remote_addr`` and appends the real peer as
the LAST ``X-Forwarded-For`` hop (``$proxy_add_x_forwarded_for``). The
leftmost XFF value is attacker-controlled and must never key a limiter.

Trust model: identical to the auth rate limits that have used this scheme
since SEC-03 (direct exposure lets a client rotate buckets by spoofing the
header — an accepted tradeoff, consistently applied).
"""
from __future__ import annotations

from starlette.requests import HTTPConnection


def client_ip_from(conn: HTTPConnection) -> str:
    """Best-effort real client IP: X-Real-IP → last XFF hop → peer."""
    real_ip = (conn.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    forwarded = conn.headers.get("x-forwarded-for")
    if forwarded:
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            return hops[-1]
    return conn.client.host if conn.client else "unknown"
