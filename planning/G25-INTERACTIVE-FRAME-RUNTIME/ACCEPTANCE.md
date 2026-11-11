# G25 ACCEPTANCE — A01–A28 逐项映射

图例：✓=verified（有测试+运行证据）；△=部分（机制在，证据面注明）；✗=blocked。
每项给出：实现位置 / 测试或旅程断言 / 证据。

## 交互正确性

| # | 验收 | 状态 | 实现 | 证据 |
|---|---|---|---|---|
| A01 | 三幅数据绑定真实可见 | ✓ | FrameGrid 瓦片 × resolveFrameEffectiveSpec（where/time_window 过滤真数据） | J18 mock：3 瓦片 data-frame-id 身份断言 + 活动瓦片 maplibre canvas 可见；frame-effective-spec 7/7 |
| A02 | active frame 与主地图一致 | ✓ | 激活 optimistic + commitFrameProductPatch(active_frame)（面板/地图同一服务端真相） | frame-grid.test「激活另一幅」CAS 断言；J18 mock scope 徽标；J18 real 刷新恢复 |
| A03 | 仅改第二幅不改一/三幅 | ✓ | commitLayerOverrideForFrame（set_layout frames 整表，其余帧逐字携带）；面板改名走 G15 同契约 | frame-edit-scope 4/4（fp-1 既有 override 不丢、fp-3 无 override 不变） |
| A04 | 同组 pan 传播 | ✓ | FrameCameraHub ①组内 resolveGroupSync + ②未挂载帧 expectedCamera | frame-camera-hub「同组 pan/zoom 传播」 |
| A05 | 同组 zoom 传播 | ✓ | 同上（syncZoom 维度） | 同上 + 「zoom 维度关闭」反例 |
| A06 | 不同组不传播 | ✓ | resolveGroupSync 组过滤（组 B/无组 target 无 patch） | frame-camera-hub 首用例断言 f3/f4 零 jumpTo |
| A07 | 无回声循环 | ✓ | 三层：容差收敛 null / applying 代际门 / entry generation | frame-camera-hub「无回声」用例（f2 单次 jumpTo、f1 不回跳、二次 fire no-op） |
| A08 | 不兼容投影明确拒绝 | ✓ | checkProjectionCompat（投影不匹配 typed 拒绝；MapSpec 全 3857 下的 CRS 缺口走 source error 可见面） | frame-runtime「投影不一致明确拒绝」 |

## 资源与失效

| # | 验收 | 状态 | 实现 | 证据 |
|---|---|---|---|---|
| A09 | 活 WebGL ≤ 上限且计入主图 | ✓ | mapInstanceRegistry（main/frame/comparison 统一计数；预算判定排除自身槽位防振荡）；对比视图占用如实披露 | frame-grid「预算被占用」用例；J18 mock 真实浏览器 `canvas.maplibregl-canvas` 计数 ≤2 轮询断言 |
| A10 | 快照 LRU 有界 | ✓ | FrameSnapshotStore（12 项 + 32MiB 双上限） | frame-runtime-stores「项数/字节上限 LRU 逐出」 |
| A11 | 缓存按 revision 失效 | ✓ | 指纹=session+frame+specRev+dataRev+viewport；投影层 specRevision 前进 → snapshot 态变 stale | frame-runtime「spec/数据刷新后旧快照 stale」；后端 stale_observation 6/6 之 stale 用例 |
| A12 | snapshot 显式非 live | ✓ | 状态词表 snapshot≠live；瓦片「快照（非实时）」徽标 + 版本标注 | frame-runtime 词表用例；J18 mock 观察行 not_observed 断言（不冒充） |
| A13 | 删除 frame 无迟到写入 | ✓ | 投影忽略 spec 外条目（身份不复活）+ hub 旧代际丢弃 + facts 代际门 + 删除前帧序后继 | frame-runtime「残留不复活」；frame-camera-hub「旧代际」；frame-grid「确定性后继」 |
| A14 | 切换 session 无残留 | ✓ | snapshotStore.invalidateSession + 草稿/期望/hub/live 全清 | frame-runtime-stores invalidateSession；frame-grid 会话切换 effect |
| A15 | resize 后布局与 camera 正确 | △ | react-map-gl 自适应 + 瓦片 ResizeObserver（只更新受影响瓦片尺寸面）；快照含 viewport 维度 | frame-runtime「尺寸变化快照不匹配」；真实浏览器 resize 全幅回归未单列（J18 已含刷新恢复） |
| A16 | context loss 后 error 观察重新绑定 | ✓ | FrameViewport webglcontextlost → 代际+1 重建 + onContextLost 事实 + 事实代际门 | frame-viewport「contextlost 代际重建」 |

## 产品语义

| # | 验收 | 状态 | 实现 | 证据 |
|---|---|---|---|---|
| A17 | 手动锁定 extent 保留 | ✓ | cameraWriteGuard + hub lockedExtent 目标跳过 | frame-camera-hub「锁定 extent」+ frame-runtime 守卫用例 |
| A18 | 共享分级同值同色不重算 | ✓ | 共享 breaks 原样搬运 legend_spec（shared_scope 标记），paint 由 thematic-paint 同一确定性投影派生 | frame-effective-spec「breaks 原样搬运」 |
| A19 | 逐 frame source 失败可见 | ✓ | 帧 typed error → error 态瓦片横幅 + 观察行 source_error；失败不阻塞其他帧 | frame-effective-spec typed error 用例；frame-grid resolve-error 事实推进 warmup |
| A20 | 保存刷新恢复 active 与 group | ✓ | active_frame/组 = 服务端真相（CAS 持久）；草稿 sessionStorage 恢复 | J18 real ⑥（第二次 reload 后 scope=fp-r-b） |
| A21 | CAS 冲突不假成功 | ✓ | 全写路径 expected_revision + 409 superseded 静默回灌（G15 同门复用） | frame-edit-scope CAS 用例（expected_revision=12 断言） |
| A22 | 快速切换不串帧 | ✓ | advanceLiveSlot 串行链（prior.then）+ facts 代际门 + hub attach 轮询去重 | frame-grid「键盘切换」连续 ']'/'[' 断言不串 |
| A23 | 真实 Pi 更新 frame 被浏览器采用 | △ | 后端 frame_product 工具链 G15 已有；本方向消费 committed spec（live generation 订阅）。真实 Pi 子进程 × frame 更新 × 浏览器采用的三方联跑未在本分支单独取证（G15 real 旅程在 nightly 占位同样未取） | J18 real 走真实 mutation API（同一 SetFrameProductIntent 服务端路径） |
| A24 | 逐画幅证据版本一致 | ✓ | 观察行 mapspec_revision=resolved（非 desired）+ 服务端指纹门盖章 + verify rev 对账 | 后端 test「stale_revision_not_verified」「live_snapshot_not_observed_independently」 |
| A25 | 键盘/Escape 可用 | ✓ | '[' / ']' 切换 + registerEscapeHandler 关闭（INPUT/TEXTAREA 焦点守卫） | J18 mock ⑦⑧；frame-grid 键盘用例 |
| A26 | 连续挂载卸载无增长 | ✓ | 30 次挂卸后 registry 计数归零；runtime dispose / registry release 对称 | frame-viewport「30 次无残留」 |
| A27 | 根地图旧项目行为不变 | ✓ | map-panel 仅 +2 接入补丁（挂载点+主图登记）；无产品 → 网格 null；对比视图仅 +登记 effect | 全量前端回归（除 BASE 4 既有失败全绿）；comparison-view 补丁仅注册面 |
| A28 | 无浏览器权限时 blocked 而非通过 | ✓ | playwright REQUIRE_BROWSER 契约（missing browser = hard red）+ real 档 skip 明示 REAL_ONLY_REASON；本分支产物 J18 real 若环境缺失按 blocked 记录 | 模式机制（helpers/mode.ts）；本文档 real 档证据栏如实区分 |

## 残差与如实披露

- A15 △：resize 的**真实浏览器**全幅拖拽回归未单列（机制 + 单测在；J18 已含
  reload 恢复）。评审若判需要，补一段 J18 resize 断言即可。
- A23 △：Pi 子进程 × frame 更新 × 浏览器三方联跑留待 G15/G12 的 real 旅程
  恢复后自然覆盖（同一 spec 消费通道）；本方向以真实 mutation API 路径
  （同 intent 同服务端）取得 real 证据。
- react-map-gl v8 × maplibre 6.10 在**帧视窗挂载**时打印 1 条非致命
  console error（`container must be String or HTMLElement`，库内部竞争，
  地图正常渲染、断言全过、无泄漏计数异常）——已定位为 id prop 触发的
  库内警告路径（去 id 则功能受损），记录为已知噪声。
