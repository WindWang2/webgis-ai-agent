"""Cartography / Workflow 扩展声明（ADR-0104 / Wave 5-6）。

两个轻量声明形态：宿主在投影期把它们翻译为对应权威 registry 的注册
调用（翻译逻辑在 context.py，本模块只承载可校验的数据）。

Cartography 项的三种 kind 与投影目标：
- component → ``app/lib/cartography/component_registry.MapComponentDescriptor``
  扩展组件强制 ``runtime_status`` 声明为 planned/unavailable，除非能
  提供真实渲染器证据（V1：一律 planned，native 保留给前端真实实现——
  「planned ≠ rendered」是既有诚实性规则）。
- model → ``model_library.MapModel``（扩展 map model 默认带 fallback 链
  建议声明的 degradation policy）。
- theme → 主题/调色板注册（强制 ``<ns>_`` 前缀，防 last-wins 覆盖种子）。

Workflow pack：携带 ``CartographyRecipe`` 列表（gis_harness V2 DSL），由
宿主经 recipe registry 投影；keep-first 语义天然保证种子优先，包内
recipe id 仍强制 ``<ns>_`` 前缀。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..diagnostics import DiagnosticCode, ExtensionDiagnostic

_VALID_CARTO_KINDS = frozenset({"component", "model", "theme"})


@dataclass
class CartographyItemSpec:
    kind: str
    id: str
    description: str = ""
    runtime_status: str = "planned"
    # 目标 registry 的构造载荷（component → MapComponentDescriptor kwargs；
    # model → MapModel kwargs；theme → theme 注册 kwargs）。字段级校验由
    # 目标 registry 自身的 pydantic/validate 完成（SDK 不复制第二套 schema）。
    payload: dict[str, Any] = field(default_factory=dict)
    # 诚实性声明（文档 / 目录生成用）。
    supported_renderers: tuple[str, ...] = ()
    export_behavior: str = "degraded"
    legend_behavior: str = "auto"
    accessibility_notes: str = ""
    degradation_policy: str = "omit_with_disclosure"

    def validate(self) -> list[ExtensionDiagnostic]:
        diagnostics: list[ExtensionDiagnostic] = []
        if self.kind not in _VALID_CARTO_KINDS:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"cartography kind {self.kind!r} invalid"
                )
            )
        if self.runtime_status == "native":
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID,
                    f"cartography item {self.id!r}: extension items cannot claim runtime_status "
                    "'native' (no renderer evidence); use 'planned'",
                )
            )
        if self.export_behavior not in {"degraded", "unsupported", "native_export"}:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"invalid export_behavior {self.export_behavior!r}"
                )
            )
        if self.degradation_policy not in {"omit_with_disclosure", "render_placeholder", "block"}:
            diagnostics.append(
                ExtensionDiagnostic.error(
                    DiagnosticCode.MANIFEST_INVALID, f"invalid degradation_policy {self.degradation_policy!r}"
                )
            )
        return diagnostics


@dataclass
class WorkflowPackSpec:
    pack_id: str
    description: str = ""
    recipes: list[Any] = field(default_factory=list)  # CartographyRecipe（gis_harness DSL）

    def validate(self) -> list[ExtensionDiagnostic]:
        diagnostics: list[ExtensionDiagnostic] = []
        if not self.recipes:
            diagnostics.append(
                ExtensionDiagnostic.warning(
                    DiagnosticCode.MANIFEST_INVALID, f"workflow pack {self.pack_id!r} declares no recipes"
                )
            )
        for recipe in self.recipes:
            recipe_id = getattr(recipe, "id", None)
            if not recipe_id:
                diagnostics.append(
                    ExtensionDiagnostic.error(
                        DiagnosticCode.MANIFEST_INVALID,
                        f"workflow pack {self.pack_id!r} contains a recipe without id",
                    )
                )
        return diagnostics
