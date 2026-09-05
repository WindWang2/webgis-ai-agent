"""Recipe Pack 构建套件 —— 领域包的紧凑构造助手。

目标：让每个领域包以「专业语义」为中心组织，摆脱构造样板。所有助手都是
薄包装（直接返回 DSL V2 模型），不引入第二套事实：
- capability id 必须来自 CapabilityRegistry（registry_validation 校验）；
- obligation.precondition_id 必须来自算法层 scientific preconditions；
- 制图元素 id 必须来自 MapModelRegistry / 组件词表。
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from app.services.gis_harness.recipes import (
    CartographyRecipe,
    EligibilityRule,
    RecipeFallback,
)
from app.services.gis_harness.workflow_schema import (
    CompletionRequirement,
    DataRoleRequirement,
    ScientificObligation,
    WorkflowFallbackPolicy,
    WorkflowProfile,
)


# ── WorkflowProfile 构造 ─────────────────────────────────────────────────

def wf(
    domain: str,
    family: str,
    *,
    zh: Iterable[str] = (),
    en: Iterable[str] = (),
    roles: Iterable[DataRoleRequirement] = (),
    obligations: Iterable[ScientificObligation] = (),
    completion: Iterable[CompletionRequirement] = (),
    fallbacks: Iterable[WorkflowFallbackPolicy] = (),
    disclosures: Iterable[str] = (),
    recompute: Iterable[str] = (),
    evidence: Iterable[str] = (),
) -> WorkflowProfile:
    return WorkflowProfile(
        domain=domain,
        workflow_family=family,
        keywords_zh=list(zh),
        keywords_en=[k.lower() for k in en],
        data_roles=list(roles),
        obligations=list(obligations),
        completion_requirements=list(completion),
        fallback_policies=list(fallbacks),
        required_disclosures=list(disclosures),
        recompute_dimensions=list(recompute),
        evidence_requirements=list(evidence),
    )


def role(
    name: str,
    *,
    required: bool = True,
    acquisition: str = "local",
    capability: str = "",
    artifacts: Iterable[str] = (),
    geometry: Iterable[str] = (),
    policy: str = "block",
    disclosure: str = "",
    note: str = "",
) -> DataRoleRequirement:
    return DataRoleRequirement(
        role=name,
        required=required,
        acquisition=acquisition,
        capability_hint=capability,
        accepted_artifact_types=list(artifacts),
        geometry_kinds=list(geometry),
        missing_policy=policy,
        degrade_disclosure=disclosure,
        note=note,
    )


def obl(
    obligation_id: str,
    kind: str,
    *,
    precondition: str = "",
    code: str = "",
    desc: str = "",
    action: str = "warn",
) -> ScientificObligation:
    return ScientificObligation(
        obligation_id=obligation_id,
        kind=kind,
        precondition_id=precondition,
        warning_code=code,
        description=desc,
        on_violation=action,
    )


def done(dimension: str, evidence: str = "", *, required: bool = True) -> CompletionRequirement:
    return CompletionRequirement(dimension=dimension, evidence=evidence, required=required)


def fb(
    reason_code: str,
    *,
    frm: str = "",
    to: str = "",
    downgrade: str = "degraded",
    disclosure: str = "",
    blocks: bool = False,
) -> WorkflowFallbackPolicy:
    return WorkflowFallbackPolicy(
        reason_code=reason_code,
        from_element=frm,
        to_element=to,
        downgrade_class=downgrade,
        disclosure=disclosure,
        blocks_completion=blocks,
    )


# ── 常用完成契约（C7 七维的标准子集）─────────────────────────────────────

def standard_completion(*, uncertainty: bool = False) -> List[CompletionRequirement]:
    dims = [
        done("data", "subject 数据已绑定且必选角色无 block 缺失"),
        done("analysis", "关键能力节点完成（DAG 必选节点无 pending/failed）"),
        done("science", "科学义务满足：无 block_method 违反"),
        done("cartography", "主专题图层与图例/组件落地"),
        done("observed_map", "前端实测渲染有效（render 验证通过）"),
        done("methodology_disclosure", "触发的方法论义务已披露"),
    ]
    if uncertainty:
        dims.append(done("uncertainty_disclosure", "不确定性/精度信息已披露"))
    return dims


# ── 常用角色 ─────────────────────────────────────────────────────────────

def subject_role(*, capability: str = "poi_query", artifacts: Iterable[str] = ("poi_feature_set", "point_feature_set"), geometry: Iterable[str] = ("point",)) -> DataRoleRequirement:
    return role("subject", capability=capability, artifacts=artifacts, geometry=geometry)


def boundary_role(*, required: bool = True) -> DataRoleRequirement:
    return role(
        "boundary", required=required, capability="admin_boundary_query",
        artifacts=("admin_boundary_set", "polygon_feature_set"), geometry=("polygon",),
    )


def denominator_role(*, disclosure: str = "") -> DataRoleRequirement:
    return role(
        "denominator",
        acquisition="data_fabric",
        disclosure=disclosure or (
            "缺少人口/面积等归一化分母：只能评价数量与密度，"
            "不能下人均/率/公平性结论。"
        ),
        note="分母缺失时禁止 per-capita / rate / equity 语义",
    )


def elevation_role(*, required: bool = True) -> DataRoleRequirement:
    return role(
        "elevation", required=required, capability="raster_source",
        artifacts=("raster_surface",), geometry=("raster",),
    )


# ── 制图元素/组件常用集 ──────────────────────────────────────────────────

MAP_COMPONENTS_BASE = ["title", "legend", "north_arrow", "scale_bar", "attribution"]
MAP_COMPONENTS_CONTINUOUS = ["title", "continuous_colorbar", "north_arrow", "scale_bar", "attribution"]
MAP_COMPONENTS_STATS = ["title", "legend", "statistics_panel", "north_arrow", "scale_bar", "attribution"]
MAP_COMPONENTS_CHART = ["title", "legend", "chart_panel", "north_arrow", "scale_bar", "attribution"]


def min_points_rule(element: str, min_points: Optional[int] = None, code: str = "INSUFFICIENT_POINTS") -> EligibilityRule:
    return EligibilityRule(
        element=element, check_points=True, min_points=min_points,
        requires_geometry=["Point", "MultiPoint"], reason_code=code,
    )


def point_fallback(*, when: str = "point_count < threshold", code: str = "INSUFFICIENT_POINTS") -> List[RecipeFallback]:
    return [RecipeFallback(when=when, reason_code=code, use="point_distribution")]
