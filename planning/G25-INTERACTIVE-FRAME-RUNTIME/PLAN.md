# G25 PLAN — 多画幅真实交互运行时与联动浏览

分支：`zcode/g25-interactive-frame-runtime-20261111T173941`（BASE b8f2a6ecc）。
规划目录：`planning/G25-INTERACTIVE-FRAME-RUNTIME/`。

## 设计总纲

**核心判断**：G15 已交付数据面（schema/投影/CAS/验证），G25 交付**运行面**——把持久 frame 产品变成能分别交互的地图画幅。ComparisonView（双图互同步范本）证明本仓库已有「第二张 maplibre 地图 + MapSpecRuntime reconcile + 相机纯函数同步 + 防回声」的全部构件；G25 把它推广到 N 画幅 + 有界资源 + 快照 + 观察。

**所有权**：`frontend/components/map/frames/`（新目录，组件+测试）、`frontend/lib/cartography/frame-runtime*.ts`（新运行时契约纯函数+store）、`frontend/e2e/journeys/j18-*.journey.ts`（新旅程）。接入点：map-panel 挂载（~2 行）、map-studio-tab 面板增强（小补丁）、registry 登记（1 条目）。后端：只读消费现有 frame_products API；仅在观察对接需要时加只读字段/路由（加性）。

## 分层架构

```
committed MapSpec (frame_product.v1)          ← 单一真相（服务端）
        │ projectFrameProduct (G15, 复用)
        ▼
FrameRuntimeProjection (P0, 新纯函数)          ← 只读运行时投影
  · 每帧: mountState(unmounted/loading/live/snapshot/stale/error)
  · desiredRevision(spec/data/style) · camera(draft/expected/applied)
  · snapshotFingerprint = hash(session,frame,specRev,dataRev,viewport)
        │
        ▼
FrameGridController (P1, 新组件, 网格编排)
  · MapInstanceBudget(默认2, 计主图) — 活实例仲裁
  · 活跃槽位: activeFrameId → FrameViewport(live)
  · 其余槽位: SnapshotTile(快照 or 未观察占位)
  · SnapshotStore(LRU: 项数+字节上限, fingerprint 失效) (P4)
  · 顺序快照预热(并发1, 可被用户激活打断)
        │
        ├── FrameViewport (P1/P3/P4, 新组件)
        │     react-map-gl Map + MapSpecRuntime(复用) + frame spec 派生过滤
        │     相机事件 → FrameCameraHub; context loss → generation 重挂
        │
        ├── FrameCameraHub (P3, 新模块级事件总线)
        │     真实相机事件(source id+seq+generation) → resolveGroupSync(复用)
        │     → 应用补丁(isSyncing 代际吞回声) + 未挂载帧期望相机记录
        │     主图联动: 主图与活动画幅互同步(与 ComparisonView 同门)
        │
        ├── 逐帧观察生产者 (P6, 新)
        │     每帧上报: mountState/版本匹配/source error/范围/camera/级别
        │     → __g25FrameObservation 页面挂点(浏览器可见, 只读)
        │     → 服务端 frame_products/verification 对齐字段
        │
        └── 编辑 scope 路由 (P2/P5)
              active frame 激活 → HUD 属性/图层操作落 frame override;
              操作名明示 仅本幅/同组/整个产品; frame_id 稳定定位
```

## 工作包 → 文件映射

| WP | 交付物 | 测试 |
|---|---|---|
| P0 | `frontend/lib/cartography/frame-runtime.ts`（投影+状态机+fingerprint 纯函数） | `frame-runtime.test.ts` |
| P1 | `frontend/components/map/frames/frame-viewport.tsx`、`frame-grid.tsx`、`frontend/lib/cartography/frame-instance-registry.ts`（预算仲裁）、`frame-snapshot-store.ts`（LRU） | `frame-grid.test.tsx`、`frame-snapshot-store.test.ts`、`frame-instance-registry.test.ts` |
| P2 | `frame-grid.tsx` 激活流 + camera 草稿（sessionStorage, 客户端本地披露）+ 失效后继 `frame-runtime.ts` | `frame-grid.test.tsx`、`frame-runtime.test.ts` |
| P3 | `frontend/components/map/frames/frame-camera-hub.ts`（接线 resolveGroupSync） | `frame-camera-hub.test.ts` |
| P4 | 清理清单入 FrameViewport/registry/snapshot store；context loss 代际；resize 定向 | 各组件测试 + `frame-snapshot-store.test.ts` |
| P5 | map-studio-tab/panel 一致性小补丁 + 键盘切换 + scope 命名 | `frame-grid.test.tsx` + panel 测试补充 |
| P6 | `frontend/lib/cartography/frame-observation.ts` + 挂点 | `frame-observation.test.ts` |
| P7 | `frontend/e2e/journeys/j18-frame-interactive.journey.ts` + registry + real 档 fixture | journey 自身 |

## 关键设计决策（可证伪）

1. **活实例预算默认 2、计主图**：`FrameInstanceRegistry` 模块级登记全部 maplibre 实例（主图/画幅/对比副图），网格开启时活画幅 ≤ budget−1。A09 用它断言。禁止每帧常驻 context。
2. **快照来源 = 卸载时 canvas 捕获**（ deactivate/swap 时 `getCanvas().toDataURL()`）+ 顺序预热（并发 1，共享预算槽位轮转，可打断）。快照永远标注 fingerprint（spec/data revision + viewport size），stale 显式。不在预算外创建 context。
3. **camera 三层**：applied（地图实况）/expected（未挂载帧的同组期望）/draft（用户交互草稿, sessionStorage per session+frame）。激活时优先 draft→expected→主图相机对齐（ComparisonView 先例）。camera 永不进 schema、永不 CAS 写。
4. **防回声三层**：resolveGroupSync 容差收敛（构造性）+ isSyncing 代际标志（事件层）+ hub sequence/generation（陈旧事件丢弃）。A07 断言。
5. **主图联动语义**：活动画幅与主图双向同步（同 ComparisonView pan/zoom 协议）；帧间同步按 sync_group。主图不属于任何 sync_group（它是工作台根视图）——A06「不同组不传播」以帧组为界。
6. **投影不兼容拒绝**：不同 projection（frame spec 的 projection ≠ 主图）→ 明确拒绝挂载并给原因（A08），不复制坐标。锁定 extent（frame data.where 有 bbox 锁定或 spec extent 锁定）→ 相机写入被拒 + 披露。**待实现时按调查员报告的 spec 投影字段核实**。
7. **观察不撒谎**：未挂载帧 = `not_observed`；快照帧 = `snapshot` + fingerprint（非 live）；旧 revision 快照 ≠ verified。frameA 完成不得填 frameB。
8. **J18 双档**：mock 档（page.route fixture 全网格交互断言）+ real 档（真后端 uvicorn :8001 + 真浏览器）。real 档 blocked 时标 blocked（A28）。

## 执行顺序（每轮: 读→失败用例→实现→回归→自审→ledger）

1. **M1（P0+P3 契约层）**：frame-runtime.ts 纯函数（状态机/投影/fingerprint/后继选择）+ camera-hub 防回声核心 —— 全部可确定性单测。
2. **M2（P1 视窗层）**：instance registry + snapshot store + FrameViewport + FrameGrid（jsdom 档 mock maplibre）。
3. **M3（P2+P5 编辑接线）**：激活流、scope 路由、面板一致性、键盘。
4. **M4（P4 资源）**：清理审计、context loss、resize、stale 传播。
5. **M5（P6 观察）**：观察生产者 + 挂点 + 服务端字段对齐。
6. **M6（P7 验收）**：J18 mock + real 旅程、验收矩阵 A01–A28 映射、性能采样。
7. **M7**：独立 review → fix → 复审 → PR。

## 预算与资源纪律

- 编译/测试默认 -j2；`pnpm test`（vitest）限制单文件粒度串行跑；Playwright workers=1（config 已 fullyParallel，运行时用 --workers=1）。
- 重任务（Next build、Playwright real 档、后端全量 pytest）一律 flock `.heavy-task.lock`。
- 后台服务只启动：dev server（本 worktree 端口）、real 档 uvicorn（:8001 独立 DATA_DIR/SQLite）。用完即停。

## 明确不做

- 不重做 frame schema / shared_expression 计算 / PublicationIR 版面 / G26 栅格出版 / G28 完成判定。
- 不升级 Pi vendor（G22 所有权）。
- 不把 camera/可见性写回服务端授权面（客户端声称不是授权）。
- 不接管他人 issues / 在途 PR 的修复。
