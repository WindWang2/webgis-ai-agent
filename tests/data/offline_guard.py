"""Offline socket guard (ads-v1 DS0 / gap A11, ADR-0170).

Hard guarantee that tests never reach the **external** network: when
``ADS_FORCE_OFFLINE=1`` is set, outbound connects to public internet
addresses raise ``NetworkBlockedError`` instead of dialling out. Loopback
(127.0.0.0/8, ::1, localhost) and RFC1918/private ranges stay allowed —
they cannot reach the internet, and in-suite infrastructure (fake servers,
fakeredis, TestClient) legitimately uses loopback.

The gate therefore proves *no internet egress*, not "no sockets at all".

Used two ways:

- **Session-wide gate** — ``tests/conftest.py`` installs the guard for the
  whole run when the env var is set, so the offline gate
  (``ADS_FORCE_OFFLINE=1 pytest …``) *proves* a lane is offline-green rather
  than assuming it.
- **Scoped** — ``with offline_socket_guard():`` around a specific test that
  must prove it dials nothing external.

Implementation: ``socket.socket`` is subclassed with connect/connect_ex
address vetting (loopback/private pass, anything else is blocked); AF_UNIX
passes untouched; ``socket.create_connection`` is vetted the same way.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any, Optional

import pytest

ENV_FLAG = "ADS_FORCE_OFFLINE"


class NetworkBlockedError(RuntimeError):
    """Raised instead of dialling out while the offline guard is active."""


def _address_host(address: Any) -> Optional[str]:
    """Extract the connect target host from an address tuple-ish value."""
    if isinstance(address, tuple) and address:
        host = address[0]
        return str(host) if host is not None else None
    return None


def _is_internal(host: Optional[str]) -> bool:
    """Loopback / private / link-local → internal (allowed); public → blocked."""
    if host is None:
        return True  # non-tuple address families (AF_UNIX paths etc.)
    lowered = str(host).lower().strip("[]")
    if lowered in {"localhost", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(lowered)
    except ValueError:
        return False  # hostname other than localhost → needs DNS+internet → blocked
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_unspecified
    )


class _OfflineGuard:
    """Installs connect-level blockers; ``restore()`` puts the originals back."""

    def __init__(self) -> None:
        self._restorers = []

    def install(self) -> "_OfflineGuard":
        real_socket = socket.socket
        real_create_connection = socket.create_connection

        class GuardedSocket(real_socket):
            """socket whose outbound connects are vetted (internal pass)."""

            def connect(self, address):  # noqa: ANN001 — matches stdlib signature
                if not _is_internal(_address_host(address)):
                    raise NetworkBlockedError(
                        f"external network connect to {address!r} is blocked under "
                        f"{ENV_FLAG}; route external-protocol tests through "
                        "tests/data/fabric_fixtures.py"
                    )
                return super().connect(address)

            def connect_ex(self, address):  # noqa: ANN001
                if not _is_internal(_address_host(address)):
                    raise NetworkBlockedError(
                        f"external network connect to {address!r} is blocked under "
                        f"{ENV_FLAG}; route external-protocol tests through "
                        "tests/data/fabric_fixtures.py"
                    )
                return super().connect_ex(address)

        def guarded_create_connection(address, *args, **kwargs):  # noqa: ANN001
            host = address[0] if isinstance(address, tuple) else address
            if not _is_internal(str(host) if host is not None else None):
                raise NetworkBlockedError(
                    f"external network connect to {address!r} is blocked under {ENV_FLAG}"
                )
            return real_create_connection(address, *args, **kwargs)

        socket.socket = GuardedSocket
        socket.create_connection = guarded_create_connection
        self._restorers = [
            lambda: setattr(socket, "socket", real_socket),
            lambda: setattr(socket, "create_connection", real_create_connection),
        ]
        return self

    def restore(self) -> None:
        while self._restorers:
            self._restorers.pop()()


_ACTIVE: Optional[_OfflineGuard] = None


def install_global_if_flagged() -> bool:
    """Install the session-wide guard when ``ADS_FORCE_OFFLINE`` is truthy.

    Called once from ``tests/conftest.py``; idempotent. Returns whether the
    guard is now active.
    """
    global _ACTIVE
    import os

    if _ACTIVE is not None:
        return True
    if os.environ.get(ENV_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}:
        _ACTIVE = _OfflineGuard().install()
        return True
    return False


class offline_socket_guard:
    """Context-manager form for scoped offline proof inside a single test."""

    def __enter__(self) -> None:
        self._guard = _OfflineGuard().install()

    def __exit__(self, exc_type, exc, tb) -> None:
        self._guard.restore()


@pytest.fixture
def offline_guard():
    """Opt-in fixture: assert a test path dials nothing external."""
    guard = _OfflineGuard().install()
    try:
        yield
    finally:
        guard.restore()
