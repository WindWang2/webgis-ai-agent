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

    # ── V3：GIS Task Ontology 引用完整性（capability/artifact/map model
    #    /data role/task family 全部对账单一事实源）──────────────────────
    from typing import get_args as _typing_get_args

    from app.services.gis_harness.gis_ontology import get_task_ontology
    from app.services.gis_harness.intent import TaskType as _TaskType
    from app.services.gis_harness.workflow_schema import DATA_ROLES as _DATA_ROLES

    issues.extend(
        f"gis_ontology: {violation}"
        for violation in get_task_ontology().validate(
            capability_exists=capabilities.has,
            artifact_type_exists=artifacts.has,
            map_model_exists=lambda m: models.resolve(m) is not None,
            data_role_vocabulary=tuple(_DATA_ROLES),
            family_vocabulary=_typing_get_args(_TaskType),
        )
    )

    # ── V8（ADR-0136）：Unified Capability Graph 机器闸 ──────────────
    #   图级 dangling/duplicate/词表违规（model→capability implements、
    #   algorithm exposed_by 等跨 registry 边的完整性）。error 级 fatal；
    #   warning 级留痕（随治理收敛为 fatal）。
    try:
        from app.services.gis_harness.capability_graph import validate_graph

        for issue in validate_graph():
            prefix = f"capability_graph[{issue.severity}]: "
            issues.append(f"{prefix}{issue.code}: {issue.detail}")
    except Exception as exc:  # noqa: BLE001 — 图构建失败按违规披露
        issues.append(f"capability_graph: validation unavailable: {exc}")

    # ── V4：Methodology Registry 引用完整性（Epic workflow-v4）─────────
    #   方法族/候选方法的 capability/algorithm/artifact/task 引用全部
    #   对账单一事实源；悬空 fatal（与 ontology 同级）。
    from app.services.gis_harness.workflow_v4.methodology import (
        get_methodology_registry,
    )

    issues.extend(
        f"methodology: {violation}"
        for violation in get_methodology_registry().validate(
            ontology_task_exists=get_task_ontology().has,
            capability_exists=capabilities.has,
            algorithm_exists=algorithms.has,
            artifact_type_exists=artifacts.has,
        )
    )
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
        # Workflow V2（Goal C / R1-A7）：workflow 画像静态完整性校验 —— 角色
        # 词表 / 义务 precondition 注册存在性 / 降级分类 / artifact 类型引用
        # 全部在此收口（此前是死代码，typo 的 precondition id 会静默降级为
        # unknown，科学门槛无痕消失）。
        wf_profile = getattr(recipe, "workflow", None)
        if wf_profile is not None:
            from app.lib.gis.scientific_preconditions import precondition_exists
            from app.services.gis_harness.workflow_schema import validate_workflow_profile

            issues.extend(
                f"recipe {rid}: {violation}"
                for violation in validate_workflow_profile(
                    wf_profile,
                    capability_exists=capabilities.has,
                    artifact_type_exists=artifacts.has,
                    precondition_exists=precondition_exists,
                )
            )
        # V3（GIS Task Ontology）：recipe.ontology_tasks 必须命中本体
        # 登记表 —— 悬空的本体引用会让 opt-in 路由层静默失效。
        for task_id in (getattr(recipe, "ontology_tasks", None) or []):
            from app.services.gis_harness.gis_ontology import get_task_ontology
            if not get_task_ontology().has(task_id):
                issues.append(f"recipe {rid}: unknown ontology task {task_id}")

    # ── V3：Recipe 分层组合（family / composite / scenario）───────────
    # 用**传入的** recipes registry 现建投影（review A5：单例投影 + 参数
    # registry 会在测试/隔离场景下误报或漏报）。
    from app.services.gis_harness.workflow_families import WorkflowFamilyRegistry

    family_layer = WorkflowFamilyRegistry()
    family_layer.load(recipes)
    issues.extend(
        f"workflow_families: {violation}"
        for violation in family_layer.validate(recipes)
    )

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
    # V7（Goal 08）：组件目录组合智能自检 —— 类目→主表达亲和表
    # referential integrity（id 必须可解析为 native 模型）。
    from app.lib.cartography.composition_selection import validate_affinity_table
    issues.extend(
        f"composition_selection: {violation}"
        for violation in validate_affinity_table()
    )

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

    # ── Epic 11：方法知识层（taxonomy / descriptors / provenance / graph）
    #    全部审定表对账 canonical 词表；graph 悬空引用在 build 期 fail-closed。
    issues.extend(
        f"methodology_intel: {violation}"
        for violation in _validate_methodology_intelligence(
            ontology=get_task_ontology(),
            capabilities=capabilities,
            algorithms=algorithms,
            artifacts=artifacts,
            models=models,
            component_registry=component_registry,
            compositions=compositions,
        )
    )

    return issues


def _validate_methodology_intelligence(
    *,
    ontology: Any,
    capabilities: Any,
    algorithms: Any,
    artifacts: Any,
    models: Any,
    component_registry: Any,
    compositions: Any,
) -> List[str]:
    """知识层对账（deferred imports；悬空/违例以 methodology_intel: 前缀上报）。"""

    from app.lib.gis.methodology.descriptors import (
        get_method_descriptor_registry,
    )
    from app.lib.gis.methodology.graph import (
        GraphBuildError,
        get_knowledge_graph,
    )
    from app.lib.gis.methodology.provenance import (
        provenance_exists,
        validate_ledger,
    )
    from app.lib.gis.methodology.taxonomy import get_task_taxonomy
    from app.services.gis_harness.workflow_schema import DATA_ROLES
    from app.services.gis_harness.workflow_v4.methodology import (
        get_methodology_registry,
    )

    issues: List[str] = list(validate_ledger())

    def _comp_exists(c: str) -> bool:
        return component_registry.has(c) or component_registry.get_by_type(c) is not None

    def _model_exists(m: str) -> bool:
        return models.resolve(m) is not None

    methods_reg = get_methodology_registry()
    issues.extend(
        f"taxonomy: {v}"
        for v in get_task_taxonomy().validate(
            task_exists=ontology.has,
            family_exists=lambda f: methods_reg.family(f) is not None,
            method_exists=lambda m: methods_reg.method(m) is not None,
            artifact_type_exists=artifacts.has,
            map_model_exists=_model_exists,
            component_exists=_comp_exists,
            provenance_exists=provenance_exists,
            data_role_vocabulary=tuple(DATA_ROLES),
        )
    )
    issues.extend(
        f"method_descriptors: {v}"
        for v in get_method_descriptor_registry().validate()
    )
    # 方法增强层覆盖完整性：V4 每个候选方法必须有 descriptor（纯加法演进
    # 的伴随义务——新增方法不补条目在这里红）。
    desc_reg = get_method_descriptor_registry()
    for fam in methods_reg.families():
        for m in fam.candidate_methods:
            if not desc_reg.has(m.method_id):
                issues.append(
                    f"method_descriptors: 缺少 V4 候选方法的增强条目 "
                    f"{m.method_id}")
    # viz bridge + template spec 对账（悬空 fatal；R1-F5 分歧走披露）
    from app.lib.cartography.template_intelligence import (
        get_template_spec_registry,
    )
    from app.lib.gis.methodology.viz_bridge import validate_bridge
    issues.extend(
        f"viz_bridge: {v}"
        for v in validate_bridge(
            artifact_type_exists=artifacts.has,
            map_model_exists=_model_exists,
            component_exists=_comp_exists,
        )
    )
    from app.services.gis_harness.workflow_schema import DATA_ROLES as _ROLES
    issues.extend(
        f"template_spec: {v}"
        for v in get_template_spec_registry().validate(
            composition_exists=compositions.has,
            category_exists=get_task_taxonomy().has,
            family_exists=lambda f: methods_reg.family(f) is not None,
            artifact_type_exists=artifacts.has,
            component_exists=_comp_exists,
            capability_exists=capabilities.has,
            data_role_vocabulary=tuple(_ROLES),
        )
    )
    try:
        graph = get_knowledge_graph()
        if graph.node_count == 0 or graph.edge_count == 0:
            issues.append("knowledge_graph: 空图（投影断裂）")
    except GraphBuildError as exc:
        issues.append(f"knowledge_graph: build failed: {exc}")
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
