# Agent C — Layer Lifecycle / Session Switch / Agent+Manual 静态追踪笔记

Base: master@2aabdc43。本文件给出三条关键路径的静态追踪结论与状态一致性矩阵，作为 findings.md 的证据底稿。行号均为当前 master。

## 0. 权威状态面（五个真相源 + 一份投影）

| 面 | 位置 | 写入者 |
|---|---|---|
| 后端 desired MapSpec | `mapspec.json` + Redis `mapspec` 字段（store.py） | MapSpecLifecycleEngine.apply_mutation / apply_presentation_batch（分布式 session 锁 + CAS expected_revision + checkpoint + 事务回滚） |
| 后端 CAS 令牌 | `_cartographic_mutation_revision`（Redis）+ `mapspec.json.rev` sidecar（#1073/F-9 复活路径回写 hint，store.py:262-284） | 同上（commit_mapspec_state 单事务） |
| 运行时 layers 注册表 | session_data `layers` 数组 | 仅 Upsert/Remove/Rollback 经 layer_op 写（ST-P3-5：presentation patch 不写） |
| 前端 committed 投影 | session-cursor `committed` + `revision`（前端） | SSE mapspec 事件（带 revision 单调门）、user-mutation/visibility/component 提交响应 |
| 前端 HUD 镜像 | useHudStore.layers（行上带 `_mapspecLayerId`/`_refId`/`_tileUrl`） | addLayer（step_result 挂载）、syncSpecLayersToStore（spec→行镜像 + 行修剪）、restore |
| 地图实况 | MapLibre style + MapSpecRuntime.appliedSpec | reconcileAsync（compose → diff → debounced ops，#459 epoch 门、#375 z 序门） |

合成式：`composeLiveMapSpec(committed, hud, pending, removed)`（live-spec.ts:149-233）—— committed 层（滤 pendingRemoved、叠 pending presentation）在前 + HUD-only 层在后；层序 = committed.layers 序。pendingRemoved/pending 是用户/agent 在途乐观态对 compose 的压制/覆盖层。

## 1. Layer lifecycle 逐阶段矩阵（create→mount→show/hide→style→reorder→group→edit→remove→restore）

| 阶段 | 用户路径 | Agent 路径 | backend MapSpec | 前端 mirror(HUD) | map runtime | Layer Tree | 一致性结论 |
|---|---|---|---|---|---|---|---|
| create(数据) | 上传/分析 ref | 工具产出 ref + `webgis_layer_upsert`/`map_product` | UpsertLayerIntent（锁+review+checkpoints+ref stamping，lifecycle_engine.py:1332-1403） | step_result addLayer（隐藏挂载）或 syncSpecLayersToStore 镜像 | ref-only 源由 debounced patch 跳过，数据到位后 source:update+recompile 补挂（runtime.ts:340-396） | 行出现 | 一致；同 id upsert 保留用户 durable presentation（ST-P2-2 已修，_preserve_durable_presentation:960-1017） |
| mount | — | 同上 | — | — | reconcileAsync 串行链 + 入队合并（G-5） | — | 一致 |
| show/hide | layers-tab toggle → commitLayerPresentation（串行链+CAS+pending 保护） | layer_visibility_update/finalize → applyLayerVisibilityTransaction（lock 门 + desired→runtime→durability→postcondition） | PatchLayerPresentationIntent / GISMutationBatch（user-wins 守卫 + presentation_owner 印记 + AUTO_SAFE） | applyCommittedMapSpec 跳过在途 pending 层 | setLayoutProperty 即时 + compose 叠 pending | 即时翻转 | 一致（durability 队列的迟到响应缺陷见 C-2） |
| style | 面板 → commitLayerStyleAndCommit（patch_layer_style，#1077） | LAYER_STYLE_UPDATE 命令（运行时 paint，specBacked 诚实 durable:false） | PatchLayerStyleIntent 进 spec | updateLayer(style) | updateLayerStyle | swatch 同步 | 命令路径对 spec 层的回滚风险已在 ack 披露（layerCommands.ts:801-821）；一致 |
| reorder | 拖拽 → reorderLayersAndCommit → POST reorder_layers | reorder_layer 命令只改 HUD store | 用户路径 ReorderLayersIntent ✓；agent 路径**无写** | 用户路径回灌新序；agent 路径仅本地行序 | compose 层序=committed 序 → agent 路径 spec 层 z 序不动 | agent 路径显示新序 | **分叉：C-3** |
| group/lock | workbench doc（delta/全量 + workbench `_rev` CAS + 协作总线） | agent 突变被 guard_intent_locks / 前端 partitionByLock 拒（layer_locked 单码契约） | SetWorkbenchState/PatchWorkbenchDelta（256KB 上限/环深校验） | workbenchSlice + persistence（armed 门、rebase 一次） | 不涉 | 分组树 | 一致（锁守卫双实现债见 C-7） |
| edit(组件) | 拖拽收尾 → commitComponentPatch | webgis_component_update（局部突变）/layout_set（整表） | Patch/Remove/Duplicate/RebindComponentIntent（96KB 组件上限、锁内 layerId 复核） | override 乐观层与 spec 收敛（component-mutation.ts:258-303） | MapSpecChrome/导出同源组件 | 组件面板 | 一致；upsert 工厂缺 4 类见 C-8 |
| remove | DeleteLayerButton → removeLayerAndCommit（pendingRemoved + 单次 superseded 重试 + 双 superseded 保 pending + 外科式回滚） | remove_layer 命令（map+store 删，durability 走 removeLayerFromSpec）+ webgis_layer_remove（后端直写） | RemoveLayerIntent（family 谓词清扫，source 不 GC 为既定契约） | 行删除；`unsynced` 保留 pending 压制 compose | reconcile 移除（含 label 子层） | 行删除 | 用户路径一致；**agent 路径 durability 永不发出 → 僵尸复活：C-1** |
| restore | 会话切换/story 页 | — | mapspec + observation（指纹匹配优先） | buildLayerFromRestored + allowedIds 过滤 + ref 回填（abort 守卫） | reconcile 全量 | 行重建 | 一致（pendingRemoved 幽灵行防护 G-9/P1 在位） |

结论：lifecycle 主干在“用户路径”上闭环完整（ST 修复质量高）；两处断裂都集中在 agent 侧通道（C-1 durability、C-3 reorder），一处并发窗口（C-2）。

## 2. Session switch（A pending → 切 B → A response 到达）污染面矩阵

守卫清单（A→B 时各自生效性）：

| 到达通道 | 守卫 | 结论 |
|---|---|---|
| SSE 事件（mapspec/revision/图层挂载） | useMapBridge：streamSessionId 比对 + abort（useMapBridge.ts:361-389）；use-sse-stream INV-2 顶部丢弃（use-sse-stream.ts:513-518）；commitMapSpecDocument 旧代次门 | 干净 |
| 独立 explorer 进度流 | explorerAbortRef 随 sessionId abort（use-sse-stream.ts:466-475） | 干净 |
| 图层 ref 数据 GET 回填 | layerFetchAbortRef abort + `current._refId === fetchRef` 双检（use-sse-stream.ts:786-789） | 干净 |
| restore 的 ref 回填 | opts.signal + `opts.signal?.aborted` + `_refId` 双检（map-state-restore.ts:360-368） | 干净 |
| 过滤证据结算 | filterEvidenceSessionRef 比对（map-panel.tsx:662-667） | 干净 |
| 框选发布 | selection epoch（map-panel.tsx:986/998） | 干净 |
| map-action ACK 响应内 repair | `responseSessionId !== sessionIdRef.current` 拒（useMapBridge.ts:212） | 干净 |
| **user-mutation 五个提交函数** | await 后复核 "Review R1 MAJOR-1"（user-mutation.ts:135/211/308/352、removeLayerFromSpecOnce:408） | 干净 |
| commitComponentPatch | await 后复核（component-mutation.ts:109/119） | 干净 |
| workbench 持久化 | targetSessionId 门 + notifyWorkbenchSessionChanged 解武装（persistence.ts:83-95/185-186） | 干净 |
| **visibility durability（postPresentationOnce）** | 仅入队前检查一次；成功/superseded 分支 await 后直接写 setMapSpecRevision + commitMapSpecDocument + clearPendingPresentation | **污染：C-2** |
| **commitComponentLifecycle** | 同型（component-mutation.ts:200-213） | **污染：C-2** |
| agent remove durability（layerCommands） | removeLayerFromSpecOnce 会话预检 —— 但因 C-1 未传参，效果反而是“永不 POST”（歪打正着不跨会话，但也不写本会话） | 见 C-1 |

游标重置时机：selectSession 同步 `setMapSpecSessionCursor(sid, 0, token)`（use-workspace-session.ts:204，#736），restore 成功后再以服务端 revision 重设（:266-270）。setMapSpecRevision 单调门 + commitMapSpecDocument 旧代门只拒“回退”，A 的 revision>0 对 B 的 0 是前进方向 —— 门不设防，必须靠写入者复核（C-2 根因）。

## 3. Agent+Manual 交互（agent 改图 → 用户手动 → agent 继续）

- **user-wins presentation**：三层防护 —— ① spec 印记 `cartographic_intent.presentation_owner/expected_visible`（_patch_layer_presentation:901-938，幂等同值不改写归属 R2-P2-1）；② 环形 provenance legacy 守卫（mutation.py:120-171，H3 扩展到 upsert 的家族存在性判定）；③ 锁内复检 seam（pre_commit_check）。结论：agent 无法反转用户显隐/透明度决策（同值重放允许）。
- **workbench 锁**：guard_intent_locks（agent/system 全 intent 整笔拒绝）+ 前端 partitionByLock（部分拒绝披露 locked_layer_ids）+ AUTO_SAFE 修复环锁抑制（quality_loop.py:614-634）。结论：锁定层对 agent 突变、修复环均封闭；双实现债 C-7。
- **stale revision**：agent 上下文经 webgis_state_get 读权威 spec + revision（F1 复活序修复后 CAS 令牌随 spec 恢复）；迟到 SSE 不回退游标（ST-P3-1 修复在位）。结论：无 stale-revision 复活面。
- **重复层**：同 id upsert 走 `_should_remove_layer` family 替换 + pendingRemoved 压制 + syncSpecLayersToStore 幂等去重（id/_mapspecLayerId 双键）+ G-9 行修剪。结论：无双行。
- **visibility 重置**：用户隐藏层被 agent 重跑 upsert → `_preserve_durable_presentation` 剥离 agent 的 visibility 覆写并继承 owner 印记（#1070 F-3）；agent finalize 经 GISMutationBatch 以 agent origin 落盘（不再洗白 user 印记，H2 修复在位）。
- **残留缺口**：① agent remove 与用户删除并发/顺序执行时，C-1 使 agent 删除不落 spec（用户视角“删了又活”）；② agent reorder 与用户手动排序互相覆盖时，C-3 使 agent 序不落 spec（用户 reload 后看到自己的序被“恢复”，实为 agent 序从未生效）；③ C-2 窗口内用户切会话后的乐观 pending 被旧响应清除。

## 4. 行为级检查清单命中情况（任务书样例）
- “按钮存在但当前状态不应可用”：C-8（create=true 对 4 类型恒失败，错误提示误导）。
- “界面显示成功但 backend 未成功”：C-1（agent remove 本地成功、backend 未删）；C-3（ack store_updated 但 z 序/后端未变，虽未谎称 confirmed）。
- “图层开关 UI 变化但地图没同步”：C-3 的 reorder 变体（面板动、地图不动）。
- “地图正确但 Layer Tree 状态错误”：C-1 复活态的中间窗口（地图有层、面板无行）。
- 跨会话污染：C-2。
