"""G3D04 · 三维意图理解与编排（scene3d）。

Agent 侧 3D 语义连接层：确定性 cue 解析 → Requirement IR SceneSpec →
证据核实 → 能力快照 → representation 决策（包既有 plan_scene 决策核）→
Pi 工具编排（编译为既有 MapSpec intent）→ 交互失效/双门 verdict。

纪律：

- **零第二真相**：不造第二决策表/第二 mutation/第二工具面——决策核是
  ``app.lib.cartography.scene_planning.plan_scene``，mutation 走既有
  SetSceneIntent/SetViewIntent，分析走既有 registry 工具真实 dispatch。
- **零编造**：垂直证据只来自真实数据事实（ref 注册回执/extrusion 采样
  结论）；无 cue 不推断 3D；无证据不建议垂直表达。
- **零前端依赖**：交互感知复用既有 Interaction IR kind（G3D02 拥有前端
  scene3d 面），本包不新增交互词表。
"""
from app.services.gis_harness.scene3d.cues import Scene3DCues, extract_scene3d_cues

__all__ = ["Scene3DCues", "extract_scene3d_cues"]
