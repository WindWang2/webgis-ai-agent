"""统一 registry 校验套件（§26）—— 交叉引用完整性，禁止静默悬空引用。

覆盖：Capability ↔ Algorithm ↔ Artifact ↔ MapModel ↔ Recipe ↔
ProductTemplate ↔ StyleTemplate（含 composite slot）。任何一方注册了
引用另一方 id 的条目，被引用方必须存在。`validate_gis_library()` 汇总
全部检查，空列表 = 通过；单测（tests/unit/gis_harness/
test_registry_validation.py）+ 可选 available_tools 视图下做 tool 存在性
对账。
"""
from __future__ import annotations

from typing import Any, List, Optional

from app.lib.cartography.model_library import (
    get_map_model_registry,
    validate_model_library,
)
from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.artifacts import get_artifact_type_registry
from app.lib.gis.capability_registry import get_capability_registry
from app.services.gis_harness.components import ComponentType
from app.services.gis_harness.product_templates import (
    get_product_template_registry,
)
from app.services.gis_harness.recipes import get_recipe_registry
from app.services.gis_harness.template_catalog import get_template_catalog


def validate_gis_library(
    available_tools: Optional[set] = None,
    *,
    tool_registry: Optional[Any] = None,
) -> List[str]:
    """全库交叉校验。返回违规列表（空 = 通过）。

    ``available_tools``：传入真实 ToolRegistry 工具名集合时，对 native
    算法的 tool_candidates 做存在性对账（audit #825 的 registry 版）。
    """
    issues: List[str] = []

    artifacts = get_artifact_type_registry()
    capabilities = get_capability_registry()
    algorithms = get_algorithm_registry()
    models = get_map_model_registry()
    recipes = get_recipe_registry()
    products = get_product_template_registry()
    catalog = get_template_catalog()

    # ── lib 层自检 ────────────────────────────────────────────────────
    issues.extend(artifacts.validate())
    issues.extend(capabilities.validate())
    issues.extend(algorithms.validate(available_tools=available_tools))
    issues.extend(validate_model_library())
    # 参数一致性门（§43 parity）：显式传 tool_registry 才做 schema 级
    # 对账（构建注册表是校验专用成本；默认轻量）。
    if tool_registry is not None:
        issues.extend(
            validate_algorithm_tool_parameter_parity(tool_registry=tool_registry))
    else:
        from app.lib.gis.parameter_contracts import get_parameter_contract_registry
        issues.extend(get_parameter_contract_registry().validate())

    # ── MapModel：artifact 引用 + 组件类型合法 ────────────────────────
    valid_components = set(ComponentType.__args__) if hasattr(ComponentType, "__args__") else set()
    for mid in models.all_ids:
        model = models.get(mid)
        assert model is not None
        for ref in model.accepted_artifact_types:
            if not artifacts.has(ref):
                issues.append(f"map model {mid}: unknown artifact type {ref}")
        for comp in model.recommended_components:
            if valid_components and comp not in valid_components:
                issues.append(f"map model {mid}: unknown component type {comp}")
        for kind in model.supported_template_kinds:
            if kind not in {"basemap", "symbology", "layout", "thematic", "composite"}:
                issues.append(f"map model {mid}: unknown template kind {kind}")
        for target in model.geometry_layer_types.values():
            if target not in {"circle", "fill", "line", "heatmap", "raster",
                              "symbol", "fill-extrusion"}:
                issues.append(f"map model {mid}: invalid geometry layer type {target}")

    # ── Capability：compatible_map_models 引用 + 至少一个算法 ─────────
    for cap_id in capabilities.all_ids:
        cap = capabilities.get(cap_id)
        assert cap is not None
        for mm in cap.compatible_map_models:
            if models.resolve(mm) is None:
                issues.append(f"capability {cap_id}: unknown map model {mm}")

    # ── Algorithm：compatible_map_models 引用 ─────────────────────────
    for algo_id in algorithms.all_ids:
        algo = algorithms.get(algo_id)
        assert algo is not None
        for mm in algo.compatible_map_models:
            if models.resolve(mm) is None:
                issues.append(f"algorithm {algo_id}: unknown map model {mm}")

    # ── Recipe：capability / map model 引用 ───────────────────────────
    for rid in recipes.all_ids:
        recipe = recipes.get(rid)
        assert recipe is not None
        recipe_caps = list(recipe.preferred_analysis) + list(recipe.optional_analysis)
        for task_caps in (getattr(recipe, "task_optional_analysis", None) or {}).values():
            recipe_caps.extend(task_caps)
        for cap in recipe_caps:
            if not capabilities.has(cap):
                issues.append(f"recipe {rid}: unknown capability {cap}")
        for carto in [recipe.primary_cartography] + list(recipe.secondary_cartography):
            if carto and models.resolve(carto) is None:
                issues.append(f"recipe {rid}: cartography '{carto}' not in MapModelRegistry")

    # ── ProductTemplate：recipe / map model / capability / layer_type ──
    for tid in products.all_ids:
        tpl = products.get(tid)
        assert tpl is not None
        if tpl.recipe_id not in recipes:
            issues.append(f"product template {tid}: unknown recipe {tpl.recipe_id}")
        for role in tpl.layer_roles:
            model = models.resolve(role.resolved_map_model)
            if model is None:
                issues.append(
                    f"product template {tid}: layer role map model "
                    f"'{role.resolved_map_model}' not in MapModelRegistry")
                continue
            if role.layer_type and role.layer_type != model.maplibre_layer_type \
                    and role.layer_type not in model.geometry_layer_types.values():
                issues.append(
                    f"product template {tid}: layer role '{role.role}' layer_type "
                    f"'{role.layer_type}' drifts from model {model.id} "
                    f"({model.maplibre_layer_type})")
            if role.source_capability and not capabilities.has(role.source_capability):
                issues.append(
                    f"product template {tid}: unknown source_capability "
                    f"{role.source_capability}")
            if role.source_artifact and not artifacts.has(role.source_artifact):
                issues.append(
                    f"product template {tid}: unknown source_artifact "
                    f"{role.source_artifact}")

    # ── 组件体系（taxonomy / descriptor / variant / composition）自检 ──
    from app.lib.cartography.component_registry import get_component_registry
    from app.lib.cartography.component_taxonomy import (
        get_component_category_registry,
    )
    from app.lib.cartography.component_templates import (
        get_component_template_registry,
    )
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )

    component_registry = get_component_registry()
    component_templates = get_component_template_registry()
    compositions = get_composition_template_registry()

    issues.extend(get_component_category_registry().validate())
    issues.extend(component_registry.validate())  # 含 renderer 矩阵对账
    issues.extend(component_templates.validate())
    issues.extend(compositions.validate())

    # ProductTemplate：composition_template_id / component_overrides /
    # component_requirements 的引用存在性（声明了就必须指向真实目标）
    for tid in products.all_ids:
        tpl = products.get(tid)
        assert tpl is not None
        if tpl.composition_template_id and not compositions.has(tpl.composition_template_id):
            issues.append(
                f"product template {tid}: unknown composition_template_id "
                f"{tpl.composition_template_id}")
        for ctype in list(tpl.component_overrides) + list(tpl.component_requirements):
            if not component_registry.has(ctype) \
                    and component_registry.get_by_type(ctype) is None:
                issues.append(
                    f"product template {tid}: component reference '{ctype}' "
                    f"not in ComponentRegistry")

    # ── Style templates / composite（catalog 汇总 template registry 校验）──
    issues.extend(catalog.validate())

    return issues


def validate_algorithm_tool_parameter_parity(tool_registry=None) -> List[str]:
    """算法/工具参数一致性门（ADR-0099 §43 parity）。

    声明了 ``parameter_contract_ref`` 的算法，其每个候选工具的 OpenAI
    schema 必须包含契约的全部 **required** 参数名 —— 参数错配是死契约的
    最强信号（工具签名改了、契约没跟，或反之）。

    ``tool_registry`` 缺省时惰性构建真实注册表（本函数是校验专用入口，
    非热路径）。schema 不可得的环境返回空（诚实跳过，不误报）。
    """
    from app.lib.gis.parameter_contracts import get_parameter_contract_registry

    contract_registry = get_parameter_contract_registry()
    issues: List[str] = list(contract_registry.validate())
    algorithms = get_algorithm_registry()

    try:
        if tool_registry is None:
            from app.tools import init_tools
            from app.tools.registry import ToolRegistry

            tool_registry = ToolRegistry()
            init_tools(tool_registry)
        tool_schemas = {
            s["function"]["name"]: s["function"].get("parameters") or {}
            for s in tool_registry.get_schemas()
        }
    except Exception:  # noqa: BLE001
        return issues

    for algo_id in algorithms.all_ids:
        algo = algorithms.get(algo_id)
        assert algo is not None
        if not algo.parameter_contract_ref:
            continue
        contract = contract_registry.get(algo.parameter_contract_ref)
        if contract is None:
            continue  # algorithm_registry.validate 已报悬空引用
        for tool_name in algo.tool_candidates:
            schema = tool_schemas.get(tool_name)
            if schema is None:
                continue  # 工具存在性由 algorithms.validate(available_tools) 管
            props = set((schema.get("properties") or {}).keys())
            missing = [p.name for p in contract.parameters
                       if p.required and p.name not in props]
            if missing:
                issues.append(
                    f"algorithm {algo_id}: tool {tool_name} schema 缺契约必填"
                    f"参数 {missing}（parameter contract "
                    f"{algo.parameter_contract_ref}）")
    return issues


__all__ = ["validate_gis_library", "validate_algorithm_tool_parameter_parity"]
