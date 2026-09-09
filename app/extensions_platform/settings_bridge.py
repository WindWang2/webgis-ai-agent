"""设置桥：把全局 Settings 的扩展相关字段解析为 HostPolicy。

解析全部 fail closed：非法 JSON / 未知权限词在构建期抛
ExtensionPlatformError（含 typed diagnostic），而不是静默忽略半份配置。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError
from .host import HostPolicy
from .permissions import parse_grants_config


def host_policy_from_settings() -> HostPolicy:
    from app.core.config import settings

    roots = [
        Path(p.strip())
        for p in settings.EXTENSIONS_DIRS.split(os.pathsep)
        if p.strip()
    ]
    allow = frozenset(p.strip() for p in settings.EXTENSIONS_ALLOW.split(",") if p.strip())
    block = frozenset(p.strip() for p in settings.EXTENSIONS_BLOCK.split(",") if p.strip())
    builtin_ids = frozenset(
        p.strip() for p in settings.EXTENSIONS_BUILTIN_IDS.split(",") if p.strip()
    )
    grants = parse_grants_config(settings.EXTENSION_PERMISSION_GRANTS)
    feature_flags = _parse_json_dict(
        settings.EXTENSION_FEATURE_FLAGS, "EXTENSION_FEATURE_FLAGS"
    )
    extension_settings = _parse_json_dict(
        settings.EXTENSION_SETTINGS_JSON, "EXTENSION_SETTINGS_JSON"
    )
    # Round-1 审计 minor11：feature flag 只接受严格布尔（JSON "false" 字符串
    # 曾被 bool() 变成 True）；非 object 的扩展设置显式拒绝而非静默丢 {}。
    from .diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError

    for key, value in feature_flags.items():
        if not isinstance(value, bool):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"EXTENSION_FEATURE_FLAGS[{key!r}] must be a JSON boolean, "
                    f"got {type(value).__name__}",
                )
            )
    for key, value in extension_settings.items():
        if not isinstance(value, dict):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"EXTENSION_SETTINGS_JSON[{key!r}] must be a JSON object, "
                    f"got {type(value).__name__}",
                )
            )
    return HostPolicy(
        roots=tuple(roots),
        allow=allow,
        block=block,
        builtin_ids=builtin_ids,
        grants=grants,
        feature_flags=dict(feature_flags),
        extension_settings={str(k): dict(v) for k, v in extension_settings.items()},
        allow_local_untrusted_activation=settings.EXTENSIONS_ACTIVATE_UNTRUSTED,
        secrets=_parse_secrets(settings.EXTENSION_SECRETS_JSON),
        network_allow=parse_network_allow(settings.EXTENSION_NETWORK_ALLOW),
        artifact_roots=tuple(
            Path(p.strip()).resolve()
            for p in settings.EXTENSION_ARTIFACT_ROOTS.split(os.pathsep)
            if p.strip()
        ),
        trusted_publishers=parse_trusted_publishers(settings.EXTENSION_TRUSTED_PUBLISHERS),
        trust_signed=settings.EXTENSIONS_TRUST_SIGNED,
        allow_unsigned_dev=settings.EXTENSIONS_ALLOW_UNSIGNED_DEV,
        max_worker_crashes=_parse_max_worker_crashes(settings.EXTENSIONS_MAX_WORKER_CRASHES),
        # ── V3（ADR-0119）────────────────────────────────────────────
        trust_store=_load_trust_store(settings.EXTENSION_TRUST_STORE_PATH),
        isolation_backend=_parse_isolation_backend(settings.EXTENSIONS_ISOLATION_BACKEND),
        stream_window=_parse_bounded_int(
            settings.EXTENSION_STREAM_WINDOW, "EXTENSION_STREAM_WINDOW", 1, 1024
        ),
        max_stream_events=_parse_bounded_int(
            settings.EXTENSION_MAX_STREAM_EVENTS, "EXTENSION_MAX_STREAM_EVENTS", 1, 1_000_000
        ),
        version_pins=parse_version_pins(settings.EXTENSION_VERSION_PIN),
    )


def _parse_secrets(raw: str) -> dict[str, dict[str, str]]:
    """EXTENSION_SECRETS_JSON：{ext_id: {ref: value}}；形状 fail closed。"""
    data = _parse_json_dict(raw, "EXTENSION_SECRETS_JSON")
    secrets: dict[str, dict[str, str]] = {}
    for ext_id, refs in data.items():
        if not isinstance(refs, dict):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"EXTENSION_SECRETS_JSON[{ext_id!r}] must be a JSON object of ref->value",
                )
            )
        cleaned: dict[str, str] = {}
        for ref, value in refs.items():
            if not isinstance(value, str):
                raise ExtensionPlatformError(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.MANIFEST_PARSE_FAILED,
                        f"EXTENSION_SECRETS_JSON[{ext_id!r}][{ref!r}] must be a string",
                    )
                )
            cleaned[str(ref)] = value
        secrets[str(ext_id)] = cleaned
    return secrets


def parse_network_allow(raw: str) -> dict[str, frozenset[str]]:
    """EXTENSION_NETWORK_ALLOW："id:host1,host2;id2:*" → {id: frozenset(hosts)}。

    host 形状：小写 host / host:port 的 host 部分 / "*"（全部放行）。
    空条目 = 该扩展无任何出网允许（broker 默认 deny 不受影响）。
    """
    allowed: dict[str, frozenset[str]] = {}
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        ext_id, _, hosts_csv = chunk.partition(":")
        ext_id = ext_id.strip()
        if not ext_id:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"EXTENSION_NETWORK_ALLOW entry {chunk!r} lacks extension id",
                )
            )
        # 先按原始形态拒绝非法条目（path/space/前导冒号），再把 host:port
        # 规约为 host 部分（broker 按 URL hostname 匹配，端口维度无法强制；
        # 静默保留 :port 会让条目永不命中——Round-1 MINOR-5 footgun）。
        hosts = set()
        for raw in hosts_csv.split(","):
            entry = raw.strip().lower()
            if not entry:
                continue
            if "/" in entry or " " in entry or entry.startswith(":"):
                raise ExtensionPlatformError(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.MANIFEST_PARSE_FAILED,
                        f"EXTENSION_NETWORK_ALLOW host {entry!r} is not a plain host "
                        "(use host or host:port, or '*' for all)",
                    )
                )
            hosts.add(entry.partition(":")[0] or entry)
        hosts = frozenset(hosts)
        if not hosts:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"EXTENSION_NETWORK_ALLOW entry {chunk!r} lists no hosts",
                )
            )
        existing = allowed.get(ext_id, frozenset())
        allowed[ext_id] = existing | hosts
    return allowed


def parse_trusted_publishers(raw: str) -> dict[str, Path]:
    """EXTENSION_TRUSTED_PUBLISERS："key_id:path,..." → {key_id: Path}。"""
    publishers: dict[str, Path] = {}
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        key_id, sep, path = chunk.partition(":")
        if not sep or not key_id.strip() or not path.strip():
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"EXTENSION_TRUSTED_PUBLISHERS entry {chunk!r} must be key_id:path",
                )
            )
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key_id.strip()):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"publisher key_id {key_id!r} must be a lowercase identifier",
                )
            )
        publishers[key_id.strip()] = Path(path.strip())
    return publishers


def _parse_max_worker_crashes(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                f"EXTENSIONS_MAX_WORKER_CRASHES must be an integer, got {raw!r}",
            )
        ) from exc
    if value < 1 or value > 10:
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                f"EXTENSIONS_MAX_WORKER_CRASHES must be in [1, 10], got {value}",
            )
        )
    return value


def _load_trust_store(raw: str) -> Any:
    """EXTENSION_TRUST_STORE_PATH → TrustStore；空 = None（V2 语义不变）。

    解析 fail closed：文件不可读 / 形状非法 → typed（宁可宿主起不来，
    不可带着半份信任根运行）。
    """
    path = (raw or "").strip()
    if not path:
        return None
    from .trust_store import TrustStore

    return TrustStore.load(Path(path))


_ISOLATION_BACKENDS = frozenset({"process", "bubblewrap"})


def _parse_isolation_backend(raw: str) -> str:
    value = (raw or "process").strip().lower()
    if value not in _ISOLATION_BACKENDS:
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                f"EXTENSIONS_ISOLATION_BACKEND must be one of "
                f"{sorted(_ISOLATION_BACKENDS)}, got {raw!r}",
            )
        )
    return value


def parse_version_pins(raw: str) -> dict[str, str]:
    """EXTENSION_VERSION_PIN："id==1.2.0;id2==0.3.1" → {id: version}。"""
    from .api_version import is_version

    pins: dict[str, str] = {}
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        ext_id, sep, version = chunk.partition("==")
        ext_id = ext_id.strip()
        version = version.strip()
        if not sep or not ext_id or not version:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"EXTENSION_VERSION_PIN entry {chunk!r} must be id==version",
                )
            )
        if not is_version(version):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"EXTENSION_VERSION_PIN version {version!r} is not X.Y[.Z] semver",
                )
            )
        if ext_id in pins and pins[ext_id] != version:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_PARSE_FAILED,
                    f"EXTENSION_VERSION_PIN has conflicting entries for {ext_id!r}",
                )
            )
        pins[ext_id] = version
    return pins


def _parse_bounded_int(raw: Any, field_name: str, lo: int, hi: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                f"{field_name} must be an integer, got {raw!r}",
            )
        ) from exc
    if value < lo or value > hi:
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                f"{field_name} must be in [{lo}, {hi}], got {value}",
            )
        )
    return value


def _parse_json_dict(raw: str, field_name: str) -> dict[str, Any]:
    from .diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError

    if not (raw or "").strip():
        return {}
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                f"{field_name} is not valid JSON: {exc}",
            )
        ) from exc
    if not isinstance(value, dict):
        raise ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.MANIFEST_PARSE_FAILED,
                f"{field_name} must be a JSON object",
            )
        )
    return value
