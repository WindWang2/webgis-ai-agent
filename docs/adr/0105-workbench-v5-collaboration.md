# ADR-0105: Professional GIS Workbench & Collaboration V5

状态：已实施（feat/workbench-v5-collaboration）｜日期：2026-09-09
关联：ADR-0104（Workbench V4）、ADR-0091（Interactive Workspace V4）、ADR-0058（CAS revision）

## 1. 决策与理由

### 1.1 Workbench 组织态持久化（修订 ADR-0104 的「不持久化」边界）

ADR-0104 声明 workbenchSlice（分组/选择/锁定/隔离/模式）"不持久化、不进 MapSpec"。V5 反转**组织态**部分：

- 新增 `patch_workbench_state` MapSpec mutation 意图，把 `WorkbenchDocV5`（嵌套分组树 / membership / lockedLayerIds / mode）整体写入 `mapspec["workbench"]` 分支；
- 走**既有** per-session 锁 + `expected_revision` CAS + provenance 通道，不建第二事实源、不建新表、不建 localStorage 影子（会话锚仅存 sessionId 指针）；
- 选择（selection）、隔离（isolate）、搜索、对比 position 保持 transient（与 ADR-0104 一致）。

理由：分组树/锁是用户跨刷新期望保留的工作意图；选择/隔离是会话内瞬时手势。64KB→256KB 上限依据 10k 图层场景全量 membership 的真实字节数；组织态不携带数据载荷。

### 1.2 side-by-side 真双面板（反转 ADR-0104 的「诚实下线」）

V4 在「主地图零改动」约束下 side-by-side 双半屏永不共地理（诚实下线为 0.5 裁剪）。V5 解除该约束：side-by-side 激活时主图画布收缩为左半幅（map-panel 容器类切换，MapLibre trackResize 自动适配），副图占右半幅、相机同步 —— 两窗格为真实半宽视口，覆盖同一地理范围。副视图 parity：is3D/terrain、activeFilters、selectionFilters 与主图同款状态透传 compose 通道；副图层族图例经同一 LegendStack 渲染。

### 1.3 Agent lock 强制（typed 冲突）

锁定语义从「UI 护栏」升级为全通道护栏：agent 可见性事务与 remove_layer 在身份解析后按锁定集分区；全部被锁 → ack `status=failed, error="layer_locked"`（`ack.error` 为自由字符串，零 schema 变更）；部分被锁 → 只应用未锁目标并披露 `locked_layer_ids`。用户唯一 override = 显式解锁。

### 1.4 Undo/Redo 与 journal

- 命令模型：capture-before-execute；undo/redo = 经既有提交通道的**反向重放**（非状态回写），async/agent 突变安全（409 由既有收敛路径处理）；有界 50；会话切换清空。
- 不可逆操作（remove_layer 落账）仅入 journal（`reversible:false` 元数据）；opLog（journal）接活写入点，修复 V4「零生产者」断供。
- 折叠/展开为轻量展示态，只入 journal 不入 undo 栈（内存取舍）。

### 1.5 多 tab 协同基座

BroadcastChannel（`wb5:{sessionId}`）只广播**已提交**事实（doc + revision）；接收方以「不旧于本地已见 revision」门控采纳并水合；确定性冲突解决 = 服务端 CAS last-writer-wins。同源同浏览器边界；session 所有权仍由服务端校验。无 presence/CRDT —— 不造第二真相。

### 1.6 刷新恢复

localStorage 会话锚（sessionId + authed + savedAt，7 天 TTL）：仅认证会话自动恢复（匿名 owner_token 是内存能力，不持久化）；恢复走既有 selectSession 全管线（消息 + map-state + 图层 + workbench doc）。pagehide 时 best-effort 冲刷防抖窗口内的 doc 变更。

### 1.7 10k 图层渲染

投影后扁平行描述符 + 固定行高窗口虚拟化（自研 hook，>200 行启用）；折叠传播在扁平化层生效；锁定/选择 Set 化，投影 O(n)。inline GeoJSON 视口通道在 bbox 过滤后增加确定性网格抽稀（8×8 cell，面积优先，预算 5000）与陈旧视口应用取消（generation token + idle 回调）。

## 2. 已知限制

- 多 tab 下 presentation（显隐/样式）跨 tab 同步依赖各自会话收敛，不实时广播（collab 仅覆盖组织态 doc）；
- 虚拟化窗口（>200 行）内行高固定 34px，展开「更多操作」次行的可见性受限；
- undo 不覆盖 remove_layer（重挂需 source 数据重取，诚实标记不可逆）；
- 多目标（一 ref 多层）agent 可见性突变只入 journal 不入 undo（不可整单撤销）。

## 3. 未来工作

- 组树拖拽 reparent UI（`moveLayerGroup` API 已就绪）；
- presence 与游标共享（需要服务端事件总线）；
- membership 倒排/差量 patch（超出 256KB 场景）；
- presentation 跨 tab 广播通道。
