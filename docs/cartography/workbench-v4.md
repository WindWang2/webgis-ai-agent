# Professional Workbench V4（Goal C）

> 实现记录；架构决策见 `docs/adr/0104-professional-cartography-workbench-v4.md`。Phase 0 审计（8 份）为分支工作产物，不入库；结论已并入本文。

## 子系统地图

| 子系统 | 入口 | 测试 |
|---|---|---|
| 统一工作台投影（模式/多选/分组/锁定/隔离/对比） | `frontend/lib/store/slices/workbenchSlice.ts` | `workbenchSlice.test.ts` |
| 图层工作台 UI（分组树/批量/搜索/复制样式/重试） | `frontend/components/sidebar/layers-tab.tsx` | `layers-tab.workspace.test.tsx` / `layers-tab.compare.test.tsx` |
| 投影层（纯函数） | `frontend/lib/layers/workspace-projection.ts` | `workspace-projection.test.ts` |
| 图层命令层（isolate/批量/样式粘贴/重试） | `frontend/lib/layers/layer-ops.ts` | `layer-ops.test.ts` |
| Golden-model parity（随机命令序列不变式） | `frontend/lib/mapspec/workspace-parity.test.ts` | — |
| 模式化 shell + agent `set_mode` | `frontend/components/layout/nav-rail.tsx` / `frontend/lib/map-commands/workbenchCommands.ts` | `nav-rail.test.tsx` / `workbenchCommands.test.ts` |
| 组件交互 V4（snap/置顶/键盘缩放折叠隐藏） | `frontend/components/map/map-components/floating-chrome.tsx` | `floating-chrome.wave4.test.tsx` |
| 图表双向联动（门禁对齐/chart_highlight 修复/视野联动） | `frontend/components/chat/chart-core.tsx` / `frontend/components/map/map-components/chart-panel.tsx` / `frontend/lib/map-commands/chartCommands.ts` | `chart-linkage.wave5.test.tsx` |
| 对比工作台（swipe + V5 真双面板，ADR-0105） | `frontend/components/map/comparison/` | `comparison-sync.test.ts` / `comparison-view.test.tsx` |
| typed 样式意图 | `frontend/lib/styles/style-intent.ts` / `frontend/lib/map-commands/styleCommands.ts` | `style-intent.test.ts` |
| 导出 parity + 显式降级 | `frontend/lib/map-kit/export-chrome.ts` / `exporter.ts` | `export-chrome.parity.test.ts` |
| 布局几何语料 / 500 层压力 | `frontend/test/visual/workbench-layout-corpus.test.tsx` / `frontend/test/workbench-stress-500.test.tsx` | — |

## 关键契约（速查）

1. **地图语义真相唯一**：MapSpec（session-cursor 镜像）+ user-mutation CAS 串行链。workbenchSlice 只存 UI projection；分组/选择/锁定/隔离不持久化、不进 MapSpec。
2. **z-order 唯一真相**：HUD store 数组序（= committed spec 序）。分组树只切分视图。
3. **锁定护栏**：UI 禁操作 + layer-ops 批量/隔离跳过 + turn-focus park/finalize 豁免。
4. **服务端回声不去失鉴权**：`updateLayer(..., { source: 'server' })`；状态派生读 `presentation_converged`（不含鉴权条款），假「待同步」防线见 `render-evidence.attestation.test.ts`。
5. **联动无环**：viewport→chart 只读投影（opt-in `extentLinked`）；chart→selection→map 过滤；selection 不驱动 viewport。
6. **样式意图封闭词表**：agent/用户共用；落点 patch_layer_style 通道；agent 不写任意 paint JSON。
7. **导出诚实降级**：`ExportChromeModel.degradations[]` 汇入导出后系统消息；live ⊇ export 词表由包含测试锁定。

## 已知限制

- 图层 intent 状态机与 session-cursor 的手工合并（审计 02 B7/B9）未合一：turn-focus 的轮次收起是受控分叉（pending 压制、不落盘）。
- side-by-side 在 V4 诚实下线（「主图不动」约束下双半屏永不共地理）。
  **V5（ADR-0105）已恢复为真双面板**：side-by-side 激活时主图画布收缩为
  左半幅，副图占右半幅、相机同步 —— 双窗格为真实半宽视口；副视图 parity
  （is3D/terrain、图例过滤、选择过滤、副图族图例）已实现。大规模内联
  GeoJSON 的视口确定性网格抽稀见 W7（预算 5000）。
- 分组树不跨会话持久（避免 stale layer id 垃圾）。
- 小倍数（small_multiple）/cartogram 图保持 planned：单画布 MapSpec 与 Gastner-Newman 算法成本，见 `docs/cartography/map-model-catalog.md` 的诚实披露。
