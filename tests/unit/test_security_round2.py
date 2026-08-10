"""Regression tests for round-2 security fixes:

- SEC-04: explorer GovDataAdapter must SSRF-block private/loopback/metadata
  URLs before fetching (source.url is attacker-influenced from remote
  platform search responses).
- SEC-06: PostGIS where-pushdown must be a real parameterized filter (not a
  silent no-op string literal), and query failures must return EMPTY results
  with explicit errors — never fabricated sample features.
- SEC-08: raster tile route must reject raster paths outside the allowed data
  roots.
"""
import pytest

from app.services.data_fabric.adapters.postgis_adapter import _parse_safe_where


# ---------------------------------------------------------------------------
# SEC-04 — explorer SSRF
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gov_adapter_fetch_blocks_private_url():
    from app.adapters.gov.gov_data_adapter import GovDataAdapter
    from app.adapters.base import DataSource

    adapter = GovDataAdapter()
    for bad_url in [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:6379/",
        "http://10.0.0.5:5432/",
        "http://localhost:3000/",
        "http://metadata.google.internal/",
    ]:
        source = DataSource(
            id="x", name="n", url=bad_url, format="csv",
            description="", source_type="gov",
        )
        with pytest.raises(ValueError, match="blocked|Unsafe|invalid"):
            await adapter.fetch(source)


@pytest.mark.asyncio
async def test_gov_adapter_fetch_allows_public_url(monkeypatch):
    from app.adapters.gov.gov_data_adapter import GovDataAdapter
    from app.adapters.base import DataSource

    adapter = GovDataAdapter()

    class FakeRaw:
        data = b"a,b\n1,2\n"
        content_type = "text/csv"
        encoding = "utf-8"

    class FakeContent:
        async def iter_chunked(self, n):
            yield FakeRaw.data

    class FakeResp:
        status = 200
        headers = {}
        content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def fake_get(self, url, **kwargs):
        return FakeResp()

    import aiohttp

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    source = DataSource(
        id="x", name="n", url="https://data.example.gov/file.csv", format="csv",
        description="", source_type="gov",
    )
    raw = await adapter.fetch(source)
    assert raw.data == b"a,b\n1,2\n"


# ---------------------------------------------------------------------------
# SEC-06 — PostGIS where pushdown
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "expr, expected_sql, expected_param",
    [
        ("type = 'commercial'", '"type" = %s', "commercial"),
        ("age >= 30", '"age" >= %s', 30),
        ("score > 4.5", '"score" > %s', 4.5),
        ("name LIKE 'shop%'", '"name" LIKE %s', "shop%"),
        ("active = true", '"active" = %s', True),
        ("deleted = null", '"deleted" = %s', None),
        ("city != 'Beijing'", '"city" != %s', "Beijing"),
    ],
)
def test_parse_safe_where_valid(expr, expected_sql, expected_param):
    sql, params = _parse_safe_where(expr)
    assert sql == expected_sql
    assert params == [expected_param]


@pytest.mark.parametrize(
    "expr",
    [
        "name LIKE '%Street%'",   # % wildcards with 's' — reviewer fix
        "name LIKE '%s'",
        "name LIKE 'S%'",
        "name LIKE '%S%'",
    ],
)
def test_parse_safe_where_accepts_like_wildcards(expr):
    """Reviewer fix: LIKE patterns containing %s/%S are legitimate wildcards,
    not placeholder injection (values are always bound parameters)."""
    sql, params = _parse_safe_where(expr)
    assert sql == '"name" LIKE %s'
    assert params == [expr.split("'")[1]]


@pytest.mark.parametrize(
    "expr",
    [
        "1=1",                      # injection attempt
        "type = 'x' OR 1=1",        # conjunction
        "name; DROP TABLE users",   # statement injection
        "type=(SELECT 1)",          # unquoted subquery
        "x = (SELECT 1)",           # unquoted subquery with spaces
        "col == 5",                 # invalid operator
        "",                         # empty
        "= 5",                      # missing column
        "col >",                    # missing value
        "co l > 5",                 # space in identifier
        "x = 1 UNION SELECT 2",     # unquoted union
        "x = DROP",                 # unquoted keyword
    ],
)
def test_parse_safe_where_rejects_unsafe(expr):
    with pytest.raises(ValueError):
        _parse_safe_where(expr)


def test_parse_safe_where_binds_literal_percent_s():
    """'name LIKE %s' (bare, unquoted) is bound as the STRING '%s' — values are
    always parameters, so this is safe, not injection. It must parse (reviewer
    fix: previously rejected via the %S token check)."""
    sql, params = _parse_safe_where("name LIKE %s")
    assert sql == '"name" LIKE %s'
    assert params == ["%s"]


def test_postgis_query_failure_returns_empty_not_fabricated(monkeypatch):
    """SEC-06: a failed PostGIS query must return EMPTY features with an
    explicit error — never a fabricated Beijing sample polygon."""
    from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter
    from app.schemas.data_fabric_schema import QuerySpec

    adapter = PostGISAdapter.__new__(PostGISAdapter)  # bypass __init__

    def _boom(self, conn):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(adapter, "_connection_context", lambda: _boom(None))
    monkeypatch.setattr(adapter, "_sanitize_identifier", lambda ds: ("public", ds))

    result = adapter.query("some_table", QuerySpec(limit=10))
    assert result.features == [], (
        "SEC-06 regression: query failure returned fabricated features"
    )
    assert result.total_count == 0
    assert result.metadata.get("success") is False
    assert "error_hint" in result.metadata


# ---------------------------------------------------------------------------
# SEC-08 — raster tile path validation
# ---------------------------------------------------------------------------

def test_raster_path_validation_rejects_outside_roots(tmp_path, monkeypatch):
    """SEC-08: a ref pointing outside the data dir must be rejected before
    rasterio.open."""
    from app.utils.path import validate_data_path

    data_root = tmp_path / "data"
    data_root.mkdir()
    outside = tmp_path / "outside_secret.tif"
    outside.write_bytes(b"x")

    with pytest.raises(ValueError):
        validate_data_path(str(outside), str(data_root))

    # A path inside the root is fine.
    inside = data_root / "raster" / "ok.tif"
    inside.parent.mkdir()
    inside.write_bytes(b"x")
    safe = validate_data_path(str(inside), str(data_root))
    assert safe.endswith("ok.tif")
