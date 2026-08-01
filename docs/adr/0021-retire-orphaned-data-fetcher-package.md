# Retire the orphaned data_fetcher package

**Status:** accepted

We will **delete** the `app/services/data_fetcher/` package in its entirety — the service,
the `DataSourceAdapter` base + four concrete adapters, `CacheManager`, `PermissionFilter`,
the `DataSourceType` enum, and the `StandardGISData` / `DataQuery` models — together with
`tests/unit/test_postgis_adapter_sql_safety.py`. No replacement is introduced. The
deep-module SQL-safety validators in `postgis_adapter.py` are deleted with the package.

## Context

Architecture-review batch 3 (Candidate #1, 2026-08-01 report) identified `data_fetcher` as
the most striking finding: a self-contained adapter framework that **ships with zero
production callers**. The real fetching in this codebase is done by two separate, deeper
subsystems — the Explorer pipeline (`app/services/explorer/` + `app/adapters/` + the
`GovDataAdapter`) and the Chinese-maps tools (`app/tools/chinese_maps/`) — neither of which
references `data_fetcher`.

A code + history investigation confirmed the orphaning on every dimension:

### 1. Zero callers, current and never-restored

`grep` for every exported symbol (`DataFetcherService`, `DataSourceAdapter`,
`DataSourceType`, `CacheManager`, `PermissionFilter`, `StandardGISData`, `DataQuery`) across
`.py`, `.toml`, `.yaml`, `.json` returns matches **only inside the package itself**. Nothing
constructs `DataFetcherService`, nothing calls `.query()`, nothing imports the base
`DataSourceAdapter` for typing, and the `_adapter_map` is typed `Dict[DataSourceType, Any]`
— so the base class isn't even used as the map's value type. No dynamic or config-file
wiring exists.

### 2. The orphaning was deliberate, not accidental

Commit `07ced80` ("refactor(A8): clean up main.py, remove celery-dependent routes (tasks,
data_fetcher)", 2026-04-05) deleted the package's **only** caller — `app/api/routes/data_fetcher.py`
and its `/query` + cache-invalidate endpoints — as part of a cleanup removing
Celery-dependent routes. The service package was left behind as dead code. It has carried
zero callers in the four months since.

### 3. The seam is shallow — fails the deletion test

`DataSourceAdapter` (`adapters/base.py`) is a 6-line base class with one method,
`query(Dict[str, Any]) -> Any`. Both input and output are untyped `Any` — the seam carries
no domain information. It performs no dispatch (the service does that with a `dict.get`),
no input validation, no output normalization, defines no shared helpers. Deleting the base
class and the four `(... DataSourceAdapter):` declarations changes nothing about the
adapters' behaviour. The deletion test passes cleanly: complexity would not reappear across
callers — it would not reappear at all.

### 4. The service is a shallow facade over delegation

`DataFetcherService` has one public method, `query()`. Of the seven steps its docstring
lists, four are single-line delegations to sibling modules (`CacheManager`,
`PermissionFilter`); the genuinely original logic (format normalization + cache-fallback)
is ~40 lines. It is not deep — the interface (1 method) and the implementation (mostly
delegation) are comparably sized.

### 5. The package actively harms the build

The package is **unimportable** in any environment lacking the `oss2` optional dependency:
`data_fetcher/__init__.py` → `service.py` → `adapters/__init__.py` → `oss_adapter.py` →
`import oss2` is a hard, top-level import chain. The one test that exists
(`test_postgis_adapter_sql_safety.py`) cannot import the package normally and instead
loads `postgis_adapter.py` via `importlib.util.spec_from_file_location` by file path,
performs a string `.replace` on its source to rewrite the base-class import, and `exec`s
the result — reaching far past the interface to surgically avoid the package's broken
import graph. Additionally `permissions.py:28` references `Optional` without importing it,
a latent `NameError`.

### 6. The deep piece (PostGIS validators) guards a dead pattern

The four SQL-safety validators (`_validate_ident`, `_validate_table`,
`_validate_properties`, `_bbox_values`) are genuinely deep — security-audited at `61b8526`
("SQL injection") — and earn their keep *in principle*. But they guard a raw-`text()`-SQL
query pattern over user-named tables/columns. An audit of every live `db_session()` and
`async_db_session()` consumer confirms **no live code performs that pattern**: the five
tools using `_utils.db_session()` (`upload_tools`, `nature_resources`, `report`,
`monitoring_report`, `spatial_tasks`) all use SQLAlchemy ORM queries over fixed models, not
raw `text()` with user-controlled identifiers. The validators are deep but protect nothing
reachable. The decision (taken during architecture-review grilling, 2026-08-01) is YAGNI:
re-introduce from git history if a raw-PostGIS-query need ever appears, rather than carry
speculative security surface for a consumer that does not exist.

## Decision

Delete the package. Delete its test. Introduce no replacement.

The two live fetchers stay as they are:
- `app/services/explorer/` + `app/adapters/` (`BaseDataAdapter` with `discover` /
  `quick_assess` / `fetch` / `parse` and typed domain models) — the deep-module version of
  what `data_fetcher`'s `DataSourceAdapter` was a skeleton of, already wired into the
  Explorer pipeline.
- `app/tools/chinese_maps/` — the live Chinese-provider (Amap/Baidu/Tianditu) integration,
  which is the deeper, async successor to `data_fetcher`'s sync `ThirdPartyAPIAdapter`.

This applies the same bar ADR-0007/0008/0009/0013 established: a seam earns its keep with a
real consumer. Here there are zero, and the deeper replacement was already built beside it.
Deleting it removes confusion about which fetch path is live and removes the `oss2`
broken-import problem for free.

## What we are not doing

- No salvage of the SQL-safety validators into a `utils/sql_safety.py` or similar. They are
  recoverable from git history (commit `61b8526`) if a raw-SQL-over-user-identifiers need
  appears; carrying them speculatively is the YAGNI this decision rejects.
- No replacement adapter framework. The two live fetchers already exist and do not need a
  shared base.
- No removal of `oss2` from dependency manifests — `oss2` is not listed in `pyproject.toml`
  or `requirements.txt` (it was an undeclared optional import), so there is nothing to
  remove there; the broken top-level import simply ceases to exist.

## Relationship to prior ADRs

Consistent with the "seam earns its keep with a real consumer" line: ADR-0013 deleted a
zero-caller dispatch seam; ADR-0009 rejected introducing an interface for a non-divergent
concern; ADR-0008 extracted a module only once a 4th caller was on the horizon. This ADR
extends that discipline to the package level: a whole adapter framework with zero callers
goes, especially when its deeper replacement is already live.

## Trigger to revisit

Reopen only if **a genuine need for a unified, source-type-polymorphic data fetcher
reappears** — i.e. a new caller that wants to query PostGIS / OSS / local files / a
third-party API through one uniform interface, *and* the existing live fetchers
(`explorer/`, `chinese_maps/`) cannot absorb it. At that point the design lesson from this
ADR applies: build the base interface with typed domain models (as `app/adapters/base.py`
already does), not a 6-line `Any→Any` stub, and wire at least two real adapters from day
one — "one adapter = hypothetical seam."

A re-suggestion framed as "restore data_fetcher for symmetry" or "we might need OSS/PostGIS
fetching someday" does not meet this bar unless it names the concrete caller and shows why
the live fetchers cannot serve it.
