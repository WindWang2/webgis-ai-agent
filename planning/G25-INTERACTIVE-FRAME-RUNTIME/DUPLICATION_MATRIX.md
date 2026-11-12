# G25 DUPLICATION_MATRIX — 能力 × 现状 × 增量

BASE `b8f2a6ecc`。原则：已合入能力复用不重建；下表「本任务增量」列外的均为明确不做。

| 能力 | 现状证据 | owner/PR/branch | 本任务剩余增量 | 共享接口 | 明确不做 |
|---|---|---|---|---|---|
| frame_product.v1 schema/投影/CAS | `types.generated.ts:526`、`frame-product.ts`（15 用例） | G15 #2076 已合入 | 无（只读消费） | `projectFrameProduct`/`commitFrameProductPatch` | 不改 schema、不加 camera 字段 |
| 相机同步纯函数 | `frame-sync.ts`（resolveGroupSync/syncGroupMembers）+ `comparison-sync.ts` | G15/Wave8 已合入 | 接线到真实相机事件（P3），不改纯函数 | `resolveGroupSync`/`readCamera`/`applyCameraPatch` | 不重写 resolveSyncPair、不改 ComparisonView |
| 第二地图挂载范本 | `comparison-view.tsx`（react-map-gl+MapSpecRuntime+防回声+清理） | Wave8/W6 已合入 | 泛化为 N 画幅网格（P1），不动对比视图 | `MapSpecRuntime.reconcileAsync`、`injectResolvedRefSources` | 不复制 ComparisonView 代码，不建第二套 map runtime |
| maplibre 实例注册/计数 | **不存在**（全仓 grep 无） | — | 新建 `frame-instance-registry.ts`（P1，A09） | 注册表单例 API | 不重构 map-panel 主图生命周期 |
| 快照/抓帧 | `frame-composer.ts`（导出时刻单图逐帧抓 canvas + waitForIdle） | W9/ADR-0118 | 新建 LRU 快照 store（P1，卸载捕获+顺序预热） | waitForIdle 模式借鉴 | 不复用导出管线、不改 frame-composer |
| 激活门控先例 | `temporal-map/live-frame.ts`（seq 令牌 + markLiveApplied） | GOAL03 M4 已合入 | 新建画幅激活流（P2，seq+applied 同构） | 模式复用非代码复用 | 不动 temporal 数据面 |
| 帧有效文档解析（where/time_window/layerOverrides/shared_expression） | 后端 `resolver.py` 纯函数完整 | G15 WP04 已合入 | **前端 JS 等价求值器**（where AST 词表同源移植，P0） | 与 `evaluate_where` 同语义（含 _num_eq/字符串窗口/大小写规则） | 不新建第二份后端解析、不动 feature_pages.py |
| 逐画幅验证 | `verification.py`（observed 契约就绪，OBSERVATION_MODES 四级） | G15 WP11 已合入 | 生产者 + DTO frames 字段 + status 接线（P6） | `verify_frame_product(document, observed)` | 不改终验判定语义（G28） |
| 观察上报通道 | `POST cartographic-observation`（fingerprint 门 + latest-wins） | P9 已合入 | `frames` 增维可选字段（加性，同 scene3d_assets 先例） | `CartographicRuntimeObservationRequest` 加字段 | 不新建 endpoint/store |
| 面板编辑 | `frame-product-panel.tsx`（激活/改名/组/图例/排序/隐藏/解绑） | G15 WP07 已合入 | 激活态一致性、键盘、scope 命名（P5 小补丁） | `commitFrameProductPatch` | 不重做面板 |
| 面板 e2e | `j16-frame-product.journey.ts`（mock-only，零地图断言） | G15 + #2084 已合入 | 新 J18 交互旅程（不占 j15/j16） | registry.ts 登记 | 不改 j16 |
| Pi 工具 | `frame_product_tools.py` 5 工具 | G15 WP09 已合入 | 必要时只读 runtime 状态查询（P5，加性） | ToolRegistry | 不让后端以 visible 为写授权 |
| PublicationIR 版面/栅格出版 | publication_export 走 resolver | G15/G26 边界 | 无 | — | 不做混合栅格出版（G26） |
| 多尺度/拾取/数据交付 | G27 #2087 / G24 #2088 / G18 #2080 在途 | 开放 PR | 仅登记依赖（map-panel/tools/__init__ 加性冲突） | — | 不并入他人 PR 内容 |

## 历史安排核实（不重立）

- G09 批量分析：worktree `webgis-wt-g09-*` 在途，不碰。
- G21 OD flow：worktree `webgis-wt-g21-*` 在途，不碰。
- 模板/算法/recipe 大批次：`planning/G04-*` 等已有，不新立项。
- 快照时点开放 PR #2084/#2085/#2086 已全部合入 BASE，无需组合验证 worktree。
