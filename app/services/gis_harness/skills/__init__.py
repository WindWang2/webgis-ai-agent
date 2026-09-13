"""GIS Skill / Procedure Library V1 —— 领域作业方法知识层（ADR-0182）。

**Skill = reusable domain procedure**：Agent 完成空间分析/遥感分析/制图
目标时可调用、组合、约束、复用的领域作业方法知识。位于 goal 与
capability/execution planning 之间：

    Goal → Skill(procedure) → capability requirements → tools/algorithms

红线（详见 ADR-0182 与 docs/dev/gis-skill-v1-decisions.md）：

- 引用不复制：capability/recipe/ontology/角色词表全部引用既有 registry；
- 不是第六套平行 registry、不是第二个 planner、不是第二个 agent loop；
- 描述 procedure 而非 CoT：结构化契约 + 有界 guidance（≤240 字）；
- 确定性优先：resolver/composition/replay 零 LLM；
- 技能是版本化资产：YAML + extra=forbid + 启动 fail loud；运行期不自改。

与 `app/skills/` 的边界：那里是 chat 层 prompt 技能（.md 文档 + .py 可执行
脚本，遗留形态）；本包是结构化、版本化、可校验的 GIS 领域技能资产。
两套 "skill" 命名在 UBIQUITOUS_LANGUAGE.md 中显式区分。
"""
from app.services.gis_harness.skills._base import SkillAssetModel
from app.services.gis_harness.skills.bridges import (
    missing_capabilities,
    project_capability_plan_inputs,
    project_product_requirements,
)
from app.services.gis_harness.skills.composition import (
    CompositionMember,
    SkillComposition,
)
from app.services.gis_harness.skills.contract import (
    SKILL_SCHEMA_VERSION,
    CapabilityRequirement,
    SkillContract,
)
from app.services.gis_harness.skills.loader import (
    SkillLibrary,
    SkillLibraryError,
    get_skill_library,
    load_skill_library,
    reset_skill_library,
)
from app.services.gis_harness.skills.procedure_ir import SkillProcedure
from app.services.gis_harness.skills.replay import replay_procedure
from app.services.gis_harness.skills.resolver import SkillResolver
from app.services.gis_harness.skills.semantics import (
    StatisticalSemantics,
    TemporalSemantics,
)

__all__ = [
    "SKILL_SCHEMA_VERSION",
    "SkillAssetModel",
    "SkillContract",
    "CapabilityRequirement",
    "SkillProcedure",
    "StatisticalSemantics",
    "TemporalSemantics",
    "SkillComposition",
    "CompositionMember",
    "SkillResolver",
    "SkillLibrary",
    "SkillLibraryError",
    "get_skill_library",
    "load_skill_library",
    "reset_skill_library",
    "replay_procedure",
    "missing_capabilities",
    "project_product_requirements",
    "project_capability_plan_inputs",
]
