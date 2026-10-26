# G3D02 EVIDENCE — 验证证据与性能实测

## 提交链（branch zcode/g3d02-scene-engine-tiles-v1，base 1115d683a）
1. `e42540c5f` 基线快照 + PR 重叠矩阵（去重门禁）
2. `66321361e` M1 摸底 + 设计定案（PLAN/DECISIONS）
3. `f821447f8` M2 MapSpec v1.7 契约面（schema/intent/tool/门/投影 + 32+12+3 测试 + 三重锁同步 + master parity golden 漂移顺手修复）
4. `b318469a2` M3-M6 scene3d 渲染内核（geo/gltf/tiles3d/scheduler/bridge/manager + map-panel 接线 + 62 测试）
5. `252719151` M7 j9 E2E + 像素世界渲染修复（真浏览器实测暴露 Float32Array/世界尺度/raycast 双变换三个真实缺陷）
6. （本轮）独立 review 两轮 5×P0 + 6×P1 清偿（见 INDEPENDENT_REVIEW.md）

## 真实运行证据
- **j9 journey 截图**：`frontend/e2e/artifacts/j9-scene3d.png`（headless Chromium + SwiftShader WebGL：2D 底图 + 红色 3D 模型 + 3D 检视卡）。
- **完整链路断言**（Playwright `--grep journey-9`，1 passed/9.4s）：SSE step_result（v1.7 mapspec + fly_to）→ GLB fetch（fixture 路由）→ custom layer 挂载（渲染帧矩阵实证 hasRenderFrame）→ 3D toggle → 屏心点击 → 3D 检视卡 + `feature_selected` IR POST（layer_id=scene3d:campus）→ 无 JS/WebGL fatal。
- **渲染管线关键数字**（旅程 debug 面实测）：zoom 15.5 → worldSize=23,726,566px；锚点像素位 (18,721,579, 9,737,830)；拾取命中距离→米换算按 W/circumference(φ)。

## 性能实测（本机 Ryzen 9 5900HX / 62GB / 软渲染）
- **调度决策**：21,845 节点 tileset（8 层×4 叉）全遍历 + 预算截断 = **4.6ms**（200ms 节流 tick 内 ~2% 预算；纯函数，node 24 实测）。
- **GLB 挂载路径**（fetch stub + 真解析器 + THREE 构建，vitest 计时）：单资产挂载 31ms（含 2 三角 fixture；解析/构建随复杂度线性，预算上限 5M 三角/256MB 解码量前置拦截）。
- **幂等 re-sync**：22ms（无网络、无重挂 —— manager 账本命中）。
- **three 依赖**：three@0.186.1 exact pin（~600KB gz 量级，map-panel 动态可达路径）；未做 route-level 拆分 —— 首屏 chunks 不受影响（scene3d 经 renderer.ts re-export 由 map-panel 消费，Next 按页 chunk 化）。生产 bundle 逐 chunk 对比未完成（见残余限制）。

## 关键修复清单（评审驱动）
两轮独立 review 的 5×P0 全部修复 + 回归测试锁定：region 弧度（OGC）、glTF 内容 y-up→z-up、ECEF 锚定平移、解码预算前置（两类放大炸弹）、tile entry 保留（LRU 破洞）。P1 六项：setStyle 存活性重挂、流式 body 预算、过期 tile 不入组、失败退避 30s、datum 披露、SetScene 旁路门。详见 INDEPENDENT_REVIEW.md。

## 已知限制与残余风险（诚实披露）
1. **观察链缺口**：纯 3D spec（零图层）不触发 cartographic-observation POST —— 指纹认证链依赖 HUD 图层代指纹（#1389 C09 既有设计）。集成层以 collectRenderObservation 直测覆盖（runtime.scene3d.test.ts）；浏览器层以 dev-only `__scene3dAssets` 窗口断言。
2. **3D Tiles 聚合预算未实现**（review P1-3）：render 集 ≤128 × 单 parse 预算仍可累计大内存；per-asset 字节/三角累计上限是后续项（当前防线：并发 4 + LRU 96 + 每 parse 5M/64MB/256MB）。
3. **WebGL context lost 浏览器事件面**（review P1-5）：maplibre 触发 `webglcontextlost` map 事件，react-map-gl 转发表不含 → 重试弹窗在真实 context lost 下可能不出现（既有 handleMapError 限制，非本分支引入）；scene3d 的清理链在 mapKey bump 路径已接通。
4. **无 GPU 真机矩阵**：WebGL 验证基于 SwiftShader 软渲染；桌面/移动 GPU 行为未实测。terrain 深度遮挡为近似（深度缓冲共享，非逐像素 terrain depth）。
5. **byteStride/sparse accessor**：不支持但当前静默交错错读（review P2-1）—— 记为后续 typed 拒绝项（预算防线已保证不 OOM）。
6. **生产 bundle 逐 chunk 对比**未完成（构建时长超出本轮窗口）；three 增量按依赖树估算。
7. 其余 j1-j8 旅程未本地重跑（无文件交叉；CI lane 承担）。

## 环境事实
pnpm@10.34.9 / node v24.21.0 / three 0.186.1 + @types 0.186.0；Python 3.13 uv venv（requirements.lock）；pytest 9.1.1；构建互斥 /tmp/webgis-build.lock（flock）。
