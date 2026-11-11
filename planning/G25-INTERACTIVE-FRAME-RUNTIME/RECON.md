# G25 RECON — 多画幅真实交互运行时与联动浏览

调查时间：2026-11-12（UTC）。调查者：主 Agent（自读）+ 只读架构调查员（subagent）。
BASE_SHA：`b8f2a6ecc777d0ae633202034b609a48a8d140d9`（origin/master，fetch 于 2026-11-12）。
快照声称的 `8a9a79f76` 已过期；其列为「开放」的 #2084/#2085/#2086 均已合入 BASE。

## 0. 仓库与并行纪律核实

- 仓库 = WindWang2/webgis-ai-agent（remote origin 核实）。
- 开放 PR：#2087（G27 多尺度表达）、#2088（G24 MVT 权威拾取）。两者均未注册新 journey（J18 空闲）；均改 `app/tools/__init__.py`（加性，合并可解）；#2088 改 `frontend/components/map/map-panel.tsx`（本方向在 map-panel 只做最小 hook 挂载，降低冲突面）。
- 本机 worktree：g21/g09/g22/g23/g24/g27/g3dv2 等在途，无 g25 冲突路径。
- 宿主级重任务锁：`/home/kevin/projects/webgis/.heavy-task.lock`（flock；已核实其他方向在用）。
- 硬件：16 核 / 62GiB RAM（可用 ~36GiB）。编译预算：默认 -j2，锁内重任务串行。

## 1. 前端基线（自读核实）

### frame-sync.ts（G15 纯函数，P3 的地基）
- `resolveGroupSync(sourceId, sourceCamera, targets, syncPan, syncZoom)` → 同组除源外未收敛成员的 `CameraPatch[]`；`null` patch = 已收敛（构造性断环）。基于 comparison-sync 的 `resolveSyncPair`（EPSILON=1e-7 容差）。
- `syncGroupMembers(frames)` → group→frameId[]。
- **未接线**：无任何运行时调用方（`git grep resolveGroupSync` 仅定义与测试）。

### comparison-sync.ts / comparison-view.tsx（第二地图挂载范本）
- `CameraSnapshot{center,zoom,bearing,pitch}`、`readCamera(map)`、`applyCameraPatch(map,patch)`（jumpTo）、`resolveSyncPair`（容差收敛→null）。
- ComparisonView：react-map-gl `<Map>` 第二实例 + `MapSpecRuntime.reconcileAsync(filteredSpec)`；`isSyncingRef` 写相机期间吞事件（防回声）；主图 move→副图走 `primary.on('move')` 实例事件，副图→主图走 onMove viewState；`onStyleRecovery` 处理 setStyle 后重挂；Escape 经 `registerEscapeHandler`；导出 registry（comparison-export-registry）持有 canvas 访问器闭包不持 Map 实例。
- 数据共享：`injectResolvedRefSources` 全局 refId LRU 缓存去重，双图共享同一 GeoJSON 对象。

### frame-product.ts（G15 投影 + CAS 写入）
- `projectFrameProduct(spec)` → `FrameProductView{bound,enabled,productKind,gridTitle,columns,activeFrame,slots[],sharedExpression,disclosures}`；slot 含 `raw`（patch 整表基底）、title/enabled/legend/syncGroup/active/binding{ref,layerId,field,contentRevision,hasWhere,hasTimeWindow}。
- `commitFrameProductPatch(patch)`：POST mapspec/mutations intent=set_frame_product + expected_revision CAS；409 superseded 静默回灌。
- `subscribeFrameProduct`/`getFrameProductSnapshot`（面板用 store 订阅）。

### frame-product-panel.tsx（G15 管理入口）
- 挂 `map-studio-tab.tsx:425`（Studio「画幅」子页签 `#map-studio-tab-frames`）。已有：激活画幅（Target 图标→active_frame patch）、改标题（blur→frames 整表 patch）、同步组 A/B/无、图例策略、排序、隐藏/恢复、解绑。
- **无**：与地图上激活态的一致性标记（面板内 active 徽标 = 产品 active_frame，但地图侧无对应渲染）、键盘切换画幅、scope 命名（仅本幅/同组/整个产品）。

### map-panel.tsx（主图，2025 行）
- `<Map>` 主实例 + 兄弟覆盖层模式：ComparisonView/TemporalTimeController/LegendStack/MapDecorations/MapSpecChrome 均为兄弟节点。
- `comparisonSideBySide` 时主图容器收窄半幅 —— 已有「主图让位」先例。
- 本方向接入点：**单个兄弟节点挂载**（与 ComparisonView 同款），map-panel 仅 +import/+JSX（2 行级）。

### schema（types.generated.ts，frame_product.v1）
- `FrameProductConfig{enabled,product_kind,grid{columns,title},frames[],shared_expression?,active_frame?,quality?,disclosures?}`；`ProductFrameSlot{frame_id,title?,legend?,sync_group?,data?}`；`FrameDataBinding{ref,layer_id,content_revision?,field?,where?,time_window?}`。
- **无 camera 字段** —— camera 是运行时态，客户端本地（session 级）持久化，不扩 schema（schema 所有权在 G15/frame schema 边界外）。

### e2e registry（#2084 规则）
- `frontend/e2e/helpers/registry.ts` JOURNEYS 数组：j1–j16 已占用。新增 = 新文件 `journeys/jN-*.journey.ts` + registry 登记 {id,file,title,smoke,modes,owner}。G25 使用 **j18**（已核实两开放 PR 均未占用）。
- playwright config：`E2E_MODE=mock|real`，mock 走 page.route fixtures，real 需 E2E_API_URL(:8001) 真后端；`testMatch **/*.journey.ts`；retries=0。
- J16（j16-frame-product.journey.ts，mock-only）：三画幅恢复 + 改名 CAS + 激活 —— **面板旅程，无地图渲染、无交互视窗**。G25 的 J18 必须是真地图交互。

## 2. 后端基线（调查员补充后完善）

- `app/services/gis_harness/frame_products/`：resolver.py（数据绑定→frame 数据视图）、shared_expression.py（共享分级）、service.py、verification.py（逐画幅验证）。
- `app/tools/frame_product_tools.py`：Pi 工具（set_frame_product 等）。
- 待调查员报告：verification 输出结构、观察生产者对接点、route 前缀。

## 3. G15 自述残差（#2076 披露）

- 相机同步组 = 契约 + 纯函数；**多画幅交互视窗、快照LRU、按需激活、主图与激活画幅联动未接线**。
- G15 TEST_MATRIX/REVIEW 中的验收边界详见 DUPLICATION_MATRIX.md。

## 4. 真实残差清单（P0–P7 → 现有代码缺口）

| 工作包 | 现状 | 缺口（本方向交付） |
|---|---|---|
| P0 契约/状态 | FrameProductView 投影存在（数据面） | 运行时投影：mount 状态机、desired/resolved revision、camera 来源、snapshot fingerprint、generation —— 全缺 |
| P1 视窗调度 | ComparisonView 双图范本；无 frame 视窗 | FrameViewport 组件、活实例上限仲裁器（默认 2 计主图）、快照 LRU store、按需激活、快照预热序列 |
| P2 主图↔活动画幅 | 无 | active frame 激活→HUD 属性/图层操作 scope 路由、camera 草稿持久化、失效后继选择 |
| P3 同步组传播 | resolveGroupSync 纯函数未接线 | 相机事件 hub（source/sequence/generation 防回声）、未挂载帧期望相机记录、投影/锁定 extent 拒绝降级 |
| P4 资源/失效 | ComparisonView 自清理范本 | 帧级清理清单、context loss 代际重挂+观察重绑、resize 只更新受影响帧、快照 stale 传播 |
| P5 编辑/Agent 定位 | 面板编辑全有 | 面板↔地图激活一致、键盘切换、scope 明示（仅本幅/同组/整个产品）、stable frame_id 定位核实 |
| P6 逐画幅观察 | verification.py 存在（服务端） | 前端逐帧观察生产者（加载/source error/范围/camera/snapshot-live 级别）+ 接入现有验证链 |
| P7 真浏览器验收 | J16 面板 mock 旅程 | J18 真实多画幅交互旅程（mock 档全量 + real 档真后端）+ registry 登记 |

## 5. 已知约束与红线（执行时遵守）

- MapSpec = desired-state 单一真相；不新建第二份图层状态（FrameViewport 的 spec 从 committed MapSpec 派生 + frame slot 过滤，同 ComparisonView 模式）。
- 写路径只走 set_frame_product + CAS（commitFrameProductPatch）；camera 永不写 schema。
- 主图零改动原则继承 ComparisonView 惯例 —— G25 对 map-panel 的修改 = 挂载点 + 主图让位 CSS 钩子（comparison 先例内）。
- 不重做：frame schema、shared classification、后端 PublicationIR 版面（G26）、全局完成判定（G28）。
