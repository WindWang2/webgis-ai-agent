"""GIS Harness —— GIS 领域智能层（domain intelligence layer）。

职责边界（见 docs/gis-harness.md）：

- Pi                    = Agent Runtime（语义理解、工具调用发起方）
- GIS Harness（本包）    = GIS 领域智能：intent / recipe / eligibility /
                           fallback / product plan / components / evidence
- ToolRegistry          = GIS 能力面
- ToolDispatchService   = 执行 + ref + evidence（不改）
- MapSpec               = 唯一制图期望状态（不建第二套）
- MapLibre              = Runtime renderer

本包不执行工具、不持有第二套 runtime state；所有产出一等公民可序列化、
确定性、可进 Harness evidence。
"""
from app.services.gis_harness.intent import (
    MapRequestIntent,
    ScopeIntent,
    SubjectIntent,
    merge_intent_hints,
    resolve_map_request_intent,
)
from app.services.gis_harness.recipes import (
    CartographyRecipe,
    EligibilityReport,
    RecipeRegistry,
    get_recipe_registry,
    reset_recipe_registry,
)
from app.services.gis_harness.components import (
    CartographyComponent,
    build_default_components,
)
from app.services.gis_harness.planner import (
    MapProductPlan,
    MapProductPlanner,
)
from app.services.gis_harness.template_catalog import (
    TemplateCatalog,
    get_template_catalog,
)
from app.services.gis_harness.template_selector import (
    TemplateSelector,
)

__all__ = [
    "MapRequestIntent",
    "ScopeIntent",
    "SubjectIntent",
    "merge_intent_hints",
    "resolve_map_request_intent",
    "CartographyRecipe",
    "EligibilityReport",
    "RecipeRegistry",
    "get_recipe_registry",
    "reset_recipe_registry",
    "CartographyComponent",
    "build_default_components",
    "MapProductPlan",
    "MapProductPlanner",
    "TemplateCatalog",
    "get_template_catalog",
    "TemplateSelector",
]
