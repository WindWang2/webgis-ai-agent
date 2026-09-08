"""Capability Broker：worker 扩展的宿主能力代理与唯一执行点（ADR-0105 V2 / Wave 4）。

原则：
- **默认 deny**：无授权、无 allowlist、无供给 → 一律 typed 拒绝；
- **无 ambient authority**：worker 内的每次能力使用都显式跨 RPC，由本
  broker 在宿主进程内执行并审计；
- **SSRF gate 权威**：network 允许表只是粗过滤，最终由 data fabric 的
  ``DataFabricSecurity.validate_url`` 把关（私网/环回/元数据 IP 全拒绝）；
- **审计不落敏感值**：secrets 只记 ref，headers/body 不进审计。

操作面（v1）：
- ``network_request``  —— network 授权 + allowlist + SSRF gate + httpx
- ``artifact_read``    —— project_artifact_read 授权 + artifact 根内路径
- ``artifact_write``   —— project_artifact_write 授权 + artifact 根内路径
- ``secret_get``       —— 供给即授权（按扩展 id 供给的 ref）

诚实边界：in-process 扩展不经过本 broker（trusted-code 语义不变）；
broker 约束的是 worker 扩展的宿主通道，不是任意 OS 系统调用。
"""

from __future__ import annotations

import base64
import binascii
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from .diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError
from .permissions import Permission, PermissionGrantSet

MAX_HTTP_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
DEFAULT_HTTP_TIMEOUT_S = 20.0
AUDIT_RING_SIZE = 256
_ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST"})
_DENIED_HEADERS = frozenset({"host", "content-length", "connection"})


class ExtensionPlatformDenied(ExtensionPlatformError):
    """broker 拒绝（含授权缺失 / allowlist 不匹配 / 路径越界 / SSRF）。"""


def _deny(code: DiagnosticCode, message: str, **context: Any) -> ExtensionPlatformDenied:
    return ExtensionPlatformDenied(
        ExtensionDiagnostic.error(code, message, **context)
    )


@dataclass(frozen=True)
class BrokerLimits:
    max_http_response_bytes: int = MAX_HTTP_RESPONSE_BYTES
    max_artifact_bytes: int = MAX_ARTIFACT_BYTES
    http_timeout_s: float = DEFAULT_HTTP_TIMEOUT_S


class BrokerAuditLog:
    """有界审计环（FIFO 溢出丢弃最旧；快照确定性排序由调用方保证）。"""

    def __init__(self, max_entries: int = AUDIT_RING_SIZE) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)

    def record(
        self, extension_id: str, op: str, ok: bool, detail: str, **context: Any
    ) -> None:
        self._entries.append(
            {
                "extension_id": extension_id,
                "op": op,
                "ok": ok,
                "detail": detail,
                "context": dict(context),
            }
        )

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self._entries)


class CapabilityBroker:
    """单扩展的 broker 实例（host 在激活期构造；线程安全性依赖 httpx）。"""

    def __init__(
        self,
        extension_id: str,
        grants: PermissionGrantSet,
        network_allow: frozenset[str] = frozenset(),
        secrets: Optional[dict[str, str]] = None,
        artifact_roots: tuple[Path, ...] = (),
        limits: Optional[BrokerLimits] = None,
        audit: Optional[BrokerAuditLog] = None,
        http_transport: Optional[Any] = None,
    ) -> None:
        self._extension_id = extension_id
        self._grants = grants
        self._network_allow = frozenset(network_allow)
        self._secrets = dict(secrets or {})
        self._artifact_roots = tuple(Path(r).resolve() for r in artifact_roots)
        self._limits = limits or BrokerLimits()
        self._audit = audit or BrokerAuditLog()
        self._http_transport = http_transport  # 测试注入；None = 真实 httpx

    # ── 分派 ─────────────────────────────────────────────────────────
    def handle(self, op: str, payload: dict[str, Any]) -> tuple[bool, Any]:
        try:
            handler = {
                "network_request": self._network_request,
                "artifact_read": self._artifact_read,
                "artifact_write": self._artifact_write,
                "secret_get": self._secret_get,
            }.get(op)
            if handler is None:
                raise _deny(
                    DiagnosticCode.BROKER_DENIED,
                    f"unknown broker op {op!r}",
                    extension_id=self._extension_id,
                )
            if not isinstance(payload, dict):
                raise _deny(
                    DiagnosticCode.BROKER_DENIED,
                    "broker payload must be an object",
                    extension_id=self._extension_id,
                )
            value = handler(payload)
            self._audit.record(self._extension_id, op, True, "ok")
            return True, value
        except ExtensionPlatformDenied as exc:
            self._audit.record(
                self._extension_id,
                op,
                False,
                exc.diagnostic.message,
                **({"ref": payload.get("ref")} if op == "secret_get" else {}),
            )
            return False, {
                "code": exc.diagnostic.code.value,
                "message": exc.diagnostic.message,
            }
        except Exception as exc:  # noqa: BLE001 - 内部故障不向 worker 泄漏细节
            self._audit.record(self._extension_id, op, False, f"internal: {type(exc).__name__}")
            return False, {
                "code": DiagnosticCode.BROKER_DENIED.value,
                "message": f"broker op {op!r} failed internally",
            }

    # ── 授权 ─────────────────────────────────────────────────────────
    def _require(self, permission: str) -> None:
        if not self._grants.allows(permission):
            raise _deny(
                DiagnosticCode.PERMISSION_NOT_GRANTED,
                f"extension {self._extension_id!r} lacks permission {permission!r} "
                f"for this broker op (default deny)",
                extension_id=self._extension_id,
                permission=permission,
            )

    # ── network_request ───────────────────────────────────────────────
    def _network_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require(Permission.NETWORK)
        url = payload.get("url")
        if not isinstance(url, str) or not url.strip():
            raise _deny(
                DiagnosticCode.BROKER_DENIED, "network_request requires 'url'",
                extension_id=self._extension_id,
            )
        method = str(payload.get("method") or "GET").upper()
        if method not in _ALLOWED_METHODS:
            raise _deny(
                DiagnosticCode.BROKER_DENIED,
                f"method {method!r} not allowed (GET/HEAD/POST only)",
                extension_id=self._extension_id,
            )
        # 粗过滤：出网 allowlist（host 级；"*" = 全放行）。
        hostname = (urlparse(url).hostname or "").lower()
        if not hostname:
            raise _deny(DiagnosticCode.BROKER_DENIED, "URL has no hostname",
                        extension_id=self._extension_id)
        if "*" not in self._network_allow and hostname not in self._network_allow:
            raise _deny(
                DiagnosticCode.BROKER_DENIED,
                f"host {hostname!r} is not in EXTENSION_NETWORK_ALLOW for "
                f"{self._extension_id!r} (default deny)",
                extension_id=self._extension_id,
            )
        # 权威 SSRF gate（解析全部地址，拒私网/环回/元数据）。
        from app.services.data_fabric.security import DataFabricSecurity, DataFabricSecurityError

        try:
            DataFabricSecurity.validate_url(url)
        except DataFabricSecurityError as exc:
            raise _deny(
                DiagnosticCode.BROKER_DENIED,
                f"SSRF gate rejected {hostname!r}: {exc}",
                extension_id=self._extension_id,
            )
        headers = {
            str(k): str(v)
            for k, v in (payload.get("headers") or {}).items()
            if str(k).lower() not in _DENIED_HEADERS
        }
        body = payload.get("body")
        if body is not None:
            if payload.get("body_is_b64"):
                try:
                    body = base64.b64decode(str(body), validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise _deny(DiagnosticCode.BROKER_DENIED,
                                f"invalid base64 body: {exc}",
                                extension_id=self._extension_id)
            elif not isinstance(body, (str, bytes)):
                raise _deny(DiagnosticCode.BROKER_DENIED, "body must be string/bytes",
                            extension_id=self._extension_id)
        timeout_s = min(
            float(payload.get("timeout_s") or self._limits.http_timeout_s),
            self._limits.http_timeout_s,
        )
        try:
            if self._http_transport is not None:
                response = self._http_transport.request(
                    method, url, headers=headers, content=body, timeout=timeout_s
                )
            else:
                with httpx.Client(follow_redirects=False, timeout=timeout_s) as client:
                    response = client.request(method, url, headers=headers, content=body)
        except httpx.HTTPError as exc:
            return {"status": 0, "error": f"{type(exc).__name__}", "content_type": "", "body_b64": "", "truncated": False}
        raw = response.content or b""
        cap = self._limits.max_http_response_bytes
        return {
            "status": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "body_b64": base64.b64encode(raw[:cap]).decode("ascii"),
            "truncated": len(raw) > cap,
        }

    # ── artifact read/write ───────────────────────────────────────────
    def _confine(self, raw_path: Any) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise _deny(DiagnosticCode.BROKER_DENIED, "artifact op requires 'path'",
                        extension_id=self._extension_id)
        candidate = Path(raw_path)
        resolved = candidate.resolve() if candidate.is_absolute() else None
        for root in self._artifact_roots:
            target = (root / candidate).resolve() if not candidate.is_absolute() else resolved
            assert target is not None
            if target.is_relative_to(root):
                return target
        raise _deny(
            DiagnosticCode.BROKER_DENIED,
            f"path {raw_path!r} is outside every configured artifact root "
            "(default deny)",
            extension_id=self._extension_id,
        )

    def _artifact_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require(Permission.PROJECT_ARTIFACT_READ)
        target = self._confine(payload.get("path"))
        if not target.is_file():
            raise _deny(DiagnosticCode.BROKER_DENIED, f"artifact {str(target)!r} not found",
                        extension_id=self._extension_id)
        data = target.read_bytes()
        cap = self._limits.max_artifact_bytes
        return {
            "path": str(target),
            "size": len(data),
            "content_b64": base64.b64encode(data[:cap]).decode("ascii"),
            "truncated": len(data) > cap,
        }

    def _artifact_write(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require(Permission.PROJECT_ARTIFACT_WRITE)
        content = payload.get("content_b64")
        if not isinstance(content, str):
            raise _deny(DiagnosticCode.BROKER_DENIED, "artifact_write requires 'content_b64'",
                        extension_id=self._extension_id)
        try:
            data = base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise _deny(DiagnosticCode.BROKER_DENIED, f"invalid base64 content: {exc}",
                        extension_id=self._extension_id)
        if len(data) > self._limits.max_artifact_bytes:
            raise _deny(
                DiagnosticCode.OUTPUT_LIMIT_EXCEEDED,
                f"artifact exceeds {self._limits.max_artifact_bytes} bytes",
                extension_id=self._extension_id,
            )
        target = self._confine(payload.get("path"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"path": str(target), "bytes_written": len(data)}

    # ── secret_get ────────────────────────────────────────────────────
    def _secret_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        # 供给即授权：ref 必须由运维按本扩展 id 显式供给（权限词表保持冻结）。
        ref = payload.get("ref")
        if not isinstance(ref, str) or not ref:
            raise _deny(DiagnosticCode.BROKER_DENIED, "secret_get requires 'ref'",
                        extension_id=self._extension_id)
        if ref not in self._secrets:
            raise _deny(
                DiagnosticCode.BROKER_DENIED,
                f"secret ref {ref!r} is not provisioned for {self._extension_id!r} "
                "(supply via EXTENSION_SECRETS_JSON; default deny)",
                extension_id=self._extension_id,
            )
        return {"ref": ref, "value": self._secrets[ref]}
