# 前端 UI 审查报告

- 审查人: Agent D（前端 / UI / WebGIS 交互体验专项）
- 审查日期: 2026-08-24
- 审查方式: 只读代码精读（未启动浏览器 / 服务），所有问题均已在代码中确认触发路径真实存在
- 范围: `frontend/components/`（chat、map、hud、sidebar、explorer、report 链路、settings、layout、drawers、panel、shared、ui）、`frontend/app/`、`frontend/lib/`（stores、map-kit、mapspec、map-commands、hooks、api）、`frontend/test/`

---

## 1. 前端架构与已有 UI 资产小结

**技术栈与布局**。Next.js 14 App Router（`app/page.tsx` 单页工作区 + `app/story` StoryMap 回放页），地图为 `react-map-gl/maplibre`（`components/map/map-panel.tsx`），全局壳层：TopBar + NavRail（竖排 tab，roving tabindex）+ ContextPanel（8 个左侧 tab，支持右缘拖拽调宽 / 键盘 ±16px）+ 底部 EmbodiedHud 遥测条（24px 收起 / 210px 展开）。样式为 Tailwind + Visual System V4 设计令牌（`app/globals.css` 双主题，`--agent-accent` 运行时主题色，theme/accent/fontSize 均持久化且 `layout.tsx` 内联脚本防首帧闪烁）。

**状态管理**。Zustand slice 拆分（`lib/store/slices/`：layers/results/task/settings/ui），`skipHydration` + gated storage 防止 rehydrate 前的默认值覆写持久化数据（`lib/store/useHudStore.ts`），刻意不持久化 layers（防 localStorage 配额爆掉）。地图期望态走 MapSpec 单一事实源：`lib/mapspec/session-cursor.ts`（committed doc + revision + pending overlay）、`lib/mapspec/live-spec.ts` 组装、`lib/mapspec-runtime/` 增量 reconcile；用户 chrome 编辑（显隐/透明度/删除/重排）经 `lib/mapspec/user-mutation.ts` 走 `apply_mutation` + `expected_revision`，409 superseded 时回灌服务端真相并 toast 提示。

**地图指令链路**。20 个指令全量覆盖于 `lib/map-commands/catalogue.ts`（view/layer/heatmap/annotation/export/query 六个 slice），`map-action-handler.tsx` 统一 settle（queued→running→terminal，恰好一次），失败经 `setPendingSystemMessage` → `SystemMessageBridge` toast 可见；ACK 管道（`map-action-acks`）带 correlation 与去重；相机仲裁（`camera-arbitration.ts`）区分用户手势与程序动画；cartographic observation→repair 有 generation/fingerprint/双响应去重三重防护。

**SSE 与进度**。`useMapBridge.ts` 拥有有界自动重连（2 次、Last-Event-ID resume、事件 id 去重、跨会话事件拒收）；`use-sse-stream.ts` 含 TokenBatcher（rAF 合帧）、增量 think 解析、工具链 ToolCallChain 终态兜底（流死亡不再永久 spinner）、explorer 独立进度流；任务中心 `use-job-center.ts` 轮询严格有界（无活跃任务 0 请求、隐藏暂停、连续失败封顶、陈旧响应丢弃）。

**测试资产**。vitest + 设计契约测试（`test/design-system/` tokens/contrast/visual-system 契约）、地图 mock surface、大量 TDD 命名测试（backfill-race、cartography-race、selection-truth、live-sync 等）。多轮修复（honest style panel、radius contract、mutation rollback、#692/#739/#804 等）质量确实很高——本轮发现的问题多数藏在"组件存在但无入口"与"两套平行实现漂移"的缝隙里。

---

## 2. 发现的问题

### U-1 [P2] 图层样式编辑面板等多块完整 UI 无任何生产入口（不可达死代码），"样式编辑"功能实际缺失
- **问题描述**: `LayerStylePanel`（图层样式编辑：填充/描边颜色、描边宽度、点大小、线型、热力半径、栅格亮度/对比度/饱和度、重命名、重置样式，共 341 行）在全部生产代码中零导入——没有任何组件 mount 它，也没有任何代码以非 null 参数调用 `setEditingLayerId`（唯一调用点是面板内部自己的关闭按钮 `setEditingLayerId(null)`）。同样不可达的还有：`components/settings/layer-management.tsx`（设置里的图层管理面板，SettingsPanel 的 `NAV_ITEMS`/`TabContent` 均未包含）、`components/drawers/map-combinator.tsx` 与 `telemetry-status-card.tsx`（仅被同样无人导入的 barrel `drawers/index.ts` 导出）、`components/hud/causal-trace.tsx`、`components/panel/asset-card.tsx`、`components/panel/chart-renderer.tsx`。而 ContextPanel 的图层 tab 描述明确承诺"可见性 · **样式** · 顺序"（`context-panel.tsx:93`），实际用户只能控制可见性/透明度/顺序/删除。
- **影响范围**: 全部用户。矢量图层的颜色/线型/点大小、热力半径、栅格调色等手动样式编辑完全无法触达；settings 中声称的图层管理入口不存在；约 900+ 行带测试的维护中代码成为死重量（测试仍然通过，掩盖了不可达事实）。
- **代码位置**: `frontend/components/hud/layer-style-panel.tsx:12`（组件定义，无生产导入者）；`frontend/lib/store/slices/layersSlice.ts:125-126`（`editingLayerId` 除面板自身外无生产写入者）；`frontend/components/settings/layer-management.tsx:16`（无导入者）；`frontend/components/drawers/index.ts:1-3`（barrel 本身无导入者）；`frontend/components/hud/causal-trace.tsx:1`；`frontend/components/panel/asset-card.tsx:1`；对照承诺文案 `frontend/components/layout/context-panel.tsx:93`
- **原因分析**: 历史重构（UI V3 左侧面板化、settings 面板重写）时入口被移除但组件与测试保留；`editingLayerId` 的生产者（原图层行的"样式"按钮）随之消失，且没有任何契约测试断言"面板可达"。
- **优化方案**: ① 在 `layers-tab.tsx` 每行操作区加一个样式编辑 IconButton（调 `setEditingLayerId(layer.id)`），并在 ContextPanel 图层 tab 内以条件渲染 mount `LayerStylePanel`（或侧滑抽屉）；② 若决定砍掉该功能，删除 `layer-style-panel.tsx`、`layer-management.tsx`、`drawers/index.ts`+两个 drawer、`causal-trace.tsx`、`asset-card.tsx`、`panel/chart-renderer.tsx` 及其测试，并把 `context-panel.tsx:93` 的描述改为"可见性 · 透明度 · 顺序"，避免虚假承诺；③ 补一条"入口可达性"契约测试（渲染工作区后 `setEditingLayerId` 路径能打开面板）。
- **验证方式**: `cd frontend && grep -rn "layer-style-panel\|LayerStylePanel" components app --include="*.tsx" | grep -v test`（确认零生产导入）；修复后 `cd frontend && npx vitest run components/hud/layer-style-panel.test.tsx components/sidebar/layers-tab.test.tsx && npm run typecheck`

### U-2 [P2] MapSpec 制图组件：连续色条与比例尺默认同锚点 bottom-right，二者互相重叠
- **问题描述**: 当 committed MapSpec 携带 `continuous_colorbar`（GIS Harness 制图的标准产物）时，live 端由 `MapSpecChrome` 渲染 chrome。`scale_bar` 与 `continuous_colorbar` 的缺省位置都是 `bottom-right`，且两者的底部偏移样式完全相同（`bottom: calc(var(--map-chrome-bottom,10px) + 30px)` + `right-3`），内联 `bottom` 又覆盖了 `bottom-3` class——两个元素锚定在同一点，后渲染的 colorbar（DOM 顺序在 scale_bar 之后）直接压在比例尺上。HUD 自有 chrome 族是刻意分层堆叠的（状态读数 +0、比例尺 +30、热力图例 +66，见 `page.tsx:309` 与 `map-decorations.tsx:63`），`MapSpecChrome` 却把两个组件塌缩到同一层。#804 的回退逻辑还保证了"colorbar-only spec 也一定渲染比例尺"，即所有带色条的 spec 都必然触发该重叠。
- **影响范围**: 所有由 AI 制图规范驱动（spec 带 layout.components）的会话：底右角比例尺被色条遮盖/互相穿插，读不出比例尺或色条数值；live 视图与导出成品（exporter 自行排版）不一致。
- **代码位置**: `frontend/components/map/map-spec-chrome.tsx:52`（`scale_bar: 'bottom-right'`）、`:54`（`continuous_colorbar: 'bottom-right'`）、`:61-65`（`BOTTOM_OFFSET_STYLE` 两者同为 `+30px`）、`:256-271`（scale_bar 渲染）、`:288-324`（colorbar 渲染）；既有测试只断言两者都渲染、未断言不重叠：`frontend/components/map/map-spec-chrome.test.tsx:294-305`
- **原因分析**: `BOTTOM_OFFSET_STYLE` 只按"位置槽"给偏移，没有按"同槽多组件"堆叠；#804 补回退比例尺时未审视它与同槽色条的碰撞。
- **优化方案**: 在 `MapSpecChrome` 内对 bottom-right 槽内的组件按渲染顺序递增偏移（如色条沿用 +30，比例尺让位到 +66，复用 HUD chrome 的堆叠约定）；或把缺省 colorbar 位置改为 `bottom-center`/`bottom-left` 与比例尺分槽；同步更新 exporter 的排版约定保持 live/export 一致。
- **验证方式**: 修复后新增断言（渲染 colorbar+scale_bar spec，断言两元素 bottom 偏移不同/无交集），`cd frontend && npx vitest run components/map/map-spec-chrome.test.tsx`

### U-3 [P2] 图层显隐/删除/重排的服务端提交失败后静默回滚，用户得不到任何失败提示
- **问题描述**: `user-mutation.ts` 中 409 superseded 路径有 toast 收敛提示（#692 修复），但**非 409 失败**（网络断开、5xx、会话过期）路径全部是"本地回滚 + 静默"：`toggleLayerAndCommit` catch 后回滚 visible 不提示；`removeLayerAndCommit` catch 后 `setLayers(previous)` 不提示；`reorderLayersAndCommit` 同样；`commitExplicitView` 注释自述"其它错误吞掉并保持调用方无感"。用户点删除 → 图层消失 → 一秒后又弹回来，全程无任何解释；断网时每次操作都这样"弹回"，看起来像按钮坏了。
- **影响范围**: 所有在弱网/后端故障时做图层显隐、透明度（`setLayerOpacityAndCommit` 同款 catch 回滚）、删除、重排操作的用户；也包括 focusLayer 后的 `commitExplicitView` 失败（视口真相丢失且无感）。
- **代码位置**: `frontend/lib/mapspec/user-mutation.ts:131-133`（toggle 回滚无提示）、`:228-233`（remove 回滚无提示）、`:244-246`（reorder 回滚无提示）、`:255-257`（opacity 回滚无提示）、`:165-175`（commitExplicitView 非 409 静默 return）；对照 superseded 路径已有 toast：`:115-120`
- **原因分析**: #692 只把"superseded 收敛"做成了非静默，rollback 分支沿用了早期"调用方无感"的写法；调用点全部 `void xxxAndCommit(...)`，rethrow 也无人消费。
- **优化方案**: 在各 catch 回滚处复用 superseded 路径的 toast 模式，`useToastStore.getState().addToast(\`操作未生效（已恢复）：${describeApiError(err, '网络错误')}\`, 'error')`（describeApiError 已在同目录 transport 导出，map-studio-tab 已有同款先例）；注意按操作语义给出文案（"删除图层失败，已恢复"/"排序未保存，已恢复"）。
- **验证方式**: 修复后 `cd frontend && npx vitest run lib/mapspec/user-mutation.test.ts`（新增断言：mock apiFetch reject 非 409，断言 addToast 被调用且回滚发生）

### U-4 [P3] HUD 步进器"失败"圆点使用非法 CSS 颜色变量，失败态圆点不可见
- **问题描述**: `embodied-hud.tsx` 失败态圆点取色 `var(--danger, var(--destructive, #dc2626))`。`--danger` 从未定义；`--destructive` 虽有定义但值是 shadcn 约定的 **HSL 裸通道三元组**（`347 82% 41%`，供 Tailwind `hsl(var(--destructive))` 使用），直接作为 `backgroundColor` 是非法颜色 → 声明在计算值阶段被丢弃 → 圆点透明不可见。#692 专门设计的"错误色静止圆点如实示败"在两主题下都渲染不出来（暗色主题 `--destructive: 351 95% 71%` 同样非法），只剩步骤名文字，错误指示弱化。
- **影响范围**: 所有触发 `aiStatus === 'error'` 的回合（工具失败/流死亡），底部 HUD 展开状态下三步stepper 的失败标记不可见。
- **代码位置**: `frontend/components/hud/embodied-hud.tsx:356`（非法取色）、`:374-381`（dot 仅此一个 backgroundColor，无其他底色兜底）；`frontend/app/globals.css:38`、`:249`（`--destructive` 为 HSL 三元组定义）
- **原因分析**: 变量命名想当然地做了三层 fallback，但中间层 `--destructive` 的"已定义"使最终字面 fallback `#dc2626` 永远不生效，而其值本身不能作颜色用；设计令牌迁移 V4 时未扫描此处。
- **优化方案**: 改用 V4 语义令牌 `dotColor = 'var(--status-critical)'`（或既有 `--critical` 十六进制变量，与 StatusBadge/toast 的错误色同源），删除无效 fallback 链；建议在设计令牌契约测试中加一条"组件内联取色不得引用 HSL 三元组变量"。
- **验证方式**: 修复后 `cd frontend && npx vitest run components/hud/embodied-hud.test.tsx`（可新增断言：failed 态 dot 的 color 不含 `--destructive`）

### U-5 [P3] POI 属性面板无加载态、无"近似数据"披露：isApproximate 只上报给 LLM，不给人看
- **问题描述**: 点击 MVT 大图层的要素时（`map-panel.tsx:705` 置 `isApproximate: true`），面板先展示瓦片裁剪出来的近似属性，随后异步 backfill 权威要素（成功则静默替换属性，失败则保留近似值）。但 `poi-info-panel.tsx` 全程没有任何 loading 指示，也从不读取 `isApproximate`——该标志只被 `buildSelectedFeatureSnapshot`（`use-sse-stream.ts:256`）作为 `is_approximate` 发给后端 LLM（#668 注释明确"honest approximation flag — LLM must not treat tile geometry as source truth"），对人类用户却是隐身的。backfill 失败时用户会把截断/近似属性当成权威数据；在途窗口内属性会"自己变"，无任何解释。
- **影响范围**: 所有 >5000 要素的 MVT 图层（数据面主力路径）的要素点击查询体验。
- **代码位置**: `frontend/components/map/poi-info-panel.tsx:59-198`（无任何 loading/近似标记；全文件无 isApproximate 引用）；`frontend/lib/store/hud-types.ts:19`（字段定义）；`frontend/components/map/map-panel.tsx:694-756`（backfill 与 isApproximate 维护）
- **原因分析**: #667/#668 的"诚实近似"只做到了 store 与 LLM 通道，UI 消费端漏接；poi-info-panel.test 也未覆盖该态。
- **优化方案**: `PoiInfoPanel` 订阅 `selectedFeature`（已订阅，第 69 行）后：`isApproximate === true` 时在属性区顶部渲染一行 `text-micro text-status-warning` 的"瓦片近似数据，正在核实…"，backfill 完成（isApproximate false）后移除；backfill 失败终态显示"权威数据获取失败，以下为近似值"。
- **验证方式**: 修复后 `cd frontend && npx vitest run components/map/poi-info-panel.test.tsx components/map/poi-info-panel.live-sync.tdd.test.tsx`（新增断言：isApproximate true 时出现提示文案）

### U-6 [P3] 图层面板不透明度滑杆：键盘调整不生效直到失焦（与样式面板的实现漂移）
- **问题描述**: `layers-tab.tsx` 的行内不透明度滑杆只在 `onPointerUp`/`onBlur` 提交（键盘用户按方向键只更新本地 draft，地图与 store 不更新，必须 Tab 离开才生效）；而 `layer-style-panel.tsx` 的同类滑杆是 `onPointerUp` + `onKeyUp` + `onBlur` 三路提交。键盘用户在图层面板拖不动"地图反应"——滑杆读数变了但图层透明度纹丝不动，看起来像坏了。
- **影响范围**: 键盘用户/精确调节场景（无鼠标）下的所有图层透明度调整。
- **代码位置**: `frontend/components/sidebar/layers-tab.tsx:303-317`（仅 onPointerUp/onBlur）；对照 `frontend/components/hud/layer-style-panel.tsx:313-324`（含 onKeyUp）
- **原因分析**: FE-03 优化（拖动不逐 tick 写 store）落地时两个面板各自为政，layers-tab 漏掉键盘提交路径。
- **优化方案**: layers-tab 的滑杆补 `onKeyUp={() => commitOpacity(layer)}`（方向键每次提交一个离散值，成本可接受）；顺带为两个滑杆统一抽一个 `OpacitySlider` shared 组件，消除双实现漂移。
- **验证方式**: 修复后 `cd frontend && npx vitest run components/sidebar/layers-tab.test.tsx`（新增：fireEvent.keyUp(range) 后断言 store.opacity 更新）

### U-7 [P3] StoryMap 播放器：当前消息无任何视觉高亮；暂停后再按播放强制从头开始
- **问题描述**: ① 播放时 `activeIndex` 只驱动 `scrollIntoView`（`story/page.tsx:74-76`），消息列表渲染（`:232-245`）完全不使用 `activeIndex`——所有 assistant 消息外观一致，用户看不出"现在讲到哪一条"；② `togglePlay`（`:100-108`）每次播放都 `setActiveIndex(0)`，暂停在第 5 条后再按播放会跳回第 1 条，播放/暂停按钮语义违背直觉（应有"从头重播"独立入口或暂停续播）。
- **影响范围**: StoryMap 回放页（分享/汇报场景）的演示体验。
- **代码位置**: `frontend/app/story/page.tsx:100-108`（togglePlay 重置到 0）、`:232-245`（消息渲染不消费 activeIndex）、`:74-76`（仅滚动）
- **原因分析**: #552 给三个空按钮接行为时按最简实现补齐，播放器状态机只做了"自动推进"，没做"当前项指示"与"暂停/重播分离"。
- **优化方案**: ① 渲染时对 `idx === activeIndex` 的消息加高亮（如 `ring-1 ring-status-accent-border` 或左侧指示条 + 其余消息降透明度）；② `togglePlay` 改为续播（仅在 `activeIndex >= messages.length - 1` 时回到 0），"从头播放"由 SkipBack 长按/独立按钮承担。
- **验证方式**: 修复后 `cd frontend && npx vitest run app/story/page.test.tsx`（新增断言：activeIndex 对应消息带高亮类名；暂停后 play 不重置 activeIndex）

### U-8 [P3] "缩放到图层"按钮对无 bbox 图层点击后完全无反馈（代码注释自认 no-op）
- **问题描述**: 图层面板每行的"缩放到图层"IconButton 调 `focusLayer(layer.id)`，但 map-panel 的聚焦效果在算不出 bbox（无 `_descriptor.bbox`、无 `source.bbox`、无内联几何，典型：无描述符的栅格/瓦片图层、空 ref 占位图层）时直接什么都不做，800ms 后清掉 focusLayerId。代码注释明确承认"无 bbox/无几何的图层点击后无反应是预期行为"。对一个始终可点、无禁用态的按钮，点击无任何反馈违背基本可操作性反馈；用户会反复点击并认定功能坏了。
- **影响范围**: 无范围信息的图层（数据源异常、描述符缺失的会话）上的聚焦操作。
- **代码位置**: `frontend/components/sidebar/layers-tab.tsx:320-328`（按钮 + 自认 no-op 的注释）；`frontend/components/map/map-panel.tsx:237-277`（`bbox` 为 null 时静默跳过 fit，仅调度复位定时器）
- **原因分析**: focus 协议没有"无法聚焦"的回传通道，图层侧也就无法禁用按钮或提示。
- **优化方案**: ① 短期：在 map-panel 聚焦效果里 bbox 为 null 时 `useToastStore.addToast('该图层暂无空间范围，无法缩放', 'info')`；② 结构化：`Layer` 增加 `hasBounds` 派生（`_descriptor.bbox || source.bbox || 有内联 features`），layers-tab 据此禁用按钮（`disabled` + aria-disabled + tooltip 说明）。
- **验证方式**: 修复后 `cd frontend && npx vitest run components/sidebar/layers-tab.test.tsx`（断言无 bbox 图层的 LocateFixed 按钮禁用或点击出现 toast）

### U-9 [P3] 底图切换器把键盘漫游高亮谎报为 aria-selected（ARIA 语义误用）
- **问题描述**: `baselayer-switcher.tsx` 的 listbox 选项 `aria-selected={isActive || idx === activeIdx}`——键盘 ArrowDown 移动 `activeIdx`（仅视觉漫游高亮，未选择）时，被漫游到的 option 会对读屏用户播报"已选中"，而实际生效的底图仍是另一项（aria-activedescendant 指向的"selected"项与真实选中项不一致）。APG listbox 语义中 selected 只能表达真实选中态。
- **影响范围**: 读屏用户切换底图时的状态播报（会听到错误的选中项）。
- **代码位置**: `frontend/components/map/baselayer-switcher.tsx:157`（aria-selected 复合条件）；对照同文件 `:171-176`（视觉上已正确区分 isActive 底色与 activeIdx hover 底色）
- **原因分析**: #700 键盘漫游补齐时把"视觉高亮"直接并进了 aria-selected，混淆了 focus/active 与 selected 两个语义。
- **优化方案**: 改为 `aria-selected={isActive}`（漫游高亮由 `aria-activedescendant` 表达即可），视觉样式保持现状。
- **验证方式**: 修复后 `cd frontend && npx vitest run components/map/baselayer-switcher.test.tsx`（新增断言：ArrowDown 后仅真实选中项 aria-selected=true）

---

### 汇总

| 编号 | 级别 | 一句话摘要 |
|------|------|------------|
| U-1 | P2 | 图层样式编辑面板等 6 块 UI 无生产入口，样式编辑功能实际缺失 |
| U-2 | P2 | spec 制图模式下连续色条与比例尺同锚点重叠 |
| U-3 | P2 | 图层显隐/删除/重排提交失败静默回滚，无用户提示 |
| U-4 | P3 | HUD 失败态圆点引用非法 CSS 颜色变量，不可见 |
| U-5 | P3 | POI 属性面板无加载态/近似数据披露（isApproximate 只给 LLM） |
| U-6 | P3 | 图层不透明度滑杆键盘调整不生效直到失焦 |
| U-7 | P3 | StoryMap 播放无当前消息高亮，暂停后重播强制从头 |
| U-8 | P3 | 缩放到图层按钮对无 bbox 图层点击无任何反馈 |
| U-9 | P3 | 底图切换器把键盘漫游高亮谎报为 aria-selected |

优先级建议：先做 U-1（决定样式编辑是"补入口"还是"删死码"，牵动 ~900 行代码去向）与 U-3（弱网下用户操作"神秘弹回"直接损伤信任）；U-2 随下一次制图组件改动合入；其余 P3 可批量在一个 a11y/反馈 polish PR 中处理。
