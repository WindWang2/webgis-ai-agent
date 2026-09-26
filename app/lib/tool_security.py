"""Tool credential/permission presence bridge (F06, ADR-0215 候选).

#1402 立起了执行闸（``requires_credentials`` / ``required_permission`` 对照
ContextVar），但生产侧没有任何供给：``present_tool_credentials`` 全仓零调
用，凭证也没有任何可查询的元数据面。本模块补上 **presence-only** 的供给
契约：

- 真实 secret 永远留在部署侧 secret manager —— 本桥只回答「某凭证 id 在
  当前部署/作用域是否配置」。presence 事实（id/kind/expiry/owner-scope）
  允许进入资格上下文与证据；secret 材料永不过桥。
- 默认 provider 从环境读取（``GIS_TOOL_CREDENTIALS`` / ``GIS_TOOL_PERMISSIONS``）；
  部署可用 :func:`set_credential_presence_provider` 注入真实 provider
  （KMS/vault 适配器）—— 本模块是唯一注入点，不建第二套凭证体系。
- 全部读路径 fail-open（provider 异常 → 空 presence），绝不阻断 dispatch。
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Dict, Optional, Protocol

#: presence 面的有界预算（声明面最大凭证/权限条目数；超出截断披露）。
MAX_CREDENTIAL_ENTRIES = 64

#: 环境变量名（presence-only 声明；不含任何 secret 值）。
CREDENTIALS_ENV = "GIS_TOOL_CREDENTIALS"
PERMISSIONS_ENV = "GIS_TOOL_PERMISSIONS"

_STR_MAX = 128


@dataclass(frozen=True)
class CredentialPresence:
    """凭证 presence 事实（安全投影：永无 secret 材料）。

    ``credential_id`` 是声明面引用（如 ``smtp`` / ``upstream_tile``），
    不是 secret 值；``expires_at`` 为 ISO-8601 字符串或 ""（未声明）；
    ``owner_scope`` 为部署/租户作用域标签（非用户身份原文）。
    """

    credential_id: str
    kind: str = ""
    expires_at: str = ""
    owner_scope: str = ""
    source: str = "env"

    def to_dict(self) -> Dict[str, str]:
        return {
            "credential_id": self.credential_id[:_STR_MAX],
            "kind": self.kind[:32],
            "expires_at": self.expires_at[:32],
            "owner_scope": self.owner_scope[:64],
            "source": self.source[:32],
        }


class CredentialPresenceProvider(Protocol):
    """presence-only provider 契约（实现方绝不抛、绝不返回 secret）。"""

    def available_credentials(self) -> Dict[str, CredentialPresence]:
        """当前作用域已配置的凭证 presence（bounded ≤ MAX_CREDENTIAL_ENTRIES）。"""
        ...


def _env_truthy(name: str, default: str) -> bool:
    raw = (os.environ.get(name) or default).strip().lower()
    return raw not in ("0", "false", "off", "no")


#: 已知 secret 前缀（启发式：操作员误把 secret 放进 presence 槽位时拒收，
#: 防止值面流入资格解释面 —— review P3）。
_SECRET_LIKE_PREFIXES = (
    "sk-", "ghp_", "gho_", "github_pat_", "xoxb-", "xoxp-", "AKIA",
    "glpat-", "shpat_", "sq0atp-", "-----BEGIN",
)


def _looks_like_secret(value: str) -> bool:
    """值形启发式：已知 secret 前缀或高熵长串（≥40 位混合字符）拒收。"""
    if not value:
        return False
    if value.startswith(_SECRET_LIKE_PREFIXES):
        return True
    if len(value) >= 40:
        has_lower = any(c.islower() for c in value)
        has_upper = any(c.isupper() for c in value)
        has_digit = any(c.isdigit() for c in value)
        if has_lower and (has_upper or has_digit):
            return True
    return False


class EnvCredentialPresenceProvider:
    """环境驱动 presence provider（``GIS_TOOL_CREDENTIALS``）。

    条目形态：``id`` 或 ``id:kind`` 或 ``id:kind:expiry_iso``，逗号分隔，
    ≤64 项。**值面只允许元数据** —— 部署侧把真实 secret 放在 secret
    manager，环境里只登记「该 id 已配置」的事实；形似 secret 的槽位值
    （known 前缀 / 高熵长串）整条拒收（fail-closed：宁可丢 presence
    披露，不可让值面流入资格解释面）。
    """

    def __init__(self, raw: str = "", *, source: str = "env"):
        self._source = source
        self._presences: Dict[str, CredentialPresence] = {}
        for entry in (raw or os.environ.get(CREDENTIALS_ENV, "")).split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(":")
            cid = parts[0].strip()[:_STR_MAX]
            if not cid or cid in self._presences:
                continue
            if len(self._presences) >= MAX_CREDENTIAL_ENTRIES:
                break
            kind = (parts[1].strip() if len(parts) > 1 else "")[:32]
            expires_at = (parts[2].strip() if len(parts) > 2 else "")[:32]
            owner_scope = (parts[3].strip() if len(parts) > 3 else "")[:64]
            if _looks_like_secret(cid) or _looks_like_secret(kind) \
                    or _looks_like_secret(expires_at) \
                    or _looks_like_secret(owner_scope):
                continue
            self._presences[cid] = CredentialPresence(
                credential_id=cid,
                kind=kind,
                expires_at=expires_at,
                owner_scope=owner_scope,
                source=self._source,
            )

    def available_credentials(self) -> Dict[str, CredentialPresence]:
        return dict(self._presences)


_PROVIDER_LOCK = threading.Lock()
_PROVIDER: Optional[CredentialPresenceProvider] = None


def set_credential_presence_provider(
    provider: Optional[CredentialPresenceProvider],
) -> None:
    """注入进程级 presence provider（None = 回落 env 默认）。"""
    global _PROVIDER
    with _PROVIDER_LOCK:
        _PROVIDER = provider


def _default_provider() -> CredentialPresenceProvider:
    return EnvCredentialPresenceProvider()


def resolve_credential_presence() -> Dict[str, CredentialPresence]:
    """当前作用域凭证 presence（fail-open：provider 异常 → 空 map）。"""
    with _PROVIDER_LOCK:
        provider = _PROVIDER
    if provider is None:
        provider = _default_provider()
    try:
        out = provider.available_credentials() or {}
    except Exception:  # noqa: BLE001 — presence 面绝不阻断调度
        return {}
    bounded: Dict[str, CredentialPresence] = {}
    for cid, presence in list(out.items())[:MAX_CREDENTIAL_ENTRIES]:
        key = str(cid or "").strip()[:_STR_MAX]
        if key and isinstance(presence, CredentialPresence):
            bounded[key] = presence
    return bounded


def resolve_granted_permissions() -> tuple:
    """环境声明的权限 presence（``GIS_TOOL_PERMISSIONS``，fail-open）。

    与 registry 闸的 ``granted_permissions()`` ContextVar 正交：本函数是
    部署级声明（哪些权限在当前部署可授予），ContextVar 是会话级授予
    （tier3 / plan-approved 语义，user-wins 不变）。
    """
    try:
        raw = os.environ.get(PERMISSIONS_ENV, "")
    except Exception:  # noqa: BLE001
        return ()
    out = []
    for entry in raw.split(","):
        entry = entry.strip()[:_STR_MAX]
        if entry and entry not in out:
            out.append(entry)
        if len(out) >= MAX_CREDENTIAL_ENTRIES:
            break
    return tuple(out)


def presence_fingerprint(presences: Dict[str, CredentialPresence]) -> str:
    """presence map 的稳定指纹（证据/等价性断言键；只含元数据，无 secret）。"""
    import hashlib
    import json

    payload = json.dumps(
        sorted(p.to_dict().items() for p in presences.values()),
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def credentials_present_map(
    presences: Dict[str, CredentialPresence],
) -> Dict[str, bool]:
    """QualificationContext.credentials_present 形态（id→bool，≤8 有界）。"""
    return {cid: True for cid in list(sorted(presences.keys()))[:8]}


__all__ = [
    "CREDENTIALS_ENV",
    "PERMISSIONS_ENV",
    "MAX_CREDENTIAL_ENTRIES",
    "CredentialPresence",
    "CredentialPresenceProvider",
    "EnvCredentialPresenceProvider",
    "set_credential_presence_provider",
    "resolve_credential_presence",
    "resolve_granted_permissions",
    "presence_fingerprint",
    "credentials_present_map",
]
