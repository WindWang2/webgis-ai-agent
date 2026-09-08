"""WorkerContext：worker 进程内扩展看到的激活门面（ADR-0105 V2）。

与 in-process :class:`~app.extensions_platform.context.ExtensionContext`
同形的注册 API，但语义不同：

- **不写任何权威 registry**（worker 进程内不存在权威状态）——只校验并
  收集声明，握手应答把可序列化的注册信息交回宿主，由宿主投影；
- worker 仅支持工具型投影；类实例投影（algorithm/provider/cartography/
  workflow）在 manifest 层已被拒绝，这里再 typed 拒绝一次（纵深防御）；
- worker 工具必须显式 ``parameters``（JSON schema dict）：``args_model``
  是类型对象，无法跨进程传递；
- ``broker`` 是宿主能力通道（network/artifact/secrets/model_invoke），
  默认 deny 由宿主侧 broker 执行（W4）。
"""

from __future__ import annotations

import base64
import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError
from ..manifest import GisExtensionManifest
from ..permissions import PermissionGrantSet
from ..trust import TrustLevel


@dataclass(frozen=True)
class DeclaredWorkerTool:
    """一个 worker 工具的完整可序列化声明（宿主投影的依据）。"""

    name: str  # 本地名（不含命名空间前缀）
    description: str
    kwargs: dict[str, Any]  # ToolRegistry.register kwargs（不含 name/description/func）


class BrokerFacade:
    """扩展侧 broker 门面：类型化方法 → 跨进程 broker 请求。

    每次调用都是显式授权检查点（宿主侧默认 deny）；拒绝抛 typed
    ``ExtensionPlatformError``。二进制载荷走 base64。
    """

    def __init__(self, request: Callable[[str, dict[str, Any]], Any]) -> None:
        self._request = request

    def http_request(
        self,
        url: str,
        method: str = "GET",
        *,
        headers: Optional[dict[str, str]] = None,
        body: Optional[bytes | str] = None,
        timeout_s: float = 10.0,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "url": url,
            "method": method,
            "timeout_s": timeout_s,
        }
        if headers:
            payload["headers"] = dict(headers)
        if body is not None:
            if isinstance(body, bytes):
                payload["body"] = base64.b64encode(body).decode("ascii")
                payload["body_is_b64"] = True
            else:
                payload["body"] = body
        result = self._request("network_request", payload)
        return {
            "status": result.get("status", 0),
            "content_type": result.get("content_type", ""),
            "body": base64.b64decode(result.get("body_b64") or ""),
            "truncated": bool(result.get("truncated")),
        }

    def read_artifact(self, path: str) -> bytes:
        result = self._request("artifact_read", {"path": path})
        return base64.b64decode(result.get("content_b64") or "")

    def write_artifact(self, path: str, data: bytes) -> int:
        result = self._request(
            "artifact_write",
            {"path": path, "content_b64": base64.b64encode(bytes(data)).decode("ascii")},
        )
        return int(result.get("bytes_written", 0))

    def get_secret(self, ref: str) -> str:
        return str(self._request("secret_get", {"ref": ref})["value"])


class WorkerContext:
    """worker 进程内的激活上下文（由 worker server 构造，扩展不可自建）。"""

    def __init__(
        self,
        manifest: GisExtensionManifest,
        trust: TrustLevel,
        grants: PermissionGrantSet,
        settings: dict[str, Any],
        module_dir: Any,
        entry_module_name: str,
        broker_transport: Optional[Callable[[str, dict[str, Any]], Any]] = None,
    ) -> None:
        self.manifest = manifest
        self.extension_id = manifest.id
        self.trust = trust
        self.grants = grants
        self.extension_settings: dict[str, Any] = dict(settings)
        self._module_dir = module_dir
        self._entry_module_name = entry_module_name
        # broker 传输由 server 注入；None = 未连接（broker 调用 typed 失败）。
        self._broker_transport = broker_transport
        self._declared_tools: dict[str, DeclaredWorkerTool] = {}
        # 运行时工具函数（永不离开 worker 进程；按本地名索引）。
        self._tool_funcs: dict[str, Callable[..., Any]] = {}
        # 扩展侧 broker 门面（transport 为 None 时调用 typed 失败）。
        self.broker = BrokerFacade(self.broker_request)

    # ── 工具注册（收集 + 校验，不写 registry）────────────────────────
    def register_tool(self, spec: Any) -> str:
        from ..sdk.tool import ToolExtensionSpec

        if not isinstance(spec, ToolExtensionSpec):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    "register_tool expects a ToolExtensionSpec",
                    extension_id=self.extension_id,
                )
            )
        self._require_declared("tools", spec.name)
        undeclared_perms = sorted(set(spec.required_permissions) - set(self.manifest.permissions))
        if undeclared_perms:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.PERMISSION_DECLARATION_INVALID,
                    f"tool {spec.name!r} requires permissions {undeclared_perms} that the "
                    "manifest does not declare",
                    extension_id=self.extension_id,
                )
            )
        diagnostics = spec.validate()
        if any(d.severity.value == "error" for d in diagnostics):
            raise ExtensionPlatformError(diagnostics[0])
        if spec.args_model is not None:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.WORKER_MODE_INVALID,
                    f"worker tool {spec.name!r} cannot use args_model (type objects do "
                    "not cross the process boundary; declare explicit 'parameters')",
                    extension_id=self.extension_id,
                )
            )
        if not isinstance(spec.parameters, dict) or not spec.parameters:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.WORKER_MODE_INVALID,
                    f"worker tool {spec.name!r} must declare an explicit 'parameters' "
                    "JSON schema (worker registration is serialized to the host)",
                    extension_id=self.extension_id,
                )
            )
        kwargs = spec.register_kwargs()
        # func 永不出 worker；权限包裹在 worker 内生效（宿主侧另有授权检查）。
        self._declared_tools[spec.name] = DeclaredWorkerTool(
            name=spec.name,
            description=spec.description,
            kwargs=kwargs,
        )
        self._tool_funcs[spec.name] = spec.wrap_with_permissions(self.grants)
        return self.manifest.namespaced_tool_name(spec.name)

    def resolve_tool(self, namespaced_name: str) -> Optional[Callable[..., Any]]:
        """按命名空间化投影名解析工具函数（未注册返回 None）。"""
        local = namespaced_name
        prefix = self.manifest.namespace + "_"
        if local.startswith(prefix):
            candidate = local[len(prefix):]
        else:
            candidate = local
        return self._tool_funcs.get(candidate)

    def _require_declared(self, section: str, key: str) -> None:
        declared = {t.name for t in self.manifest.tools}
        if key not in declared:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.UNDECLARED_REGISTRATION,
                    f"{section} registration {key!r} is not declared in the manifest "
                    "(declaration and activation must match; fail closed)",
                    extension_id=self.extension_id,
                )
            )

    # ── 类实例投影在 worker 内 typed 拒绝（纵深防御）──────────────────
    def register_algorithm(self, spec: Any = None) -> str:
        raise self._worker_projection_rejected("register_algorithm")

    def register_data_provider(self, spec: Any = None) -> str:
        raise self._worker_projection_rejected("register_data_provider")

    def register_cartography_item(self, spec: Any = None) -> str:
        raise self._worker_projection_rejected("register_cartography_item")

    def register_workflow_pack(self, spec: Any = None) -> str:
        raise self._worker_projection_rejected("register_workflow_pack")

    def _worker_projection_rejected(self, api: str) -> ExtensionPlatformError:
        return ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.WORKER_MODE_INVALID,
                f"{api} is unavailable in worker execution mode (class-instance "
                "projections require in_process; manifest declares worker mode)",
                extension_id=self.extension_id,
            )
        )

    # ── 声明读取（server 握手应答用）──────────────────────────────────
    def declared_tools(self) -> list[DeclaredWorkerTool]:
        return [self._declared_tools[name] for name in sorted(self._declared_tools)]

    # ── 兄弟模块加载（与 ExtensionContext 同规则）─────────────────────
    def load_sibling(self, module_name: str) -> Any:
        import importlib.util

        if not self._entry_module_name:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.ENTRY_POINT_FAILED,
                    "load_sibling unavailable outside activation",
                    extension_id=self.extension_id,
                )
            )
        if not self._module_dir:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.ENTRY_POINT_FAILED,
                    "load_sibling unavailable: module directory not recorded",
                    extension_id=self.extension_id,
                )
            )
        target = self._module_dir / f"{module_name}.py"
        if not target.is_file():
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.ENTRY_POINT_MISSING,
                    f"sibling module {module_name!r} not found in {str(self._module_dir)!r}",
                    extension_id=self.extension_id,
                )
            )
        if not target.resolve().is_relative_to(self._module_dir.resolve()):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.ENTRY_POINT_MISSING,
                    f"sibling module {module_name!r} escapes the extension directory",
                    extension_id=self.extension_id,
                )
            )
        qualname = f"{self._entry_module_name}.{module_name}"
        if qualname in sys.modules:
            return sys.modules[qualname]
        spec = importlib.util.spec_from_file_location(qualname, target)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualname] = module
        spec.loader.exec_module(module)
        return module

    # ── broker（W4 接入；此处提供 typed 传输边界）─────────────────────
    def broker_request(self, op: str, payload: dict[str, Any]) -> Any:
        if self._broker_transport is None:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.BROKER_DENIED,
                    f"broker transport unavailable for op {op!r} (worker not connected)",
                    extension_id=self.extension_id,
                )
            )
        return self._broker_transport(op, payload)
