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
        # ── V3（ADR-0119）：worker 化投影声明收集 ─────────────────────
        self._declared_algorithms: list[dict[str, Any]] = []
        self._declared_providers: list[dict[str, Any]] = []
        self._declared_cartography: list[Any] = []
        self._declared_workflow_packs: list[Any] = []
        self._provider_factories: dict[str, Callable[[Any], Any]] = {}
        self._provider_instances: dict[str, Any] = {}

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

    # ── model providers（V2 Wave 10：worker 模式 = 单帧工具调用）──────
    def register_model_provider(self, spec: Any) -> str:
        """worker 模式 model provider：投影为 worker 内的 invoke 工具。

        streaming 能力在 manifest 层已被拒绝（单帧 RPC）；这里的调用走
        通用 call 往返（聚合结果单帧返回）。本地名 `<pid>_invoke`
        与宿主投影名的前缀剥离形态精确一致。
        """
        from ..sdk.model import ModelProviderSpec
        from ..sdk.tool import ToolExtensionSpec

        if not isinstance(spec, ModelProviderSpec):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    "register_model_provider expects a ModelProviderSpec",
                    extension_id=self.extension_id,
                )
            )
        self._require_declared("model_providers", spec.provider_id)
        diagnostics = spec.validate(
            [
                m.model_dump() if hasattr(m, "model_dump") else dict(m)
                for m in self.manifest.model_providers
            ],
            frozenset(self.manifest.permissions),
        )
        if any(d.severity.value == "error" for d in diagnostics):
            raise ExtensionPlatformError(diagnostics[0])
        local_name = f"{spec.provider_id}_invoke"
        if local_name in self._declared_tools:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_COLLISION,
                    f"model provider invoke tool {local_name!r} collides with a "
                    "declared tool (choose a distinct provider id)",
                    extension_id=self.extension_id,
                )
            )
        owner_ctx = self
        invoke_fn = spec.invoke_fn

        def _invoke(**kwargs: Any) -> Any:
            # 与 in-process 相同的扁平/包膜双形态规约（Round-1 CRITICAL-2）。
            # V3：不再在此聚合——迭代器原样返回，server 按调用形态分流
            # （stream=true → 流帧协议；stream=false → server 侧聚合单帧）。
            if set(kwargs) == {"request"} and isinstance(kwargs["request"], dict):
                req = dict(kwargs["request"])
            else:
                req = dict(kwargs)
            return invoke_fn(req, owner_ctx)

        tool_spec = ToolExtensionSpec(
            name=local_name,
            description=spec.description or f"model provider {spec.provider_id}",
            func=_invoke,
            side_effect="external_side_effect",
            deterministic=False,
            parameters=spec.parameters
            or {
                "type": "object",
                "properties": {"request": {"type": "object"}},
            },
            tags=[f"model_provider:{self.extension_id}"],
        )
        self._declared_tools[local_name] = DeclaredWorkerTool(
            name=local_name,
            description=tool_spec.description,
            kwargs=tool_spec.register_kwargs(),
        )
        self._tool_funcs[local_name] = tool_spec.wrap_with_permissions(self.grants)
        return self.manifest.namespaced_model_provider_tool(spec.provider_id)

    def resolve_tool(self, namespaced_name: str) -> Optional[Callable[..., Any]]:
        """按命名空间化投影名解析工具函数（未注册返回 None）。"""
        local = namespaced_name
        prefix = self.manifest.namespace + "_"
        if local.startswith(prefix):
            candidate = local[len(prefix):]
        else:
            candidate = local
        return self._tool_funcs.get(candidate)

    def _require_declared(self, section: str, key: Any) -> None:
        declared: Any = {t.name for t in self.manifest.tools}
        if section == "model_providers":
            declared = {m.id for m in self.manifest.model_providers}
        elif section == "algorithms":
            declared = {a.id for a in self.manifest.algorithms}
        elif section == "data_providers":
            declared = {p.source_type for p in self.manifest.data_providers}
        elif section == "cartography":
            declared = {(c.kind, c.id) for c in self.manifest.cartography_items}
        elif section == "workflow_packs":
            declared = {w.pack_id for w in self.manifest.workflow_packs}
        if key not in declared:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.UNDECLARED_REGISTRATION,
                    f"{section} registration {key!r} is not declared in the manifest "
                    "(declaration and activation must match; fail closed)",
                    extension_id=self.extension_id,
                )
            )

    # ── 类实例投影：V2 形状拒绝 / V3 形状收集（ADR-0119）──────────────
    def register_algorithm(self, spec: Any = None) -> str:
        raise self._worker_projection_rejected("register_algorithm")

    def register_data_provider(self, spec: Any = None) -> str:
        raise self._worker_projection_rejected("register_data_provider")

    def _v3_gate(self, api: str) -> None:
        """V3 投影门控（纵深防御层）：manifest api < 1.2 → typed 拒绝。"""
        from ..api_version import parse_version

        v = parse_version(self.manifest.api_version)
        if v is None or v < (1, 2, 0):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.WORKER_MODE_INVALID,
                    f"{api} in worker mode requires api_version >= 1.2.0, "
                    f"manifest declares {self.manifest.api_version!r}",
                    extension_id=self.extension_id,
                )
            )

    def register_algorithm_v3(self, descriptor: dict[str, Any]) -> str:
        """V3：worker 算法投影 = **可序列化描述符**（元数据面）。

        与 in-process :meth:`AlgorithmExtensionSpec.build_descriptor` 的
        kwargs 同构；执行体是本 worker 内的工具（``tool_candidates`` 引用
        已注册的 worker 工具）。活对象/run fn 永不出 worker。
        """
        self._v3_gate("register_algorithm_v3")
        if not isinstance(descriptor, dict):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    "register_algorithm_v3 expects a serializable descriptor dict",
                    extension_id=self.extension_id,
                )
            )
        algo_id = descriptor.get("id")
        self._require_declared("algorithms", str(algo_id))
        candidates = list(descriptor.get("tool_candidates") or [])
        unknown = [
            t for t in candidates
            if t not in self._declared_tools and t not in {
                m.id + "_invoke" for m in self.manifest.model_providers
            }
        ]
        if unknown:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.DECLARED_BUT_UNREGISTERED,
                    f"algorithm {algo_id!r} references worker tools {unknown} that "
                    "are not registered in this worker (register tools first)",
                    extension_id=self.extension_id,
                )
            )
        if any(a.get("id") == algo_id for a in self._declared_algorithms):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_COLLISION,
                    f"algorithm {algo_id!r} already registered in this worker",
                    extension_id=self.extension_id,
                )
            )
        self._declared_algorithms.append(dict(descriptor))
        return self.manifest.namespaced_algorithm_id(str(algo_id))

    def register_data_provider_v3(
        self,
        source_type: str,
        factory: Callable[[Any], Any],
        *,
        description: str = "",
        aliases: tuple[str, ...] = (),
        mixins: tuple[str, ...] = (),
    ) -> str:
        """V3：worker 数据 provider = 工厂 + 串行 RPC 代理。

        ``factory(ctx) -> GeospatialDataSourceAdapter 实例``：实例**留在
        worker**（首用懒创建、随 worker 存活）；宿主侧经 7 方法 RPC 代理
        访问。``mixins`` ∈ {"streaming_vector","tiles","raster_window"} —
        宿主代理类据此动态继承，保证 extended_provider_capabilities 可探测。
        """
        self._v3_gate("register_data_provider_v3")
        self._require_declared("data_providers", source_type)
        if not callable(factory):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"data provider {source_type!r}: factory must be callable",
                    extension_id=self.extension_id,
                )
            )
        valid_mixins = {"streaming_vector", "tiles", "raster_window"}
        unknown = sorted(set(mixins) - valid_mixins)
        if unknown:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"data provider {source_type!r}: unknown mixins {unknown}",
                    extension_id=self.extension_id,
                )
            )
        if source_type in self._provider_factories:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_COLLISION,
                    f"data provider {source_type!r} already registered in this worker",
                    extension_id=self.extension_id,
                )
            )
        self._provider_factories[source_type] = factory
        self._declared_providers.append(
            {
                "source_type": source_type,
                "description": description,
                "aliases": list(aliases),
                "mixins": list(mixins),
            }
        )
        return self.manifest.namespaced_source_type(source_type)

    def get_provider_instance(self, source_type: str) -> Any:
        """懒创建 provider 实例（每 source_type 单例，随 worker 存活）。"""
        if source_type not in self._provider_instances:
            factory = self._provider_factories.get(source_type)
            if factory is None:
                raise ExtensionPlatformError(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.DECLARED_BUT_UNREGISTERED,
                        f"data provider {source_type!r} is not registered in this worker",
                        extension_id=self.extension_id,
                    )
                )
            self._provider_instances[source_type] = factory(self)
        return self._provider_instances[source_type]

    def register_cartography_item(self, spec: Any = None) -> str:
        """V3：cartography 声明即 JSON payload —— worker 只收集。

        payload 在握手应答中序列化，宿主走与 in-process 相同的投影路径
        （零活对象跨进程）。api < 1.2 仍拒绝（V2 语义）。
        """
        from ..sdk.declarations import CartographyItemSpec

        self._v3_gate("register_cartography_item")
        if not isinstance(spec, CartographyItemSpec):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    "register_cartography_item expects a CartographyItemSpec",
                    extension_id=self.extension_id,
                )
            )
        self._require_declared("cartography", (spec.kind, spec.id))
        diagnostics = spec.validate()
        if any(d.severity.value == "error" for d in diagnostics):
            raise ExtensionPlatformError(diagnostics[0])
        self._declared_cartography.append(spec)
        return f"{self.manifest.namespace}_{spec.id}"

    def register_workflow_pack(self, spec: Any = None) -> str:
        """V3：recipe 是 pydantic 模型 → model_dump 序列化后握手回传。"""
        from ..sdk.declarations import WorkflowPackSpec

        self._v3_gate("register_workflow_pack")
        if not isinstance(spec, WorkflowPackSpec):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    "register_workflow_pack expects a WorkflowPackSpec",
                    extension_id=self.extension_id,
                )
            )
        self._require_declared("workflow_packs", spec.pack_id)
        diagnostics = spec.validate()
        if any(d.severity.value == "error" for d in diagnostics):
            raise ExtensionPlatformError(diagnostics[0])
        self._declared_workflow_packs.append(spec)
        return f"{self.manifest.namespace}_{spec.pack_id}"

    def _worker_projection_rejected(self, api: str) -> ExtensionPlatformError:
        return ExtensionPlatformError(
            ExtensionDiagnostic.error(
                DiagnosticCode.WORKER_MODE_INVALID,
                f"{api} is unavailable in worker execution mode (class-instance "
                "projections require in_process; use the _v3 variants for "
                "worker-capable shapes, api_version >= 1.2.0)",
                extension_id=self.extension_id,
            )
        )

    # ── 声明读取（server 握手应答用）──────────────────────────────────
    def declared_tools(self) -> list[DeclaredWorkerTool]:
        return [self._declared_tools[name] for name in sorted(self._declared_tools)]

    def declared_algorithms(self) -> list[dict[str, Any]]:
        return [dict(a) for a in self._declared_algorithms]

    def declared_data_providers(self) -> list[dict[str, Any]]:
        return [dict(p) for p in self._declared_providers]

    def declared_cartography(self) -> list[dict[str, Any]]:
        return [
            {
                "kind": s.kind,
                "id": s.id,
                "description": s.description,
                "runtime_status": s.runtime_status,
                "payload": dict(s.payload),
                "export_behavior": s.export_behavior,
                "legend_behavior": s.legend_behavior,
                "degradation_policy": s.degradation_policy,
            }
            for s in sorted(self._declared_cartography, key=lambda s: (s.kind, s.id))
        ]

    def declared_workflow_packs(self) -> list[dict[str, Any]]:
        return [
            {
                "pack_id": p.pack_id,
                "description": p.description,
                "recipes": [
                    r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r)
                    for r in p.recipes
                ],
            }
            for p in sorted(self._declared_workflow_packs, key=lambda p: p.pack_id)
        ]

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
