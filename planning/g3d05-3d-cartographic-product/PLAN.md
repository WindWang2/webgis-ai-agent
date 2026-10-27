# G3D05 实施计划 —— 三维智能制图产品（场景化 UI · StoryMap · 导出质量）

任务边界（工作单 §A）：**从正确三维场景到可交付制图产品**。不重造 #1763 模板资产、#1755 导出 conformance、#1617 语义缩放；不写科学分析核心算法、不做瓦片服务、不接管渲染底层/Agent 状态机。

## 交付分解（D1–D7 → 里程碑映射）

| # | 交付 | 里程碑 | 内容摘要 |
| --- | --- | --- | --- |
| D1 | 3D 组合模板 pack | M2 | `composition_packs/scene_3d.py`：`composition.terrain_3d_scene`（地形+晕渲+等高线）、`composition.urban_3d_skyline`（建筑挤出+行政区统计）、`composition.viewshed_scene_3d`（视域+地形）。输出目标仅 `interactive`+`png`（矢量目标由 D4 显式降级而非声明支持）。新组件类型 `scene_disclosure`（chrome，3D 模板必填）与 `elevation_profile`（panel）全链路接线：COMPONENT_TYPES + component_registry + ABI META + component_renderers 真值矩阵 + component_templates 变体。 |
| D2 | scene_quality 3D 扩展 | M4 | 新代码：`SCENE_SCALE_UNTRUSTWORTHY_PERSPECTIVE`（pitch>10° 且存在 scale_bar 且无 scene_disclosure → warning）、`SCENE_LABEL_OCCLUSION_RISK`（pitch>45° 且有 label 图层 → info）、`SCENE_EXPORT_VECTOR_DEGRADE`（mode=3d 且输出目标含矢量 → info，须有降级说明）。锁进 test_scene_quality_gate。 |
| D3 | 用户场景 mutation 通道 | M3 | `SetSceneBody(intent="set_scene")` 入 MapSpecMutationUnion + body_to_intent → 复用 SetSceneIntent 事务管线（幂等/409/503 语义与既有一致）。用户与 Agent 同一真相。 |
| D4 | 导出诚实语义 | M5 | `render_diagnostics` 增 `scale_unreliable_perspective`、`svg_3d_perspective_degrade`；`product_manifest` 增 `scene` 溯源块（mode/camera/exaggeration/terrain source + 比例尺可信度判定）；前端 `scene-export-disclosure.ts` 纯函数 + exporter/export-chrome 接线（透视比例尺加"仅供参考"披露、SVG 3D 降级为 2.5D 矢量 + 收据）。RenderWork ACK gate 语义沿用（导出前 settle 既有）。 |
| D5 | 前端场景控制器 | M3 | `components/map/scene-controller.tsx`：2D/2.5D/3D 分段控制（读 committed spec.scene，写 D3 mutation）、exaggeration 滑杆（0.25–10，无 terrain 证据禁用+解释）、pitch/bearing 步进（0–85/±180）、垂直基准信息行（vertical_unit+elevation_ref，缺证据显示"未声明"）、性能模式开关（写 spec 元数据，不伪装渲染 LOD）。键盘可达、i18n（useT）、明暗主题、reduced-motion。 |
| D6 | 前端制图组件 | M2/M3 | `map-components/scene-disclosure.tsx`（场景披露卡：模式/夸张/垂直基准/比例尺可信徽标，浮动+dock 可用）、`map-components/elevation-profile-panel.tsx`（高程剖面面板：inline 序列或 `ref:profile-*` 工件，ChartCore 线图 + 距离轴；G3D03 合并前用合成 fixture）。注册进 registry + CHROME_RENDERABLE 同步 + export parity 真值矩阵。 |
| D7 | StoryMap 场景章节 | M6 | spec 升 `1.1`（additive）：`CameraKeyframe.scene {mode?,exaggeration?}`、`StoryChapter.visible_layer_ids`（≤32）；camera_planner 增 arc 场景默认；编译器透传；持久化：`story_map_assets` 表 + `POST/GET /storymap/sessions/{id}/specs`（所有权守卫，版本化）；前端 story-view 逐章应用场景 + **用户接管相机即暂停 autoplay**（恢复按钮），可见图层按章切换。 |

M7（三套产品级样例）以契约级 E2E 落地：城市三维建筑+行政区统计 / 山地 DEM+剖面+视域 / 2D-3D 双窗+StoryMap 导出 —— pytest 场景工厂（合成数据，声明为合成）+ vitest 组件级， golden 语义断言（不做像素 diff，沿用仓库既有语义 parity 纪律）。

## 实施顺序与验证门

Phase 0 规划落盘（本文件+BASELINE+OVERLAP+RISKS）→ Phase 1 D1+D2（后端模板与质量门，pytest cartography）→ Phase 2 D3+D4（mutation 通道与导出语义，pytest unit）→ Phase 3 D7 后端（storymap，pytest unit）→ Phase 4 D5+D6 前端（vitest+tsc）→ Phase 5 D4/D7 前端（导出披露+播放器）→ Phase 6 ADR+catalog 再生成+M7 场景 → Phase 7 全量本地验证（pytest 目标子集 + vitest ≤2 workers + tsc + eslint + 负例）→ Phase 8/9 两轮独立 review → Phase 10 rebase+push+PR。

每个 Phase 一个原子 commit；负例要求：撤掉 D2 护栏断言变红、mutation 非法 scene 400、无证据夸张禁用、SVG 3D 无收据变红、StoryMap 引用不存在章节被拒。

## 资源纪律

- 复用主仓 `.venv`（Python 3.13.17，同一依赖集）与 `frontend/node_modules` 符号链接（同基线 SHA，package.json 无差）。
- pytest 串行小批（`-m cartography` 目录子集 + 定向单测）；vitest `--maxWorkers=2`；tsc/eslint 单进程。
- 编译类操作（如需）走 `flock /tmp/webgis-build.lock`；`NODE_OPTIONS=--max-old-space-size=4096`。
