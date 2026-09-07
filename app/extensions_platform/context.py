"""ExtensionContext：扩展激活期拿到的投影门面（ADR-0104 / Wave 2-7）。

扩展入口的 ``activate(ctx)`` 只见到这个对象。所有注册都走投影管线：

    SDK spec 校验 → 声明核对（manifest 承诺了才允许）→ 命名空间化
    → 碰撞检查（对活 registry，先查后写）→ 写入权威 registry
    → 台账记录 undo

失败语义：任何 register_* 抛 :class:`ExtensionPlatformError`（typed）。
宿主捕获后执行台账回滚 → 激活原子性。扩展拿不到核心 registry 对象本身，
无法绕过本门面直接注入（ToolRegistry 引用只在本模块内部使用）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .diagnostics import DiagnosticCode, ExtensionDiagnostic, ExtensionPlatformError
from .ledger import ProjectionLedger
from .manifest import GisExtensionManifest
from .permissions import PermissionGrantSet
from .trust import TrustLevel

logger = logging.getLogger(__name__)


class ExtensionContext:
    """单个扩展激活期的注册门面（由 host 构造，扩展不可自行实例化）。"""

    def __init__(
        self,
        manifest: GisExtensionManifest,
        trust: TrustLevel,
        grants: PermissionGrantSet,
        settings: dict[str, Any],
        tool_registry: Any,
        ledger: ProjectionLedger,
    ) -> None:
        self.manifest = manifest
        self.extension_id = manifest.id
        self.trust = trust
        self.grants = grants
        self.extension_settings: dict[str, Any] = dict(settings)
        # 内部依赖（不由扩展触碰；host 注入）。
        self._tool_registry = tool_registry
        self._ledger = ledger
        self._registered: dict[str, set[str]] = {}

    # ── 声明核对 ──────────────────────────────────────────────────────
    def _require_declared(self, section: str, key: str) -> None:
        declared = {
            "tools": {t.name for t in self.manifest.tools},
            "algorithms": {a.id for a in self.manifest.algorithms},
            "data_providers": {p.source_type for p in self.manifest.data_providers},
            "cartography": {(c.kind, c.id) for c in self.manifest.cartography_items},
            "workflow_packs": {w.pack_id for w in self.manifest.workflow_packs},
        }.get(section, set())
        if key not in declared:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.UNDECLARED_REGISTRATION,
                    f"{section} registration {key!r} is not declared in the manifest "
                    "(declaration and activation must match; fail closed)",
                    extension_id=self.extension_id,
                )
            )

    def _record(self, kind: str, projected_id: str, undo: Callable[[], bool]) -> None:
        self._ledger.record(kind, projected_id, undo)
        self._registered.setdefault(kind, set()).add(projected_id)

    # ── tools（Wave 3）────────────────────────────────────────────────
    def register_tool(self, spec: Any) -> str:
        """投影一个 ToolExtensionSpec；返回命名空间化后的工具名。"""
        from .sdk.tool import ToolExtensionSpec

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
                    "manifest does not declare (tool surface inherits extension grants)",
                    extension_id=self.extension_id,
                )
            )
        diagnostics = spec.validate()
        if any(d.severity.value == "error" for d in diagnostics):
            raise ExtensionPlatformError(diagnostics[0])
        projected = self.manifest.namespaced_tool_name(spec.name)
        if self._tool_registry.has(projected):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_COLLISION,
                    f"tool {projected!r} already registered (core tools can never be shadowed)",
                    extension_id=self.extension_id,
                )
            )
        self._check_references(spec)
        wrapped = spec.wrap_with_permissions(self.grants)
        try:
            self._tool_registry.register(projected, spec.description, wrapped, **spec.register_kwargs())
        except Exception as exc:  # noqa: BLE001 - 归一为投影失败
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_FAILED,
                    f"tool {projected!r} rejected by ToolRegistry: {exc}",
                    extension_id=self.extension_id,
                )
            ) from exc
        self._record("tool", projected, lambda: self._tool_registry.unregister(projected))
        logger.info("extension %s projected tool %s", self.extension_id, projected)
        return projected

    def _check_references(self, spec: Any) -> None:
        """capabilities/algorithms 引用存在性（冻结 seam 的准入规则）。"""
        caps = spec.capabilities or []
        if caps:
            from app.lib.gis.capability_registry import get_capability_registry

            known = set(get_capability_registry().all_ids)
            unknown = [c for c in caps if c not in known]
            if unknown:
                raise ExtensionPlatformError(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.REGISTRY_PROJECTION_COLLISION,
                        f"tool {spec.name!r} references unknown capabilities {unknown} "
                        "(CapabilityRegistry is authoritative; extension tools may only "
                        "reference existing ids)",
                        extension_id=self.extension_id,
                    )
                )
        algos = spec.algorithms or []
        if algos:
            from app.lib.gis.algorithm_registry import get_algorithm_registry

            registry = get_algorithm_registry()
            for algo in algos:
                if not registry.has(algo):
                    raise ExtensionPlatformError(
                        ExtensionDiagnostic.error(
                            DiagnosticCode.REGISTRY_PROJECTION_COLLISION,
                            f"tool {spec.name!r} references unregistered algorithm {algo!r} "
                            "(register namespaced algorithms before tools that cite them)",
                            extension_id=self.extension_id,
                        )
                    )

    # ── algorithms（Wave 4）──────────────────────────────────────────
    def register_algorithm(self, spec: Any) -> str:
        from app.lib.gis.capability_registry import get_capability_registry
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from .sdk.algorithm import AlgorithmExtensionSpec

        if not isinstance(spec, AlgorithmExtensionSpec):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    "register_algorithm expects an AlgorithmExtensionSpec",
                    extension_id=self.extension_id,
                )
            )
        self._require_declared("algorithms", spec.id)
        # 第一遍：仅结构校验（None = 跳过存在性检查）。
        diagnostics = spec.validate()
        if any(d.severity.value == "error" for d in diagnostics):
            raise ExtensionPlatformError(diagnostics[0])
        registry = get_algorithm_registry()
        projected = self.manifest.namespaced_algorithm_id(spec.id)
        if registry.has(projected):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_COLLISION,
                    f"algorithm {projected!r} already registered",
                    extension_id=self.extension_id,
                )
            )
        known_tools = frozenset(self._tool_registry.tool_names())
        algo_diagnostics = spec.validate(
            known_capabilities=set(get_capability_registry().all_ids),
            known_tools=known_tools,
        )
        if any(d.severity.value == "error" for d in algo_diagnostics):
            raise ExtensionPlatformError(algo_diagnostics[0])
        descriptor = spec.build_descriptor(projected)
        try:
            registry.register(descriptor)
        except Exception as exc:  # noqa: BLE001
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_FAILED,
                    f"algorithm {projected!r} rejected by AlgorithmRegistry: {exc}",
                    extension_id=self.extension_id,
                )
            ) from exc
        self._record("algorithm", projected, lambda: registry.unregister(projected))
        logger.info("extension %s projected algorithm %s", self.extension_id, projected)
        return projected

    # ── data providers（Wave 7）──────────────────────────────────────
    def register_data_provider(self, spec: Any) -> str:
        from app.services.data_fabric.registry import get_registry
        from .sdk.provider import ProviderExtensionSpec

        if not isinstance(spec, ProviderExtensionSpec):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    "register_data_provider expects a ProviderExtensionSpec",
                    extension_id=self.extension_id,
                )
            )
        self._require_declared("data_providers", spec.source_type)
        declared_permissions = frozenset(self.manifest.permissions)
        diagnostics = spec.validate(declared_permissions)
        if any(d.severity.value == "error" for d in diagnostics):
            raise ExtensionPlatformError(diagnostics[0])
        from app.services.data_fabric.registry import AdapterSpec

        canonical = self.manifest.namespaced_source_type(spec.source_type)
        aliases = tuple(f"{self.manifest.namespace}_{a}" for a in spec.aliases)
        registry = get_registry()
        if canonical in registry.supported_source_types():
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_COLLISION,
                    f"source type {canonical!r} already registered",
                    extension_id=self.extension_id,
                )
            )
        adapter_spec = AdapterSpec(
            canonical=canonical,
            adapter_cls=spec.adapter_cls,
            aliases=aliases,
            supports_bbox=spec.supports_bbox,
            supports_filter=spec.supports_filter,
            supports_pagination=spec.supports_pagination,
            supports_datetime=spec.supports_datetime,
            supports_projection=spec.supports_projection,
            is_raster_tile=spec.is_raster_tile,
            is_demo=False,
            notes=spec.notes[:200],
        )
        try:
            registry.register(adapter_spec)
        except Exception as exc:  # noqa: BLE001
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_FAILED,
                    f"source type {canonical!r} rejected by AdapterRegistry: {exc}",
                    extension_id=self.extension_id,
                )
            ) from exc
        self._record("data_provider", canonical, lambda: registry.unregister(canonical))
        logger.info("extension %s projected provider %s", self.extension_id, canonical)
        return canonical

    # ── cartography（Wave 6）─────────────────────────────────────────
    def register_cartography_item(self, spec: Any) -> str:
        from .sdk.declarations import CartographyItemSpec

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
        if spec.kind == "component":
            projected = self._project_component(spec)
        elif spec.kind == "model":
            projected = self._project_map_model(spec)
        else:
            projected = self._project_theme(spec)
        logger.info("extension %s projected cartography %s", self.extension_id, projected)
        return projected

    def _project_component(self, spec: Any) -> str:
        from app.lib.cartography.component_registry import get_component_registry

        registry = get_component_registry()
        payload = dict(spec.payload)
        item_id = f"{self.manifest.namespace}_{spec.id}"
        if "id" in payload and payload["id"] != spec.id:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"component payload id {payload['id']!r} must equal declaration id {spec.id!r}",
                    extension_id=self.extension_id,
                )
            )
        payload["id"] = item_id
        comp_type = payload.get("type")
        if not isinstance(comp_type, str) or not comp_type.startswith(f"{self.manifest.namespace}_"):
            payload["type"] = f"{self.manifest.namespace}_{comp_type or spec.id}"
        if payload.get("runtime_status") not in (None, "planned", "unavailable"):
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"component {item_id!r}: runtime_status must be planned|unavailable",
                    extension_id=self.extension_id,
                )
            )
        payload["runtime_status"] = payload.get("runtime_status") or "planned"
        try:
            from app.lib.cartography.component_registry import MapComponentDescriptor

            descriptor = MapComponentDescriptor.model_validate(payload)
            registry.register(descriptor)
        except Exception as exc:  # noqa: BLE001
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_FAILED,
                    f"component {item_id!r} rejected by ComponentRegistry: {exc}",
                    extension_id=self.extension_id,
                )
            ) from exc
        self._record("cartography", item_id, lambda: registry.unregister(item_id))
        return item_id

    def _project_map_model(self, spec: Any) -> str:
        from app.lib.cartography.model_library import get_map_model_registry

        registry = get_map_model_registry()
        payload = dict(spec.payload)
        model_id = f"{self.manifest.namespace}_{spec.id}"
        if "id" in payload and payload["id"] != spec.id:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"model payload id {payload['id']!r} must equal declaration id {spec.id!r}",
                    extension_id=self.extension_id,
                )
            )
        payload["id"] = model_id
        if registry.get(model_id) is not None:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_COLLISION,
                    f"map model {model_id!r} already registered",
                    extension_id=self.extension_id,
                )
            )
        try:
            from app.lib.cartography.model_library import MapModel

            model = MapModel.model_validate(payload)
            registry.register(model)
        except Exception as exc:  # noqa: BLE001
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_FAILED,
                    f"map model {model_id!r} rejected by MapModelRegistry: {exc}",
                    extension_id=self.extension_id,
                )
            ) from exc
        self._record("cartography", model_id, lambda: registry.unregister(model_id))
        return model_id

    def _project_theme(self, spec: Any) -> str:
        from app.lib.cartography.themes import get_cartographic_theme_registry

        registry = get_cartographic_theme_registry()
        payload = dict(spec.payload)
        theme_id = f"{self.manifest.namespace}_{spec.id}"
        if "id" in payload and payload["id"] != spec.id:
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"theme payload id {payload['id']!r} must equal declaration id {spec.id!r}",
                    extension_id=self.extension_id,
                )
            )
        payload["id"] = theme_id
        try:
            from app.lib.cartography.themes import CartographicThemeDescriptor

            theme = CartographicThemeDescriptor.model_validate(payload)
            registry.register_theme(theme)
        except Exception as exc:  # noqa: BLE001
            raise ExtensionPlatformError(
                ExtensionDiagnostic.error(
                    DiagnosticCode.REGISTRY_PROJECTION_FAILED,
                    f"theme {theme_id!r} rejected by CartographicThemeRegistry: {exc}",
                    extension_id=self.extension_id,
                )
            ) from exc
        self._record("cartography", theme_id, lambda: registry.unregister_theme(theme_id))
        return theme_id

    # ── workflow packs（Wave 5）──────────────────────────────────────
    def register_workflow_pack(self, spec: Any) -> str:
        from app.services.gis_harness.recipes import get_recipe_registry
        from .sdk.declarations import WorkflowPackSpec

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
        registry = get_recipe_registry()
        projected_ids: list[str] = []
        for recipe in spec.recipes:
            recipe_id = getattr(recipe, "id", "")
            if not recipe_id.startswith(f"{self.manifest.namespace}_"):
                raise ExtensionPlatformError(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.MANIFEST_INVALID,
                        f"recipe id {recipe_id!r} must be prefixed with "
                        f"{self.manifest.namespace + '_'!r} (namespace isolation)",
                        extension_id=self.extension_id,
                    )
                )
            if registry.get(recipe_id) is not None:
                raise ExtensionPlatformError(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.REGISTRY_PROJECTION_COLLISION,
                        f"recipe {recipe_id!r} already registered (seed recipes always win)",
                        extension_id=self.extension_id,
                    )
                )
            projected_ids.append(recipe_id)
        for recipe in spec.recipes:
            registry.register(recipe)
            recipe_id = recipe.id
            self._record("workflow_recipe", recipe_id, lambda rid=recipe_id: registry.unregister(rid))
        pack_projected = f"{self.manifest.namespace}_{spec.pack_id}"
        logger.info(
            "extension %s projected workflow pack %s (%d recipes)",
            self.extension_id, pack_projected, len(projected_ids),
        )
        return pack_projected

    # ── 供宿主核对声明 ↔ 实际 ────────────────────────────────────────
    def registered_ids(self) -> dict[str, set[str]]:
        return {k: set(v) for k, v in self._registered.items()}
