# feat(g25): 多画幅真实交互运行时与联动浏览

## 功能增量（对 A01–A28 的实现映射详见 ACCEPTANCE.md）

在 G15 frame_product.v1 数据面之上交付**交互运行时**——三幅地图成为能分别
交互的产品：

- **P0 运行时契约**：`frame-runtime.ts` 只读投影 + 封闭状态机
  （unmounted/loading/live/snapshot/stale/error，snapshot 永不宣称 live、
  旧 revision 不得 verified）；快照指纹（session+frame+spec/data
  revision+viewport）；确定性后继；extent/投影守卫。
  `where-ast.ts` = 后端 feature_pages 求值器的 JS 同源移植；
  `frame-effective-spec.ts` = resolver.py 的前端等价（layerOverrides +
  where/time_window 过滤 + 共享分级**同值同色不重算** + 契约时区时间界
  归一 normalizeBoundaryValue）。
- **P1 有界视窗调度**：FrameProductGrid 单 live 槽位状态机（占用者 =
  活动画幅或快照预热目标，切换前确定性捕获）；FrameViewport
  （react-map-gl + 独立 MapSpecRuntime）；`mapInstanceRegistry` 活实例
  预算仲裁（默认 2 计主图，对比副图占用如实披露）；`FrameSnapshotStore`
  LRU（12 项 + 32MiB 双上限，指纹失效）。
- **P2/P5 编辑接线**：激活流（optimistic + `set_frame_product` CAS 持久
  → 刷新/面板同一服务端真相）；`frame-edit-scope` 「仅本幅」路由
  （set_layout frames 整表，其余帧逐字携带）；键盘 `[` `]` 切换 +
  Escape 关闭；scope 徽标命名（仅本幅/同组/整个产品）。
- **P3 同步组真实传播**：`frame-camera-hub` 把 G15 的 `resolveGroupSync`
  接到真实相机事件——三层防回声（容差收敛/applying 代际门/entry
  generation），主图↔活动画幅双向联动，pan/zoom 独立可控；未挂载帧记录
  期望相机并失效快照（不强行创建 context）；锁定 extent 拒绝写入。
- **P4 资源与失效**：卸载捕获（idle 后 live canvas dataUrl，失败如实
  null）；context loss 代际重建 + 观察重绑；会话切换全失效；连续 30 次
  挂卸无注册残留；resize 只影响受影响瓦片尺寸面。
- **P6 逐画幅真实观察**：前端逐帧观察生产者（`window.__g25FrameObservation`
  只读挂点 + OBERVATION_MODES 对齐词表）；观察 POST `frames` 增维
  （DTO 封闭词表归一，浏览器绝不冒充 export_verified）；后端
  `frame_product_status` 接入存储观察（服务端指纹门盖章 revision，
  stale → stale_observation，frameA 完成不补 frameB）。
- **P7 真实浏览器验收**：J18 双档旅程（registry 登记，#2084 规则）。
  mock 档 = Chromium+WebGL 全交互（真画布计数≤2/CAS 激活/键盘/Escape）；
  real 档 = 真后端服务端闭环（runner-local 真实 mutation 管线种子 →
  观察指纹门 POST → status 逐帧终验回读）。

## 与在途 PR 的关系

- #2090（G28）先注册了 `j17` 旅程编号 → 本方向旅程改号 **j18**
  （遵 #2084 唯一 ID 规则）；#2090 只消费既有 frame_evidence_rows，
  与本分支 frame_products/ 后端改动无文件冲突。
- #2087（G27）/#2088（G24）：map-panel.tsx / mapspec-runtime 加性冲突，
  合并时语义解（均为小接入补丁）。

## 排除项（明确不做）

- 不改 frame schema / shared_expression 计算 / PublicationIR 版面（G15/G26）。
- 不做全局完成判定（G28）——证据行经稳定字段与 goal_satisfaction 既有消费面相连。
- 不升级 Pi vendor（G22）；本分支在主线 pin dd30101 构建验证。
- 不修 BASE 既有失败（label-authoring tsc / import-workbench CJK /
  comparison-view clip-path / poi-info-panel——两侧同现，登记于 TEST_EVIDENCE）。

## 证据（详见 TEST_EVIDENCE.md）

- 前端 44 用例（8 文件）全绿：契约/纯函数/组件集成/生命周期。
- 后端 6 用例：DTO 归一 + status 接线（live/snapshot/not_observed 独立、
  stale revision 不 verified）。
- J18 mock：真实浏览器 3 轮稳定通过（6.5–13.6s）。
- J18 real：真实服务端闭环 8.2s 通过（真登录/UI 会话/真实 mutation
  管线/指纹门/终验回读）。
- 全量前端回归：5225 passed / 4 failed（全部为 BASE 既有，stash 两侧复现）。

## 性能

- 活地图预算默认 2（主图+活动画幅）；预热串行（并发 1）。
- 快照 LRU：12 项 / 32MiB（按 dataUrl 长度计）；切换捕获上限 2.5s。
- 30 次挂卸注册计数归零；J18 全程页面 canvas ≤2 实测。

## 兼容 / 回退

- 无产品会话：网格完全不渲染（仅 map-panel +2 行接入补丁）；根地图行为不变。
- camera 永不进 schema（客户端 sessionStorage 草稿，显式披露）。
- 观察帧字段全部 optional（旧客户端缺席语义）；后端 status 观察读取
  失败按缺席降级。
- schema 无变化、无迁移。

## 外部依赖 / 未测项

- J18 real 浏览器内网格交互受两个平台缺口约束（如实披露）：spec 仅经
  live-turn SSE 到达浏览器（无 turn-less 恢复通道）；LLM stub ×
  Pi-only 多轮调用 seam 失配（G12 修复面）。mock 档以同构 SSE 夹具在
  真实浏览器覆盖该交互面。
- A15（真实浏览器全幅 resize 拖拽回归）与 A23（Pi 子进程×frame 更新×
  浏览器三方联跑）为 △：机制+单测在，联跑面待平台缺口闭合。
- 已知非致命噪声：react-map-gl × maplibre 6.10 帧视窗挂载时 1 条库内
  console error（地图渲染/断言/计数正常；升级归属 G22）。
