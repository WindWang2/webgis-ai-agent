# G3D02 TEST MATRIX — 测试矩阵（基线 1115d683a → 本分支）

## Python（tests/，pytest 串行实跑）

| 域 | 文件 | 覆盖 | 结果 |
|---|---|---|---|
| v1.7 schema | `tests/unit/test_mapspec_schema_v17_scene_assets.py`（新，32 例） | 版本升级/identity upgrader/byte-stable round-trip；Model3dMapSpecSource 形状（format 词表/unknown 键披露）；scene.assets 预算/必填/垂直词表；质量门（悬空/错型/无 url-ref/重复 id/锚点域/scale/证据 warning/预算）；coordinator 三阻塞码；envelope `_data_dependency` model3d 投影；store_data 分类 | 32 passed |
| 生命周期事务 | `tests/unit/test_scene_assets_lifecycle_mutations.py`（新，12 例） | 整表替换/保留 siblings/None 拒绝/空表清空/悬空整笔拒绝 LKG 不变/错型/重复 id/锚点越界/scale≤0/revision 单调/缺座建 3d 档/图层源零触碰 | 12 passed |
| 工具面 | `tests/unit/test_scene_tools.py`（+3 例） | set_scene_assets 会话校验/注册+替换+清空/悬空拒绝 | 15 passed |
| parity corpus | `tests/cartography/test_mutation_parity_corpus.py`（+2 case） | SetSceneAssetsIntent 写入-清空 + 悬空阻塞（golden 再生） | 58 passed |
| 契约三重锁 | ts_projection / render_work corpus / carto_ir corpus / renderer_capability / semantic-checks 契约 v3 | Python↔JSON↔TS 词表（model3d/scene_assets_3d/版本 1.7/阻塞码）逐位一致 | 全绿 |
| cartography 全域 | tests/cartography | 回归 + master 既有 parity golden 漂移（#1617 representation_report 未再生）顺手修复 | 2804 passed |

## TypeScript（vitest，jsdom；MapLibre mock 契约锁定）

| 域 | 文件 | 覆盖 | 结果 |
|---|---|---|---|
| geo 数学 | `scene3d-geo.test.ts`（24 例） | mercator 投影与 MapLibre 真库 oracle 逐位一致（5 组经纬高 + meter 单位）；放置矩阵（锚点/ENU 三轴/heading/glTF 轴系校正/scale）；矩阵逆/视锥平面提取/平面局部化；ECEF/ENU 旋转；SSE 公式/球距 | 24 passed |
| glTF 解析 | `scene3d-gltf-parser.test.ts`（13 例） | GLB 容器 chunk；fixture 往返（indexed/non-indexed/材质）；typed 拒绝（1.0 版本/必需扩展/坏 JSON/accessor 越界/索引越界/预算）；GLB magic 拒收 | 13 passed |
| 3D Tiles + 调度 | `scene3d-tiles.test.ts`（14 例） | tileset 解析（region/box/相对 URI/1.1 content.uri）；拒绝（b3dm/external/1.0 url/坏版本/必需扩展/策略违例 URI）；SSE 调度（远根/近细化/REPLACE 渐进兜底/视锥剔除/预算截断/unload 差集） | 14 passed |
| 管理器生命周期 | `scene3d-manager.test.ts`（11 例） | 挂载（custom layer + fetch + build）/幂等 re-sync/revision 换代重挂/显隐不重挂/spec 移除卸载/2d 卸载/悬空 typed unavailable/404 load_failed 清理/ref-only 边界/move 监听挂摘/dispose 幂等 | 11 passed |
| runtime×ACK 集成 | `runtime.scene3d.test.ts`（4 例） | model3d 不进 MapLibre sources 但 applied spec 保留；幂等；per-source ACK applied/stale（render_work.v1 同代对账）；scene3d_assets 状态块入 observation payload | 4 passed |
| 契约镜像 | contract-manifest（render-protocol + carto-ir）/version 词表/catalogue | TS 侧三重锁 + 前端版本协商 1.7 + 命令目录 set_scene_assets | 全绿 |
| 共享 mock | `test/maplibre-mock-surface.test.ts` | scene3d 消费的 getCanvasContainer/queryTerrainElevation 进 mock（静态扫描契约） | 1 passed |

## E2E（Playwright，真实浏览器 + 真实 WebGL/SwiftShader）

| 旅程 | 覆盖 | 结果 |
|---|---|---|
| j9（新，@smoke） | SSE step_result（v1.7 mapspec + fly_to 生产投递路径）→ GLB fetch → custom layer 真渲染（hasRenderFrame 实证）→ dev 观察窗 mounted → 3D toggle（真实用户路径）→ 屏心点击 → 3D 检视卡 + feature_selected IR 上行（scene3d:campus）→ 无 JS/WebGL fatal；截图 `e2e/artifacts/j9-scene3d.png` | 1 passed（9.4s） |
| e2e-lanes 注册一致性 | j9 登记 + @smoke 映射 | 13 passed |

## 负例覆盖（散布在上述各域）
断网 404（manager load_failed + typed 清理）、非法 glTF（版本/JSON/越界/索引/预算六型）、非法 tileset（旧内容格式/external/坏形状/策略 URI）、悬空 model3d 源（coordinator 阻塞 + 质量门 + 运行时 typed unavailable）、ref-only 源（水合边界 unavailable）、重复 id/越界放置/scale≤0（写路径整笔拒绝）、2d 模式卸载、dispose 二次幂等、revision 换代在途作废。

## 无法在本机运行的验证（诚实披露）
- 无 GPU 真机矩阵：WebGL 验证基于 headless Chromium + SwiftShader（软渲染）；桌面独显/移动 GPU 行为未实测。
- Playwright 其余 8 条 journey（j1-j8）：未在本轮全量重跑（改动面无交叉 —— 仅改共享 mock 增补方法、j9 新增；PR 前由 CI lane 承担全量）。命令：`cd frontend && pnpm exec playwright test`。
- cartographic-observation POST 对纯 3D spec（零图层）不签发 —— 指纹认证链依赖 HUD 图层代指纹（#1389 C09）；集成层已用 collectRenderObservation 直测覆盖，浏览器层观察缺口记为已知限制。
