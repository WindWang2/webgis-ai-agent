# G3DV2-B PR_OVERLAP_MATRIX — 排除矩阵（逐文件/逐职责）

## 原则
已合并实现不重做；开放 PR（#1835）认领内容不复制；有专人 issue 保持归属。

## 逐 PR 排除
| 来源 | 认领职责 | 本 track 动作 |
| --- | --- | --- |
| #1771（已合并） | 底图/DEM 画像、OpenFreeMap、PMTiles→XYZ、合成 DEM、terrain-governance、tile-auth | 只读消费（scene3d 经 spec 读 source），不改其行为 |
| #1834（已合并） | scene3d 内核全部文件、MapSpec v1.7 schema、j9 journey、Picking IR | **在其文件上做增量演进**（严格解析/预算/生命周期/ACK），不重写架构、不新建第二渲染器 |
| #1773（已合并） | 三维量测（剖面/LOS/面积）、垂直基准治理 | 不触碰 app/lib/gis/geo_analysis 与量测 UI |
| #1829（已合并） | Agent 三维编排工具、SceneSpec Requirement IR、user-wins 相机仲裁 | 只读消费 viewCommands/scene-mutation 语义；不新增 planner 工具 |
| #1830（已合并） | scene-controller、scene_disclosure 组件、StoryMap 场景 keyframe、scene-export-disclosure、scene-mutation API | **复用 commitUserScene/mergeSceneRotary 管线**做 per-asset 控制扩展；不改 StoryMap 播放器 |
| #1835（OPEN，A 方向） | DEM 数据供给生产化（app/services/data_fabric DEM、Terrarium XYZ、quota 执行层、可用性发现） | **零接触**：不改 app/services/data_fabric、DEM 瓦片生产、quota 供给面 |
| issues #1797/#1805/#1809–#1811/#1821/#1824/#1826/#1827 | 他人审查 backlog | 不抢修；若 scene3d 正交修复涉及同文件，仅限 scene3d 命名空间内的行为 |

## 文件级写入计划（本 track 预期修改面）
- frontend/lib/map-kit/scene3d/**（gltf-parser/build/asset-manager/custom-layer/status/resource/tiles3d + 测试 + fixtures）
- frontend/lib/map-kit/renderer.ts（仅 3D 生命周期片段，如有必要）
- frontend/components/map/map-panel.tsx、scene-controller.tsx（3D 能力扩展）
- frontend/lib/render-protocol/**（scene ACK 相关扩展 + contract-manifest 再生成镜像）
- frontend/lib/hooks/use-cartographic-observation.ts、frontend/lib/mapspec-runtime/render-observation.ts（纯 3D 指纹门）
- frontend/e2e/journeys/j9-scene3d-assets.journey.ts（扩展断言）
- frontend/lib/telemetry/**（scene3d 预算/降级计数，若需要）
- app/schemas/chat_schema.py、app/lib/cartography/render_work_envelope.py、render_apply_ack.py（**最小 additive** scene 证据 schema）
- tests/cartography/render_work_corpus/**（经 generate.py 再生成）
- frontend/messages/{zh-CN,en-US}/map.json（3D 控制文案）

## 与 C 方向（GIS 三维算法/科学结论）边界
- 不实现水文/可视域等算法；HUD 不展示三维科学结论；垂直语义仅披露（datum/unit），不做精度承诺。
