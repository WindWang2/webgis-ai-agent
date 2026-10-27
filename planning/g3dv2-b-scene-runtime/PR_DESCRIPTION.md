# feat(g3dv2-b): 三维场景运行时工程化 —— 纯 3D 真 ACK · glTF 严格解析 · 跨资产累计预算 · context lost 生命周期 · 工作台交互

## 问题证据（#1834/#1830 EVIDENCE 声明的真实残留，本 PR 逐项清偿）
1. 纯 3D spec（零 2D 图层）不触发 cartographic-observation POST —— 指纹认证链依赖 HUD 图层"代指纹"（use-cartographic-observation.ts:165 `if (!fingerprint) return`）。
2. 3D Tiles 聚合（跨资产累计）预算缺失：仅 per-parse 5M 三角/256MB + per-asset LRU96/并发4，乘积上界仍可累计大内存。
3. WebGL context lost 无浏览器事件面恢复（无 webglcontextrestored 监听；恢复=用户手动重建整图）。
4. byteStride/sparse accessor 静默错读（gltf-parser 从未读 byteStride —— 交错 GLB"解析成功"但顶点为垃圾）。
5. setStyle 清扫 custom layer 不回调 onRemove → 共享 THREE renderer refCount 永不归零（每次 setStyle 循环净增）。
6. 生产 bundle 逐 chunk 对比未完成；scene-controller 无 per-asset 控制；3D 文案 i18n 缺口；j9 定位器永不命中。

## 与既有三维 PR 不重复
- **#1834（渲染内核）**：本 PR 在其文件上增量演进（严格解析/预算/生命周期/ACK 链），不重写渲染器、不建第二 Cesium/SceneStore。
- **#1830（制图产品）**：复用 commitUserScene/mergeSceneRotary/scene-mutation 管线与 SceneController 骨架，仅加本地表象层；不改 StoryMap 播放器。
- **#1771/#1773/#1829**：只读消费（source schema/量测契约/user-wins 仲裁），零行为改动。
- **#1835（OPEN，A 方向）**：代码面零接触；唯一文件交集 = `tests/quality/snapshots/openapi.json`（双方各自再生成快照；后合并方机械重跑 `API_SNAPSHOT_UPDATE=1 pytest tests/quality/test_api_compatibility.py` 即可）。
- 已知 issues（#1797/#1805/#1809–#1811/#1821/#1824/#1826/#1827）属他人 backlog，未触碰。

## 真实架构/数据契约（additive，INTERFACE_CONTRACTS.md 全量）
- **纯 3D ACK（三处补全）**：前端指纹门第三级回退 = 执行信封 `mapspec_fingerprint`（仅信封 revision == 会话游标 revision 时启用；HUD 行/游标优先级与语义不变）；后端 envelope 增 `scene_work[]`（≤32，进 work 指纹）；观察 DTO 增 `scene3d_assets`（≤16 封闭词表 8 码归一）并落账。**不建第二 ACK**：scene 证据 = envelope scene_work + observation scene3d_assets + 既有 sources[] 对账。
- **glTF 严格语义**：byteStride 交错按步进正确读取（不再静默错读也不一刀切拒绝）；sparse accessor 安全子集（indices/values 词表+越界+预算前置）；normalized 全词表（5120/5121/5122/5123，负满幅 clamp）；indices/sparse bufferView 必须紧密打包（spec 语义）；POSITION 整型/未归一化整型属性 typed 拒绝；3D Tiles refine 非法值拒绝+父子继承。
- **累计预算**：manager 级五维账本（geometry/texture/triangles/CPU decoded/materials），超限先跨资产逐出不可见 tile（LRU），仍超 → 单资产 typed `degraded budget_exceeded` / tile 短退避延后（可见内容保护）；全局 in-flight 下载信号量；abort/unmount/dispose/setStyle 全路径归还（含 buildModel 抛错窗口）。
- **生命周期**：status 词表 +`cancelled`（在途打断）+`context_lost`；canvas 级 lost/restored 监听（仅 3D 资产在场时接管 —— 2D 会话零行为差）；lost → 停调度/abort/释放 GPU 侧/资产标 stale；restored → 有限重建（幂等差分重挂）。
- **泄漏修复**：unmountAsset 检测 layer 已被 setStyle 清扫时显式 `releaseLayerOwnership`（group 摘除 + refCount 归还）。

## 设计取舍（DECISIONS.md D1–D8）
交错/sparse 采取**正确读取**而非拒绝（规范内、常见导出形态、成本 O(1)/元素）；预算账本放 manager（单 map 域，比较视图不互相挤占）；per-asset 显隐/透明度/光照/视角书签为**本地表象层**（user-wins、不进 spec、UI 披露；spec 可见性优先收窄）；context-lost 分层（scene3d 自愈 vs MapLibre 死亡走既有模态）；Draco/KTX2/implicit tiling 维持 typed 拒绝（许可/兼容/安全验证窗口不足）。

## 变化清单（46 文件，+3087/−131；原子 commits 4d6d569..199485e）
前端：scene3d/{gltf-parser,resource-budget(新),asset-manager,custom-layer,status,index}.ts + tiles3d/tileset.ts + fixtures + 6 个测试文件；render-work.ts/contract-manifest.ts/corpus.golden.test.ts；use-cartographic-observation.ts(+test)；scene-controller.tsx(+test)/map-panel.tsx；messages/{zh-CN,en-US}/map.json；j9 journey。
后端：render_work_envelope.py/chat_schema.py/chat.py；tests/unit/gis_harness/test_scene3d_observation_evidence.py(新)；render_work_corpus/{generate.py,golden,manifest}；OpenAPI 快照。

## 测试与跑分（EVIDENCE.md/PERF.md 全量）
- **j9 真浏览器（Chromium+SwiftShader）通过**：纯 3D observation POST 断言（信封指纹回退 + scene3d_assets mounted + apply_ack work echo）+ 真渲染 + 拾取 IR；单跑 3/3 过。
- vitest 全量 **4659 passed + 1 expected-fail**（唯一红 = `catalogue-contract`，**origin/master 干净检出同红**：#1829 后端 4 条 3D 命令未注册前端目录，与本分支零交集）；tsc 双配置 0 错；eslint 0 警告。
- pytest cartography **2912 passed**（唯一红 = `mutation_parity[set_scene_invalid_mode]`，同样 master 干净检出同红）；定向新增 8+12 全绿；ruff 通过。
- j1–j8 全量离线重跑 11 passed/7 skipped（并行负载两例抖动均单跑复验通过）。
- 性能（3 轮）：交错/sparse 解析 2–5ms/1024tri（与紧密同数量级）；预算门 0.02–0.23ms；调度 5461 节点 0.04–0.06ms/pass；**bundle 实测 5.89→5.91MB（+20KB，75 文件，three 主 chunk 671KB 不变，零新依赖）**。

## 诚实局限（RISKS.md）
无 GPU 真机（全部 WebGL 结论限 SwiftShader 软渲染，not_evaluated）；真实 context lost 无法在软渲染触发（管理器状态机由单测覆盖）；terrain 深度遮挡仍为近似（#1834 边界未变）；本地表象层不持久（UI 披露）；evictForBudget 有 200ms 节流窗口内的瞬态逐出可能（自愈）；OpenAPI 快照吸收 master 历史欠账（storymap 1.1/set_scene 等，逐项列于 R6）。

## 回滚
单分支 revert 即可（无 DB migration；生成物随 git revert 一并回退：corpus/OpenAPI 快照在提交内）。行为回滚点：指纹回退仅在 HUD/游标无指纹时介入（revert 后回到既有 2D/混合行为）；预算门超限时降级不装（不会让既有场景更早失败 —— 默认限额高于单 parse 上限）。

## 安全/许可/准确性
无新 URL 面/依赖/API key；DTO validator 封闭词表+尺寸界（非法→证据缺席不 422）；scene_work 只含 id/布尔/revision（零载荷）；垂直语义仅披露不做精度承诺；synthetic fixture 全程可追溯。

## 独立评审结论
R1（未参与实现的 subagent，全 diff + MapLibre 6.10.0/three 0.186.1 内核级对抗验证）：**0 P0 / 0 P1 / 3 P2 / 7 P3** → P2×3 全修 + P3×5 修/披露（commit 199485ebe）→ R2 复核见 INDEPENDENT_REVIEW.md。
