"""Local-file path guard tests (Section 44): traversal, symlink escape,
sensitive-system-dir blocking, allowed-root enforcement, size cap."""
import os

import pytest

from app.services.data_fabric.security import (
    DataFabricSecurityError,
    resolve_safe_local_path,
)


def test_blocks_sensitive_system_dir():
    with pytest.raises(DataFabricSecurityError):
        resolve_safe_local_path("/etc/passwd")
    with pytest.raises(DataFabricSecurityError):
        resolve_safe_local_path("/proc/self/environ")


def test_blocks_empty_and_nonstring():
    with pytest.raises(DataFabricSecurityError):
        resolve_safe_local_path("")
    with pytest.raises(DataFabricSecurityError):
        resolve_safe_local_path(None)  # type: ignore[arg-type]


def test_allows_path_under_allowed_root(tmp_path):
    f = tmp_path / "data.parquet"
    f.write_bytes(b"PAR1")
    resolved = resolve_safe_local_path(str(f), allowed_roots=[str(tmp_path)])
    assert str(resolved).endswith("data.parquet")


def test_blocks_path_outside_allowed_root(tmp_path):
    f = tmp_path / "data.parquet"
    f.write_bytes(b"PAR1")
    # allowed root is a different dir -> the file escapes it
    other = tmp_path.parent / "other_root_x"
    with pytest.raises(DataFabricSecurityError):
        resolve_safe_local_path(str(f), allowed_roots=[str(other)])


def test_blocks_symlink_escape(tmp_path):
    """A symlink that resolves outside the allowed root is rejected."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    link = allowed / "link.parquet"
    os.symlink(outside, link)
    with pytest.raises(DataFabricSecurityError):
        resolve_safe_local_path(str(link), allowed_roots=[str(allowed)])


def test_blocks_oversize_file(tmp_path):
    f = tmp_path / "big.parquet"
    f.write_bytes(b"x" * 2048)
    with pytest.raises(DataFabricSecurityError):
        resolve_safe_local_path(str(f), allowed_roots=[str(tmp_path)], max_bytes=1024)


def test_guard_blocks_geoparquet_sensitive_path():
    """The wired guard raises typed SecurityBlockedError, never reads the file.

    ADR-0094 Wave F: V2 adapters RAISE typed errors from query() instead of
    returning in-band empty "successful" results — the guard contract moved
    from metadata["error_type"] == "SECURITY_BLOCKED" to the typed raise
    (same stable error code, now on the exception).
    """
    import pytest

    from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
    from app.services.data_fabric.adapters.geoparquet_adapter import GeoParquetAdapter
    from app.services.data_fabric.errors import SecurityBlockedError

    adapter = GeoParquetAdapter(ConnectionProfile(source_type="geoparquet", endpoint="/etc/passwd"))
    with pytest.raises(SecurityBlockedError) as excinfo:
        adapter.query("/etc/passwd", QuerySpec(limit=5))
    assert excinfo.value.code == "SECURITY_BLOCKED"
