# ADR-0104: Professional Cartography Workbench V4（统一工作台投影与模式化工作区）

状态：已实施（Goal C / Workbench V4）
日期：2026-09-08
审计输入：Phase 0 只读审计 ×8（UI/图层/组件/制图/图表/导出/a11y/设计系统；结论已并入本文与 docs/cartography/workbench-v4.md，工作产物不入库）。
关联：ADR-0088（组件库/MapSpec 唯一 desired 状态）、ADR-0090（工作区交互）、ADR-0091（选择谓词/brush）、ADR-0101（制图模板库 V3）、ADR-0103（设计系统 V4）

## 背景

Workbench V3 的 UI 状态散布在多个自由度上：面板可见性布尔群、图层行的 UI 局部态（拖拽/滑杆草稿）、静态三槽语义分组、无模式概念的工作区。Phase 0 审计（`.agent-work/workbench-v4/01..08`）确认：

- 图层呈现状态同时存在 9 处（HUD store、committed MapSpec、pending、pendingRemoved、appliedSpec、live style、两个证据 stash、UI 局部态），一致性靠 CAS 串行链/pending 覆盖/对象身份门/观测-修复四套机制缝合；
- Explore/Analyze/Compose 无模式地基；agent 无 shell/UI 命令域（catalogue 全是地图语义命令）；
- 前端组件 patch 合约是后端真子集；table_panel 缺默认锚槽行导致组件静默隐藏；
- 图表选择联动声明超前于实现（门禁拦掉 4 种已接线的 kind）；`chart_highlight` 发布的 layer_id 是组件 id 而非绑定图层，联动断链；
- live/export chrome 词表存在反向 parity 缺口（披露族/table_panel 导出画、live 不挂）。

## 决策

### D1 — WorkbenchState = 单 store 内的 UI projection slice（不是第二真相）

新增 `workbenchSlice` 并入 `useHudStore`（`frontend/lib/store/slices/workbenchSlice.ts`）：mode、每模式 tab 记忆、图层多选、用户分组树、锁定、隔离、产物选择、对比状态。边界契约：

- **绝不承载地图语义真相**。权威仍是 MapSpec / backend contract（session-cursor 镜像 + user-mutation CAS 串行链）。分组/选择/锁定/隔离是会话级 UI projection，不持久化、不进 MapSpec、不进 LLM context；
- 分组树是 `Layer.group` 语义组之上的用户组织结构；z-order 唯一真相仍是 store 数组序（= committed spec 序），分组只切分视图不改顺序；
- 会话切换与 dock 同语义清空（`resetLayerGroups`）。

### D2 — Layer Workspace 命令层单一收口

用户批量/复合操作集中在 `lib/layers/layer-ops.ts`（isolate、批量显隐/不透明度、复制/粘贴样式、单层重试），全部复用既有 user-mutation 通道——**不建第二写路径**。锁定（lock）是用户意图护栏：UI 禁操作、批量与隔离跳过、turn-focus park/finalize 豁免（与 `_userPinned` 同族）。

### D3 — 模式只改面板组合

Explore/Analyze/Compose 是封闭词表（`MODE_TABS`），切换只改变 rail 可见 tab 集合与每模式的 active tab 记忆；**不复制、不快照、不回滚地图状态**。Agent 经 `set_mode` shell 命令（MapAction 队列同域）切换：可观测、可回执、幂等；agent 切换置 `modeOrigin='agent'`，rail 显示一键返回（用户上下文不丢）。

### D4 — 合约补齐（additive）

- `ComponentPatch` 增加 `style/options/position`（后端 `patch_component` 本就有的字段族）；
- 观测输出增加 `presentation_converged`（不含鉴权条款的收敛判定）：用户 presentation 编辑清空 generation 认证后，attested 恒假但 runtime 可能完全收敛——状态词表改读 presentation_converged，假「待同步」消除；修复域仍以 `style_converged` 为准；
- `updateLayer(..., { source: 'server' })`：服务端回灌保留认证标签（回声 ≠ 本地编辑）。

### D5 — 联动与降级的诚实性

- 图表类别选择门禁与后端 `selectionLinkage` 词表对齐（donut/grouped/stacked/histogram/rose 接线；line/area/scatter 数值轴诚实不联）；`chart_highlight` 发布绑定数据图层（`options.layerId`）而非组件 id；
- linked extent（`options.extentLinked` 显式 opt-in）：viewport→chart 是只读投影，chart 点击发布 selection 而 selection 不驱动 viewport——**结构上无环**；
- 导出侧 `ExportChromeModel.degradations[]`（封闭 code 词表）：chart/table 面板加载失败从静默缺席改为显式诊断并汇入导出后系统消息；`VISUAL_TYPES ⊆ CHROME_RENDERABLE_TYPES` 包含测试锁反向 parity。

### D6 — typed 样式意图

`lib/styles/style-intent.ts` 封闭词表（set_color/set_palette/lighten/darken/set_opacity/set_stroke_width/thinner/thicker/set_classification/set_point_size）：相对意图按当前样式求值（HSL 亮度/比例宽度），逐字段钳制，非法意图如实失败。Agent 走 `apply_style_intent` 命令；落点与手动样式面板同一条 patch_layer_style 通道。**Agent 不写任意 CSS/MapLibre paint JSON。**

> Review R1（MAJOR-4）接线状态：`set_mode` / `apply_style_intent` 的**前端合约已落地**（词表校验、队列回执、终端状态上报），但后端 tool/prompt 尚未发射这两条命令（catalogue-contract 只锁后端→前端方向）。后端发射接线在后续 wave 落地；在此之前它们是可达性受限的前端就绪合约，非「已生效」的 agent 能力。

## 备选与取舍

- **独立 workbench store（redux 式）**：拒绝——制造第二真相与第二订阅面；slice 并入既有 store 保持单一订阅域。
- **图层状态全面重建（LayerWorkspaceStore 替换 layersSlice + session-cursor intent 状态机合一）**：审计 02 建议的终态，但涉及 CAS/pending/superseded 三套在途机制的迁移，回归风险与本波不匹配；本波落地其先决条件（source 标记、presentation_converged、B1 修剪窗修复、golden-model parity 测试），内核收口留作后续。
- **dnd-kit 重写图层拖拽**：拒绝——HTML5 drag + Alt+方向键已有键盘等价物，重写无行为收益。
- **截图级 visual diff 进入 vitest**：环境不稳定（需常驻服务+GPU）；采用确定性布局几何语料（Wave 12）+ 既有 Playwright 基线通道分工。

## 后果

- 正向：面板组合可模式化扩展；批量/锁定/隔离有单一语义收口；agent 获得确定性 shell 与样式意图合约；假状态徽章与反向 parity 类缺陷有测试防线（B1/B2、词表包含、语料）。
- 代价：`HudState` 继承面扩大（workbench 字段进 store 快照调试视图）；分组不跨会话持久（有意——stale id 垃圾比重新分组更贵）。
- 已知限制：图层面板 `syncSpecLayersToStore` 的 intent 状态机合一未做（B7 turn-focus 收起不落盘的受控分叉仍在）；小 múltiple/cartogram 保持 planned（诚实性）。

## 验收

- `frontend/lib/mapspec/workspace-parity.test.ts`：随机命令序列下三不变式（可见性 parity / 删除压制 / 收敛到服务端文档 + 序一致）；
- `frontend/test/visual/workbench-layout-corpus.test.tsx`：布局几何语料；
- `frontend/lib/map-kit/export-chrome.parity.test.ts`：词表包含 + 降级诊断；
- 全量 vitest（2530+）、`tsc --noEmit`、`eslint --max-warnings 0`（含 jsx-a11y）。
