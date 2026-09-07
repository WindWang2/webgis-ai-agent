"""设置桥：把全局 Settings 的扩展相关字段解析为 HostPolicy。

解析全部 fail closed：非法 JSON / 未知权限词在构建期抛
ExtensionPlatformError（含 typed diagnostic），而不是静默忽略半份配置。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

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
    for key, value in feature_flags.items():
        if not isinstance(value, bool):
            raise ValueError(
                f"EXTENSION_FEATURE_FLAGS[{key!r}] must be a JSON boolean, "
                f"got {type(value).__name__}"
            )
    for key, value in extension_settings.items():
        if not isinstance(value, dict):
            raise ValueError(
                f"EXTENSION_SETTINGS_JSON[{key!r}] must be a JSON object, "
                f"got {type(value).__name__}"
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
    )


def _parse_json_dict(raw: str, field_name: str) -> dict[str, Any]:
    if not (raw or "").strip():
        return {}
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return value
