"""3D 任务多步编排 DAG（G3D04 M5）。

把"3D 资源→资格→场景→分析→挂载→判定"编成**资源受控的 typed DAG**：

- 步骤 = capability + 真实可执行动作（既有工具名 / SetScene/SetView 通道），
  不发明第二执行器——执行由 harness 既有状态机/dispatch 承担，本模块
  只做计划编制与依赖/预算/恢复语义；
- 依赖边由 artifact 类型推断（producer 输出 ∩ consumer 输入，与
  ``plan_graph.infer_dependency_edges`` 同规）；
- 每步携带取消安全点（safety_point：步骤边界可取消）、幂等键面
  （dedupe_key：mutation_id/op_id 语义）与恢复锚点载荷（resume_payload，
  交给 ``resume_anchor.build_anchor`` 的 ref 清单纪律）；
- **ref-first**：大对象（DEM/mesh/GeoJSON）只以 ref 相连，绝不进入
  LLM token 面。

边界（诚实）：``attach_scene_asset`` 步骤是 typed 占位（G3D02 渲染面）；
``acquire_elevation`` 的资源治理属 G3D01——本 DAG 引用其产出 ref。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

SCENE3D_PLAN_SCHEMA = "scene3d_plan.v1"

#: 步骤动作词表（全部映射既有通道；无第二执行语义）。
STEP_ACTIONS = (
    "acquire_elevation",     # 数据面：注册 raster-dem 源（G3D01 管线产出 ref）
    "qualify_evidence",      # scene3d.evidence 核实（只读）
    "build_scene",           # apply_scene_representation → SetSceneIntent
    "attach_scene_asset",    # G3D02 渲染面（typed 占位，unavailable）
    "run_analysis",          # run_3d_analysis → viewshed_analysis 真实 dispatch
    "attach_result",         # 结果上屏（部分：依赖 G3D03 产物面）
    "final_verdict",         # scene3d.verdict 双门判定
    "fly_camera",            # fly_camera → SetViewIntent（user-wins 守卫）
)

#: 步骤状态词表（与 plan_graph.PlanNodeStatus 对齐）。
STEP_STATUSES = ("pending", "ready", "running", "complete", "skipped",
                 "unavailable", "failed")

#: 依赖推断用的 artifact 类型（复用既有词表，不新增）。
_STEP_ARTIFACTS: Dict[str, Dict[str, tuple]] = {
    # action: {"inputs": (...), "outputs": (...)}
    "acquire_elevation": {"inputs": (), "outputs": ("raster_surface",)},
    "qualify_evidence": {"inputs": ("raster_surface",), "outputs": ("scene_evidence",)},
    "build_scene": {"inputs": ("scene_evidence",), "outputs": ("scene_config",)},
    "attach_scene_asset": {"inputs": ("scene_config",), "outputs": ("scene_config",)},
    "run_analysis": {"inputs": ("raster_surface", "scene_config"), "outputs": ("raster_surface",)},
    "attach_result": {"inputs": ("raster_surface", "scene_config"), "outputs": ("raster_surface",)},
    "final_verdict": {"inputs": ("scene_config", "raster_surface"), "outputs": ("verdict",)},
    "fly_camera": {"inputs": ("scene_config",), "outputs": ("scene_config",)},
}


class Scene3DStep(BaseModel):
    """DAG 中的一个步骤（typed；可取消/幂等/可恢复）。"""

    model_config = {"extra": "forbid"}

    step_id: str
    action: str                              # STEP_ACTIONS
    tool_name: str = ""                      # 既有工具名（执行映射面）
    depends_on: List[str] = Field(default_factory=list, max_length=8)
    status: str = "pending"                  # STEP_STATUSES
    optional: bool = False
    cancellable: bool = True                 # 步骤边界安全点
    dedupe_key: str = ""                     # 幂等键面（mutation_id/op_id 语义）
    input_refs: List[str] = Field(default_factory=list, max_length=8)
    output_ref: str = ""                     # ref-first：大对象只走 ref
    reason_code: str = ""                    # unavailable/failed/skipped 的 typed 原因
    notes: List[str] = Field(default_factory=list, max_length=4)


class Scene3DPlan(BaseModel):
    """一个 3D 任务的编排计划（纯派生；执行态由 harness 状态机持有）。"""

    model_config = {"extra": "forbid"}

    schema_version: str = SCENE3D_PLAN_SCHEMA
    plan_id: str = Field("", max_length=64)
    goal: str = Field("", max_length=200)
    analysis_goal: str = ""
    steps: List[Scene3DStep] = Field(default_factory=list, max_length=12)
    dropped_cycle_edges: List[List[str]] = Field(default_factory=list, max_length=4)
    notes: List[str] = Field(default_factory=list, max_length=4)

    def step(self, step_id: str) -> Optional[Scene3DStep]:
        for item in self.steps:
            if item.step_id == step_id:
                return item
        return None

    def ready_steps(self) -> List[str]:
        by_status = {s.step_id: s.status for s in self.steps}
        ready = []
        for step in self.steps:
            if step.status != "pending":
                continue
            deps = [by_status.get(dep) for dep in step.depends_on]
            if all(dep in ("complete", "skipped") for dep in deps):
                ready.append(step.step_id)
        return ready


def _infer_edges(steps: List[Scene3DStep]) -> tuple:
    """artifact 类型推断依赖边（与 plan_graph 同规；成环边丢弃并披露）。

    环判定：边 ``step→dep``（step 依赖 dep）成环当且仅当 dep 经**其余**
    依赖边可达 step——检查时排除被测直边本身。
    """
    outputs_of: Dict[str, str] = {}
    for step in steps:
        for artifact in _STEP_ARTIFACTS.get(step.action, {}).get("outputs", ()):
            outputs_of.setdefault(artifact, step.step_id)

    by_id: Dict[str, List[str]] = {s.step_id: list(s.depends_on) for s in steps}
    for step in steps:
        needed = _STEP_ARTIFACTS.get(step.action, {}).get("inputs", ())
        for artifact in needed:
            producer = outputs_of.get(artifact)
            if producer and producer != step.step_id and producer not in by_id[step.step_id]:
                by_id[step.step_id].append(producer)

    dropped: List[List[str]] = []
    for step in steps:
        for dep in list(by_id[step.step_id]):
            others = {k: [d for d in v if not (k == step.step_id and d == dep)]
                      for k, v in by_id.items()}
            if _has_path(others, dep, step.step_id):
                by_id[step.step_id].remove(dep)
                dropped.append([step.step_id, dep])
    return by_id, dropped


def _has_path(by_id: Dict[str, List[str]], src: str, dst: str) -> bool:
    """src 是否可经依赖边到达 dst（≥1 步；自环不算）。"""
    if src == dst:
        return False
    stack, seen = list(by_id.get(src, ())), set()
    while stack:
        node = stack.pop()
        if node == dst:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(by_id.get(node, ()))
    return False


def build_scene3d_plan(
    goal: str,
    *,
    analysis_goal: str = "",
    include_camera: bool = False,
    include_scene_asset: bool = False,
    elevation_ref: str = "",
) -> Scene3DPlan:
    """3D 任务 → 编排 DAG（纯函数；按能力/意图裁剪步骤集）。

    典型链（M7 E2E 用）：

    - 表达链：acquire_elevation → qualify_evidence → build_scene →
      final_verdict（+fly_camera 当 include_camera）；
    - 分析链：上述 + run_analysis → attach_result → final_verdict。
    """
    steps: List[Scene3DStep] = []
    steps.append(Scene3DStep(
        step_id="s3d-elev", action="acquire_elevation",
        tool_name="",  # G3D01 数据面管线（ref 由数据步骤产出）
        output_ref=elevation_ref, reason_code="",
        notes=(["外部依赖：G3D01 DEM 管线产出 raster-dem ref"]
               if not elevation_ref else []),
    ))
    steps.append(Scene3DStep(
        step_id="s3d-qualify", action="qualify_evidence",
        tool_name="get_3d_capabilities",
        input_refs=[elevation_ref] if elevation_ref else [],
        dedupe_key="qualify:evidence",
    ))
    steps.append(Scene3DStep(
        step_id="s3d-scene", action="build_scene",
        tool_name="apply_scene_representation",
        dedupe_key="scene:representation",
    ))
    if include_scene_asset:
        steps.append(Scene3DStep(
            step_id="s3d-asset", action="attach_scene_asset",
            optional=True, status="unavailable",
            reason_code="SCENE_CAP_PENDING_G3D02",
            notes=["3D Tiles/gltf 资产挂载属 G3D02 渲染面（typed 占位）"],
        ))
    if analysis_goal:
        steps.append(Scene3DStep(
            step_id="s3d-analysis", action="run_analysis",
            tool_name="run_3d_analysis",
            input_refs=[elevation_ref] if elevation_ref else [],
            dedupe_key=f"analysis:{analysis_goal}",
        ))
        steps.append(Scene3DStep(
            step_id="s3d-attach", action="attach_result", optional=True,
            status="unavailable",
            reason_code="SCENE_RESULT_ATTACH_PENDING_G3D03",
            notes=["结果栅格上屏依赖 G3D03 科学产物面（当前 stats+披露）"],
        ))
    if include_camera:
        steps.append(Scene3DStep(
            step_id="s3d-camera", action="fly_camera",
            tool_name="fly_camera", optional=True,
            dedupe_key="camera:agent",
            notes=["user-wins 守卫：用户镜头偏离时 typed 抑制"],
        ))
    steps.append(Scene3DStep(
        step_id="s3d-verdict", action="final_verdict",
        tool_name="", cancellable=False,
        dedupe_key="verdict:final",
    ))

    by_id, dropped = _infer_edges(steps)
    for step in steps:
        step.depends_on = sorted(by_id[step.step_id])[:8]

    from app.services.gis_harness.workflow_instance import canonical_fingerprint

    plan = Scene3DPlan(
        plan_id=f"s3d-{len(steps)}-"
                f"{canonical_fingerprint({'g': goal, 'a': analysis_goal, 'c': include_camera, 's': include_scene_asset})[:8]}",
        goal=goal[:200],
        analysis_goal=analysis_goal,
        steps=steps,
        dropped_cycle_edges=dropped,
    )
    if dropped:
        plan.notes.append(f"dropped cycle edges: {dropped}")
    return plan


def resume_payload(plan: Scene3DPlan) -> Dict[str, Any]:
    """恢复锚点载荷（ref 清单纪律：大对象只带 ref 与必要摘要）。"""
    return {
        "schema_version": SCENE3D_PLAN_SCHEMA,
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "analysis_goal": plan.analysis_goal,
        "step_status": {s.step_id: s.status for s in plan.steps},
        "refs": sorted({ref for s in plan.steps for ref in
                        ([s.output_ref] if s.output_ref else []) + s.input_refs
                        if ref})[:128],
        "next_ready": plan.ready_steps(),
    }


def cancel_semantics(plan: Scene3DPlan) -> Dict[str, Any]:
    """取消语义（步骤边界安全点；verdict 步骤不可取消是收口语义）。"""
    return {
        "cancellable_steps": [s.step_id for s in plan.steps if s.cancellable],
        "non_cancellable": [s.step_id for s in plan.steps if not s.cancellable],
        "safety_point": "step_boundary",
        "idempotency": {s.step_id: s.dedupe_key for s in plan.steps if s.dedupe_key},
    }
