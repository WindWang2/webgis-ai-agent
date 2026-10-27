# G3D04 · DECISIONS

- **D1 · 代码落点**：新包 `app/services/gis_harness/scene3d/`（主责区）+ 新工具文件 `app/tools/scene_orchestration_tools.py`。不改 G3D02 分支已改文件（mapspec_schema.py / app/services/mapspec/* / scene_tools.py / render_work_envelope.py / app/contracts/mapspec_intents.py / 前端 scene3d、mapspec-runtime）。理由：并行 PR 语义正交、文件邻接，收敛改动面可将合并冲突降为近零。
- **D2 · 不扩大 MutationIntent union**：fly_camera → 既有 `SetViewIntent`（经 mapspec_store.set_view，pitch/bearing 已有）；set_scene_mode/select_terrain_provider/representation 应用 → 既有 `SetSceneIntent`（经 mapspec_store.set_scene）。相机"建议档"随 scene.camera 走 SetScene；事务面单一。
- **D3 · Requirement IR additive**：新 `SceneSpec` section + `OutputSpec.medium`（输出媒介属输出面）+ LockScope 增 `scene_mode`/`scene_camera`。schema 版本不升（v1 只加字段，符合 ADR-0266 "字段只加不改"）。
- **D4 · cue 解析确定性、零 LLM**：`scene3d/cues.py` 双语正则/词表；无 cue → 不推断 3D（M2 红线："分布情况"不隐含 3D）。用户显式 3D 而证据不足 → 偏好保留 + `degrade_reason` 记录（诚实降级，不静默吞偏好）。
- **D5 · 决策核复用**：representation 决策包既有 `plan_scene`（唯一决策表权威），本方向在其上加"用户偏好层"（user explicit + evidence + medium 合成 SceneIntent 输入），不写第二决策表。
- **D6 · SetSceneIntent 回投影**：taxonomy 从 `unbound` 改 `requirement_significant(scene_mode)`（仅 user origin 投影）；semantic_projection 增 SetSceneIntent 分支（从 post-commit spec 绝对值投影 scene.dimension——与 hidden_layer_ids 同构）。agent origin 的 scene mutation 不回投影（agent 决策不是用户偏好）。
- **D7 · 能力一等公民**：新 capability pack `app/lib/gis/capabilities/scene3d.py`（scene_representation/terrain_scene_binding/scene_camera_action，category="presentation"）；plan_graph `_capability_meta` 对 presentation 派生 kind="scene"（additive）。catalog_discover / conformance / capability graph 自然可发现。
- **D8 · 分析不重写**：run_3d_analysis 经 `registry.dispatch("viewshed_analysis", ...)` 调既有工具（单一执行真相、真实证据/MethodUseContract），本工具只做 capability 门 + 编排 + staleness epoch + ref 汇总。
- **D9 · 交互感知零新词表**：复用既有 Interaction IR kind（viewport_settled 含 pitch/bearing、feature_selected、layer_visibility_changed）；不加前端钩子（G3D02 前端领地）。观察点移动 = feature_selected 语义；相机 user-wins = 用户 viewport_settled 晚于 agent 相机动作。
- **D10 · E2E 挂 G05 lab**：新族 F20–F26 走既有 runner（真实 dispatch 缝 + settle），不建第二验收框架；重新 pin 由官方 CLI 完成。
- **D11 · attach_scene_asset 界外**：orchestrator 保留 typed 步骤占位（unavailable + alternatives 指向 G3D02 的 scene 资产面），不实现渲染资产挂载本体。
- **D12 · 资源纪律**：本方向无原生编译；pytest 定向 lane + 显式 timeout；不跑 next build（无前端改动）；vitest 不涉及。

## 执行期补充决策

- **D13 · 平台绑定契约**：scene3d 三能力按 `platform.*` 工具面绑定契约注册（`algorithms/platform.py`，status=planned——presentation 面不伪装 native 分析能力；科学分析仍走 `terrain.viewshed`）。绑定候选成员互斥（`test_platform_capabilities` 纪律）：get_3d_capabilities+run_3d_analysis→scene_representation；apply_scene_representation→terrain_scene_binding；fly_camera→scene_camera_action。新 artifact 类型 `scene_config` 入 artifacts registry（category=scene）。
- **D14 · 相机锁等值放行**：IR scene_camera 锁定的轴上，**等于用户显式偏好值**的下发被放行（执行用户自己的选择是 user-wins 一致行为），偏离值才抑制。viewport 偏离（用户手动调镜头未落 spec）仍整体抑制。
- **D15 · genesis 即锁**：显式维度/相机 cue 在 genesis 直接落 scene_mode/scene_camera user 锁（超替重建路径同样成立——INTENT 分类为"新任务"时无 edit cue patch 通道）；携带旧锁与本话语新表达冲突时新表达 wins（换锁不叠加）。
- **D16 · typed decline 在 dispatch 缝是错误**：run_3d_analysis 的 capability decline 返回 success=False → dispatch isError=True。验收场景按 lab 负例纪律标 `expect_error=True`（decline 必须真的失败，负例注入才有效）；副作用缺席由 mapspec 断言钉住。
- **D17 · lab DSL 扩展**：G05 runner/contracts 增 `interaction_event` step kind（经真实 `ingest_interaction_batch` 摄入，零 HTTP）；projections 增 scene/view/scene3d/interaction 四面（additive，判定内核未动）。
