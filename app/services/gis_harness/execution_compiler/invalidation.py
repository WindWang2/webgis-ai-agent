"""IR patch → 失效面（C4 / GC-09）—— 最小重算的确定性推导。

「颜色改成蓝色」→ 零科学重算；「换成北京但样式不变」→ 全量数据角色
失效（跨城市场景旧 ref 不得假匹配，GC-06）；「改成每万人密度」→ 分母
角色 + 率步骤 + 下游闭包失效，纯计数步骤复用（证明未重跑无关昂贵计算）。

纪律：与 ``analysis_workflows.recompute``（内容指纹）互补——本层做
**需求语义差分**（哪些角色/步骤的语义身份变了），指纹层做**内容身份**
差分；两层都过才是最小重算（本层输出是执行计划的失效种子上限）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Set, Tuple

from app.services.gis_harness.analysis_workflows.contracts import (
    AnalysisWorkflowContract,
)
from app.services.gis_harness.execution_compiler.contracts import (
    InvalidationScope,
)
from app.services.gis_harness.requirement_ir.contracts import (
    RequirementDocument,
)

__all__ = ["patch_invalidation_scope", "_change_classes"]


def _snap(doc: RequirementDocument) -> Dict[str, Any]:
    """IR 语义身份快照（style 面与科学面分组）。"""
    spec = doc.intent
    core = spec.core
    return {
        # ── style 面（永不触发科学重算）─────────────────────────────
        "style": {
            "palette": spec.representation.palette,
            "geometry_kind": spec.representation.geometry_kind,
            "hidden": tuple(sorted(spec.representation.hidden_layer_ids)),
            "locked": tuple(sorted(spec.representation.locked_layer_ids)),
            "pins": {k: spec.representation.pinned_channels[k]
                     for k in sorted(spec.representation.pinned_channels)},
            "components": (
                spec.components.title, spec.components.legend,
                spec.components.scale_bar, spec.components.north_arrow,
                spec.components.labels, spec.components.attribution),
            "output_purpose": spec.output.output_purpose,
            "dpi": spec.output.dpi,
            "camera": (spec.scene.camera_pitch, spec.scene.camera_bearing,
                       spec.scene.exaggeration),
        },
        # ── 科学面（数据/参数身份）──────────────────────────────────
        "aoi": (spec.aoi.name, spec.aoi.level, spec.aoi.geometry_ref,
                core.scope.name),
        "subject": (spec.subject.type, spec.subject.category,
                    tuple(sorted(spec.datasets))),
        "measure": (
            tuple((m.id, m.statistic, m.denominator) for m in spec.measures),
            spec.statistics.dimension, spec.statistics.group_by,
            spec.statistics.normalization, spec.statistics.denominator),
        "time": (spec.time.range_start, spec.time.range_end,
                 spec.time.granularity, spec.time.series,
                 spec.time.compare_start, spec.time.compare_end),
        "spatial_relation": (
            spec.spatial_relation.kind, spec.spatial_relation.target,
            spec.spatial_relation.distance_m),
        "scene_data": (spec.scene.dimension, spec.scene.terrain_requested,
                       tuple(spec.scene.analysis_goals)),
    }


def _change_classes(
    old: RequirementDocument, new: RequirementDocument,
) -> Dict[str, bool]:
    a, b = _snap(old), _snap(new)
    return {
        "style": a["style"] != b["style"],
        "aoi": a["aoi"] != b["aoi"],
        "subject": a["subject"] != b["subject"],
        "measure": a["measure"] != b["measure"],
        "time": a["time"] != b["time"],
        "spatial_relation": a["spatial_relation"] != b["spatial_relation"],
        "scene_data": a["scene_data"] != b["scene_data"],
    }


def _semantic_change(classes: Dict[str, bool]) -> bool:
    return any(v for k, v in classes.items() if k != "style")


def patch_invalidation_scope(
    old_doc: RequirementDocument,
    new_doc: RequirementDocument,
    *,
    contract: Optional[AnalysisWorkflowContract] = None,
) -> InvalidationScope:
    """old/new IR 差分 → InvalidationScope（纯函数；GC-06/09）。

    角色失效推导：

    - aoi 变化 → **全量角色**（数据是 AOI 切片的产物；跨城市主 ref/
      overlay ref/图层 id 全部失效，不假匹配）；
    - subject 变化 → subject（+facility：同为主题数据面）；
    - measure/statistics 变化 → normalization 进入 per_capita/rate 时
      含 denominator，否则 denominator 不失效（计数→面积密度只动
      boundary 派生步骤，不动 subject ref）；
    - time 变化 → baseline / target_time；
    - spatial_relation 变化 → network（等时/邻近的权重面）。

    步骤失效 = 直接消费失效角色的步骤 + 参数语义受 measure 变化影响的
    步骤（denominator_kind）+ 下游闭包；其余步骤进入 preserved（复用面）。
    """
    classes = _change_classes(old_doc, new_doc)
    semantic_changed = _semantic_change(classes)

    invalidated: Set[str] = set()
    reasons = []

    if classes["aoi"]:
        # 全量角色：旧城市数据 ref 不可假匹配新城市（GC-06）
        if contract is not None:
            invalidated.update(contract.data_contracts)
        else:
            invalidated.update(
                {"subject", "boundary", "network", "facility", "hazard",
                 "receptor", "baseline", "target_time", "denominator",
                 "population"})
        reasons.append("aoi_changed: all data roles invalidated "
                       "(cross-city refs never match by assumption)")
    if classes["subject"]:
        invalidated.update({"subject", "facility"})
        reasons.append("subject_changed")
    if classes["measure"]:
        new_norm = str(new_doc.intent.statistics.normalization or "none")
        new_stat = tuple(
            m.statistic for m in new_doc.intent.measures)
        if new_norm in ("per_capita",) or "rate" in new_stat or \
                "ratio" in new_stat:
            invalidated.add("denominator")
        reasons.append(
            f"measure_changed(normalization={new_norm})")
    if classes["time"]:
        invalidated.update({"baseline", "target_time"})
        reasons.append("time_changed")
    if classes["spatial_relation"]:
        invalidated.add("network")
        reasons.append("spatial_relation_changed")

    science_steps: Set[str] = set()
    preserved: Set[str] = set()
    if contract is not None:
        step_roles: Dict[str, Set[str]] = {}
        param_steps: Set[str] = set()
        for step in contract.steps:
            roles = {
                v.removeprefix("role:")
                for v in step.role_inputs.values()
                if v.startswith("role:")}
            step_roles[step.step_id] = roles
            # measure 语义直接落参的步骤（分母通道）——口径变化=新计算
            if classes["measure"] and (
                    step.step_params.get("denominator_kind")
                    or any(k in step.step_params for k in
                           ("value_field", "numerator_field"))):
                param_steps.add(step.step_id)
        seeds = {
            s for s, roles in step_roles.items() if roles & invalidated}
        seeds |= param_steps
        # 下游闭包
        consumers: Dict[str, Set[str]] = {}
        for step in contract.steps:
            sources = set(step.depends_on) | {
                v.removeprefix("step:")
                for v in step.role_inputs.values()
                if v.startswith("step:")}
            for src in sources:
                consumers.setdefault(src, set()).add(step.step_id)
        frontier = set(seeds)
        while frontier:
            cur = frontier.pop()
            for nxt in consumers.get(cur, ()):
                if nxt not in science_steps:
                    science_steps.add(nxt)
                    frontier.add(nxt)
        science_steps |= seeds
        preserved = {s.step_id for s in contract.steps} - science_steps

    return InvalidationScope(
        style_changed=classes["style"],
        aoi_changed=classes["aoi"],
        subject_changed=classes["subject"],
        measure_changed=classes["measure"],
        time_changed=classes["time"],
        spatial_relation_changed=classes["spatial_relation"],
        invalidated_roles=tuple(sorted(
            r for r in invalidated if r))[:16],
        science_invalidated_steps=tuple(sorted(science_steps))[:24],
        preserved_steps=tuple(sorted(preserved))[:24],
        reasons=tuple(dict.fromkeys(r for r in reasons if r))[:6],
    )
