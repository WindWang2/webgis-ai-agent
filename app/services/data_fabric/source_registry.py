"""ads-v1 declarative source registry (DS1, ADR-0171, gaps A1/A2).

"Adding a data source" becomes "adding one YAML file" — no Python changes.

- ``config/sources/*.yaml``: one file per source (or one grouped file).
  Schema below is validated with pydantic; **any validation failure raises
  ``SourceRegistryError`` with the offending file** — silent skipping is
  forbidden (task book §3-DS1).
- Secrets are **never stored in YAML**: credential values must be ``${ENV_VAR}``
  references (or omitted); plaintext credential values fail validation
  (DS9 hardening pulls the same lever from day one).
- ``reload_if_changed()``: mtime-based hot reload — new/changed sources take
  effect without restart.
- Fabric bridge: ``to_profile()`` builds a fabric ``ConnectionProfile`` and
  ``sync_source()`` / ``sync_all()`` run the real adapter ``sync()`` so
  config-declared sources become catalog-visible exactly like code-registered
  ones (end-to-end demo for the "no code change" acceptance).

Protocol → adapter mapping:
- fabric-native protocols (postgis / ogc_api / wfs / wms / arcgis / stac /
  geoparquet / flatgeobuf / pmtiles / s3) resolve through
  ``data_fabric.registry.get_registry()``;
- ads-v1 additions (local_file / geopackage / cog / stats_api) are registered
  in ``registry.py`` by this module's import;
- ``gov_portal`` sources are consumed by the explorer-line
  ``GovDataAdapter`` (which now reads this registry instead of its hardcoded
  ``PLATFORMS`` dict).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

#: Directory holding ``*.yaml`` source declarations (repo layout convention).
DEFAULT_SOURCES_DIR = Path("config") / "sources"

#: Fabric-native protocols resolvable through the adapter registry.
FABRIC_PROTOCOLS = frozenset(
    {"postgis", "ogc_api", "wfs", "wms", "arcgis", "stac", "geoparquet", "flatgeobuf", "pmtiles", "s3"}
)
#: ads-v1 new protocols (adapters registered in data_fabric.registry).
ADS_PROTOCOLS = frozenset({"local_file", "geopackage", "cog", "stats_api"})
#: Explorer-line protocols (no GeospatialDataSourceAdapter; declared only).
EXPLORER_PROTOCOLS = frozenset({"gov_portal"})
KNOWN_PROTOCOLS = FABRIC_PROTOCOLS | ADS_PROTOCOLS | EXPLORER_PROTOCOLS


class SourceRegistryError(RuntimeError):
    """Loud failure for invalid source declarations (never silently skipped)."""

    def __init__(self, file: Path, message: str):
        self.file = file
        super().__init__(f"source registry: {file}: {message}")


class PushdownCapabilities(BaseModel):
    """下推能力声明（DS3 计划器据此选步骤；声明不实由 lint 运行时抽检）。"""

    model_config = ConfigDict(extra="forbid")

    bbox: bool = False
    cql: bool = False
    aggregation: bool = False
    time_filter: bool = False
    projection: bool = False
    pagination: bool = False


class QuotaDecl(BaseModel):
    """配额与限流（DS8 成本治理消费；None = 未声明，不是 0）。"""

    model_config = ConfigDict(extra="allow")

    requests_per_minute: Optional[int] = None
    daily_max: Optional[int] = None
    max_bytes_per_request: Optional[int] = None


class TemporalCoverageDecl(BaseModel):
    model_config = ConfigDict(extra="allow")

    start: Optional[str] = None
    end: Optional[str] = None


class AuthDecl(BaseModel):
    """认证声明。``env_keys`` 列出凭证所需的 env 变量名（值绝不入 YAML）。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["none", "api_key", "token", "basic", "oauth2"] = "none"
    env_keys: List[str] = Field(default_factory=list)
    placement: Literal["header", "query", "url"] = "header"
    header_name: Optional[str] = None
    query_param: Optional[str] = None


class FallbackRule(BaseModel):
    """One conditional fallback hop (DS4, ADR-0174).

    ``on`` lists the triggers that activate this hop — a superset of the
    executor's vocabulary; an empty list means "any failure". ``comparable``
    overrides the automatic comparability decision when the operator knows
    the two sources agree (or disagree) on granularity/coverage.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    on: List[Literal[
        "timeout", "5xx", "429", "quota", "empty_result", "truncated",
        "schema_mismatch", "circuit_open", "probe_failed", "other",
    ]] = Field(default_factory=list)
    comparable: Optional[bool] = None
    note: str = ""


class DatasetDecl(BaseModel):
    """内联数据集声明（无网络也可入目录；freshness/coverage 为声明值）。"""

    model_config = ConfigDict(extra="allow")

    dataset_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    data_type: str = "vector"
    fields: List[Dict[str, Any]] = Field(default_factory=list)
    bbox: Optional[List[float]] = None
    granularity: Optional[str] = None
    license: Optional[str] = None


class SourceDefinition(BaseModel):
    """One configured data source (config/sources/*.yaml)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    name: str
    protocol: str  # KNOWN_PROTOCOLS member (validated post-parse for loud msg)
    endpoint: str = ""
    description: str = ""

    # Capability declaration (DS1.2)
    pushdown: PushdownCapabilities = Field(default_factory=PushdownCapabilities)
    operations: List[str] = Field(default_factory=list)
    quota: QuotaDecl = Field(default_factory=QuotaDecl)
    geographic_coverage: List[str] = Field(default_factory=list)
    temporal_coverage: TemporalCoverageDecl = Field(default_factory=TemporalCoverageDecl)
    license: str = "unknown"
    freshness: Dict[str, Any] = Field(default_factory=dict)

    # Auth: env references only — plaintext secrets fail validation.
    auth: AuthDecl = Field(default_factory=AuthDecl)
    verified: bool = False  # §0.5: unverified sources are down-ranked & disclosed
    # Declared cost/order priority (lower = earlier in registry-driven local
    # chains, DS4.4); default 100 keeps declaration order effectively.
    priority: int = 100

    options: Dict[str, Any] = Field(default_factory=dict)
    datasets: List[DatasetDecl] = Field(default_factory=list)
    # DS4 fallback chain: bare ids (any-trigger) or conditional FallbackRules.
    fallbacks: List[Any] = Field(default_factory=list)

    def fabric_profile(self) -> Dict[str, Any]:
        """ConnectionProfile kwargs (credentials resolved from env, or missing)."""
        options = dict(self.options)
        # YAML top-level `datasets:` is SourceDefinition.datasets; adapters
        # (StatsApiAdapter._datasets_decl) read options.datasets. Copy so
        # declared worldbank/gbif/overpass datasets are queryable. Do not
        # clobber an explicit options.datasets.
        if self.datasets and not options.get("datasets"):
            options["datasets"] = [
                d.model_dump() if hasattr(d, "model_dump") else dict(d)
                for d in self.datasets
            ]
        return {
            "id": self.source_id,
            "source_type": self.protocol,
            "name": self.name,
            "endpoint_url": self.endpoint,
            "options": options,
            "allow_private": False,
        }

    def normalized_fallbacks(self) -> List[FallbackRule]:
        """Bare id strings → any-trigger rules; dicts/rules validated loudly."""
        out: List[FallbackRule] = []
        for fb in self.fallbacks:
            if isinstance(fb, str):
                out.append(FallbackRule(source_id=fb))
            elif isinstance(fb, FallbackRule):
                out.append(fb)
            elif isinstance(fb, dict):
                try:
                    # YAML 1.1 gotcha: a bare `on:` key parses as boolean True —
                    # normalise it back so the natural syntax stays usable.
                    fb = {("on" if k is True else k): v for k, v in fb.items()}
                    if isinstance(fb.get("on"), list):
                        # YAML parses bare 429/5xx as ints — coerce to the
                        # string trigger vocabulary.
                        fb["on"] = [str(t) for t in fb["on"]]
                    out.append(FallbackRule(**fb))
                except ValidationError as e:
                    raise ValueError(f"invalid fallback rule {fb!r}: {e}") from e
            else:
                raise ValueError(f"invalid fallback entry: {fb!r} (id string or {{source_id, on, ...}})")
        return out


class _LoadedSource:
    """A parsed YAML file plus its mtime (for hot reload bookkeeping)."""

    __slots__ = ("path", "mtime", "source")

    def __init__(self, path: Path, source: SourceDefinition):
        self.path = path
        self.mtime = path.stat().st_mtime
        self.source = source


class SourceRegistryService:
    """Loads, validates and serves ``config/sources/*.yaml`` declarations."""

    def __init__(self, sources_dir: Optional[Path] = None):
        self._dir = Path(sources_dir) if sources_dir else None
        self._loaded: Dict[str, _LoadedSource] = {}  # source_id → entry
        self._snapshots: Dict[Path, float] = {}

    # -- loading ---------------------------------------------------------------

    def _resolve_dir(self) -> Optional[Path]:
        if self._dir is not None:
            return self._dir
        base = Path(__file__).resolve().parents[3]
        candidate = base / DEFAULT_SOURCES_DIR
        return candidate if candidate.exists() else None

    def load(self) -> "SourceRegistryService":
        """(Re)load every YAML in the sources dir. Loud on any failure."""
        directory = self._resolve_dir()
        self._loaded = {}
        self._snapshots = {}
        if directory is None:
            logger.warning("[SourceRegistry] sources dir missing: %s", DEFAULT_SOURCES_DIR)
            return self
        files = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
        for path in files:
            source = self._parse_file(path)
            if source.source_id in self._loaded:
                raise SourceRegistryError(
                    path,
                    f"duplicate source_id '{source.source_id}' "
                    f"(first declared in {self._loaded[source.source_id].path.name})",
                )
            self._loaded[source.source_id] = _LoadedSource(path, source)
            self._snapshots[path] = path.stat().st_mtime
        return self

    def _parse_file(self, path: Path) -> SourceDefinition:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise SourceRegistryError(path, f"invalid YAML: {e}") from e
        if not isinstance(raw, dict):
            raise SourceRegistryError(path, "top level must be a mapping")
        try:
            source = SourceDefinition(**raw)
        except ValidationError as e:
            raise SourceRegistryError(path, e.errors()[0].get("msg", str(e))) from e
        if source.protocol not in KNOWN_PROTOCOLS:
            raise SourceRegistryError(
                path,
                f"unknown protocol '{source.protocol}' (known: {sorted(KNOWN_PROTOCOLS)})",
            )
        self._validate_secrets(path, source)
        try:
            source.normalized_fallbacks()
        except ValueError as e:
            raise SourceRegistryError(path, str(e)) from e
        return source

    @staticmethod
    def _validate_secrets(path: Path, source: SourceDefinition) -> None:
        """No plaintext credentials: auth values must come from env keys."""
        for key in source.auth.env_keys:
            if not re_env_name(key):
                raise SourceRegistryError(path, f"auth.env_keys entry '{key}' is not a valid env var name")
        # options must not smuggle credential-looking plaintext
        for opt_key, value in source.options.items():
            lowered = str(opt_key).lower()
            if any(tok in lowered for tok in ("secret", "password", "token", "api_key")):
                if isinstance(value, str) and not value.startswith("${"):
                    raise SourceRegistryError(
                        path,
                        f"options.{opt_key} looks like a credential and must use ${{ENV_VAR}} "
                        "(no plaintext secrets in the registry)",
                    )

    def reload_if_changed(self) -> bool:
        """Hot reload: true if any file changed/added/removed and reload ran."""
        directory = self._resolve_dir()
        if directory is None:
            return False
        files = set(directory.glob("*.yaml")) | set(directory.glob("*.yml"))
        if set(self._snapshots) != files:
            self.load()
            return True
        for path in files:
            if path.stat().st_mtime != self._snapshots.get(path):
                self.load()
                return True
        return False

    # -- access ----------------------------------------------------------------

    def get(self, source_id: str) -> SourceDefinition:
        self.reload_if_changed()
        entry = self._loaded.get(source_id)
        if entry is None:
            raise SourceRegistryError(
                self._resolve_dir() or Path("<none>"),
                f"unknown source_id '{source_id}' (known: {sorted(self._loaded)})",
            )
        return entry.source

    def list_sources(self, protocol: Optional[str] = None) -> List[SourceDefinition]:
        self.reload_if_changed()
        sources = [e.source for e in self._loaded.values()]
        if protocol:
            sources = [s for s in sources if s.protocol == protocol]
        return sources

    def gov_platforms(self) -> Dict[str, Dict[str, Any]]:
        """GovDataAdapter 兼容视图（PLATFORMS 迁移后由该 adapter 消费）。"""
        platforms: Dict[str, Dict[str, Any]] = {}
        for source in self.list_sources(protocol="gov_portal"):
            platforms[source.source_id] = {
                "name": source.name,
                "search_url": source.endpoint.rstrip("/") + "/search" if source.endpoint else "",
                "base_url": source.endpoint,
            }
        return platforms

    # -- fabric bridge -----------------------------------------------------------

    def to_profile(self, source_id: str):
        """Configured source → fabric ConnectionProfile (protocol must map)."""
        from app.schemas.data_fabric_schema import ConnectionProfile

        source = self.get(source_id)
        if source.protocol not in FABRIC_PROTOCOLS | ADS_PROTOCOLS:
            raise SourceRegistryError(
                Path(f"<{source_id}>"),
                f"protocol '{source.protocol}' has no fabric adapter; "
                "it is declared for explorer-line consumption only",
            )
        return ConnectionProfile(**source.fabric_profile())

    def build_adapter(self, source_id: str):
        """Configured source → bound adapter instance (auto-registration path)."""
        from app.services.data_fabric.registry import build_adapter as fabric_build

        profile = self.to_profile(source_id)
        return fabric_build(profile)

    def sync_source(self, source_id: str, owner: Optional[str] = None) -> Dict[str, Any]:
        """Run the real adapter sync for one configured source (loud failures)."""
        adapter = self.build_adapter(source_id)
        return adapter.sync(owner=owner)

    def sync_all(self, owner: Optional[str] = None) -> Dict[str, Any]:
        """Sync every fabric-mappable source; per-source failures are reported,
        never swallowed silently (collect → return in the report)."""
        report: Dict[str, Any] = {"synced": {}, "failed": {}}
        for source in self.list_sources():
            if source.protocol not in FABRIC_PROTOCOLS | ADS_PROTOCOLS:
                continue
            try:
                result = self.sync_source(source.source_id, owner=owner)
                report["synced"][source.source_id] = result
            except Exception as e:  # noqa: BLE001 — report, don't crash the loop
                report["failed"][source.source_id] = str(e)
        return report


def re_env_name(name: str) -> bool:
    import re

    return bool(re.match(r"^[A-Z_][A-Z0-9_]*$", name))


def resolve_env_ref(value: str) -> Optional[str]:
    """``${ENV_NAME}`` → env value; missing env → None (loud at consumer)."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1])
    return value


#: Module-level singleton (hot reload keeps instances fresh).
source_registry_service = SourceRegistryService()


__all__ = [
    "SourceDefinition",
    "SourceRegistryError",
    "SourceRegistryService",
    "PushdownCapabilities",
    "QuotaDecl",
    "AuthDecl",
    "DatasetDecl",
    "KNOWN_PROTOCOLS",
    "FABRIC_PROTOCOLS",
    "ADS_PROTOCOLS",
    "DEFAULT_SOURCES_DIR",
    "source_registry_service",
    "resolve_env_ref",
]
