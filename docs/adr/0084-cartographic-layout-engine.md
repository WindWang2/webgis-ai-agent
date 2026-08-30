# ADR-0084: Cartographic Layout Engine（确定性组件布局求解器）

- Status: accepted
- Date: 2026-08-30
- Extends: ADR-0081（组件解析共享层）、ADR-0070（组件运行时 v2）、#884 U-2、#1079 顶槽堆叠

## Context

ADR-0081 统一了组件**解析**（resolveMapComponents），但**布局**仍两侧
各一套半成品：

- live：底槽有 U-2 堆叠（helpers.buildBottomSlotIndexes，硬编码）；顶槽
  （#1079 layout-runtime）只认旧 `position` 字段 —— `placement.anchor`
  被忽略（E-6）；
- export：完全没有堆叠 —— scale_bar 与 continuous_colorbar 缺省同锚
  bottom-right、margin 52/56，互相遮挡（E-1）；面板族全部固定 margin 90。

另有两处既知分叉：比例尺算法 fork（live 候选表 vs export nice-number，
E-3）；`collapsed` 只在 floating 分支捕获，锚定面板折叠在导出侧丢失
（E-2）。

## Problem

需要 Component + Layout Constraint + Priority + Collision + Viewport +
Export Canvas 的统一布局层，且：

- live 与 export 必须消费**同一个求解器**（否则再次制造行为分叉）；
- user > agent > auto：自动布局不得覆盖用户显式拖放/折叠；
- 确定性（同输入同输出），无线性规划/约束求解器的复杂度。

## Decision

新增 `frontend/lib/map-components/resolve-layout.ts`：**纯函数布局
求解器**，live（layout-runtime 适配层）与 export（export-chrome 模型
构建）共用。

1. **求解模型**：输入参与者（id/type/解析后 anchor/floating 矩形/
   origin），输出每组件 `(slot, index, slotSize[, fallbackFrom])` +
   碰撞披露列表。
   - anchor 优先级裁决留给解析层（resolveMapComponent.anchor /
     resolvePosition）—— 求解器只消费最终 anchor（单一层做一件事）；
   - 槽内排序 `(priority, 声明序)` 稳定；scale_bar 恒贴边（U-2 约定）；
   - 层距 36px（与 HUD chrome 族约定一致，`DEFAULT_STACK_STEP_PX`）。
2. **user-wins**：floating（user-pinned）组件不参与堆叠也不被挪动；
   floating × floating 碰撞只披露。auto/agent 锚定组件的估算槽区与
   **user** 浮动盒 AABB 相交时**侧让**到确定性 fallback 槽
   （bottom-right→bottom-center 等，单步），`fallbackFrom` 披露。
3. **单一实现**：`layout-runtime.resolveSlotLayout` 变为共享求解器的
   适配层（保留既有 API；SlottedComponent 增 `anchor` 字段 —— E-6 修复：
   live 顶槽现在尊重 placement.anchor）。`COMPONENT_LAYOUT_META` 移居
   求解器模块（layout-runtime re-export，默认槽位表与后端 catalog 的
   defaultPosition 由测试逐项锁定 —— E-9）。
4. **导出堆叠（E-1 修复）**：`buildExportChrome` 对 enabled 锚定组件跑
   同一求解器，模型元素携带 `stackIndex/slotSize` 与生效锚点（侧让后
   槽位）；exporter 按元素 `marginY + stackIndex × 36 × scalePx` 偏移
   —— top/bottom 槽的"远离边"方向由 marginY 语义天然承载。
5. **比例尺单一算法（E-3 修复）**：`scale-math.ts`
   `computeNiceScale(mpp, targetPx)`（nice-number 1/2/5×10^k 就近）+
   `formatScaleLabel`；live（目标 ~100px）与 export（画布宽 12%）共用
   —— 目标像素是参数，算法本体单一。
6. **附属 parity 修复**：
   - E-2：`collapsed` 提升为 `ResolvedMapComponent` 顶层字段
     （mode 无关）—— 锚定面板折叠导出为标题条；
   - E-4：图例族绑定取第一个 **enabled** 成员（disabled legend 不再
     shadow enabled categorical_legend）；族内全 disabled = 用户显式
     关闭 → 无图例且不走 HUD 兜底（user-wins）；
   - E-5：colorbar 退化语义与 live 对齐（无 palette 不绘制、缺范围
     画裸条不带数值标签、单色复制两端、不伪造默认 ramp）；
   - E-10：`fromSpec` 以 enabled 可视组件为准（与 live
     hasSpecChrome 对齐 —— 只有禁用 title 的 spec 走 HUD chrome 栈）。

## Alternatives

- **线性规划/约束求解器**：拒绝 —— 组件数是个位数量级、槽位模型已
  足够表达；LP 引入不确定性调参与非确定性风险，收益为零。
- **CSS/DOM 测量驱动的 live 布局 + 导出后量测**：拒绝 —— 导出是离屏
  canvas，无法读 DOM；两套测量又回到分叉。
- **在 MapSpec 上持久化求解结果**：拒绝 —— 布局是派生投影（输入 =
  组件 + 画布尺寸），持久化即第二真相。

## Trade-offs

- 槽区尺寸是**估算值**（按类型的保守高度表）而非实测 —— 侧让判定可
  能保守（宁可侧让也不遮挡）；live 的 DOM 实际布局仍由 CSS 类承载，
  求解器只决定槽位与层序，不做像素级布局。
- fallback 单步（无递归再判定）—— 极端堆叠下仍可能碰撞，以碰撞披露
  兜底（与 ADR-0081 的 finalizer 披露语义衔接）。

## Compatibility

- layout-runtime API 不变（内部委托求解器）；`resolveSlotLayout` 行为
  差异仅在「显式 anchor」输入（旧调用不传 anchor 则走 position/默认，
  行为不变）。
- ExportChromeModel 字段纯增量（stackIndex/slotSize）。
- 旧 spec（无 placement）：全部走旧 position/默认槽路径。

## Performance

- 求解 O(C²)（C = chrome 组件数，个位数量级）、纯内存；导出侧每画布
  一次。live 侧 buildTopSlotIndexes 每渲染一次，成本与旧实现同阶。

## Failure semantics

- 未知类型 → 未知 priority（50）参与排序，不丢弃；
- 侧让后仍碰撞 → 碰撞披露（不强制挪动 user 元素）；
- 求解器异常无路径（纯函数、无 I/O）。

## Migration

无迁移：默认行为不变；新语义（堆叠/侧让/折叠）由组件声明驱动。

## Future work

- 实测尺寸反馈（渲染后上报实际 box，替换估算表）；
- P6 高级组件（graticule/map_border/inset_map）接入同一求解器；
- 碰撞披露回灌 finalizer 的 F_LAYOUT_CONFLICT（后端镜像几何语义）。
