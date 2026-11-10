# REVIEW — 独立评审 → 修复 → 复审（G20）

## 第一轮（独立冷读，2026-11-10）

Reviewer：独立 code-explorer subagent（未参与本轮任何实现；在此之前只用过一次
只读勘察 subagent，全任务累计 2 个 subagents，未突破 3 个上限）。
输入：BASE_SHA `816705218a5a…`、HEAD `c3e403d29`、完整 diff、本地测试证据、
用户场景与 11 项审查清单。完整原始评审见本文件下方「原始结论摘要」。

结论：**不建议交付**，6×P1 + 8×P2 + 5×P3；无 P0。逐项核对结果（原文）：
1. 调用链真实性 — 部分 OK（UI/Pi 同一 service、单一 MapSpec 写入面成立；提交多 checkpoint 分裂、Pi 回执无 mapspec 回灌）
2. 租户/会话/权限 — OK（7 个端点全有 owner 守卫；trusted_dispatch 被 registry 双向封死；URL 无凭证）；附 P1-4
3. 科学与单位语义 — 部分 OK（角色/单位/分类/0 值/mask/常值/跨瓦片全局 stretch 均 OK）；附 P1-1
4. 数据/版本一致性 — 部分 OK（style_id、双层隔离、旧 URL 409 均 OK）；附 P1-3、P2-2
5. 取消/异常/重试 — OK（probe seq、失败不伪装透明 PNG、put_if_current、预算）
6. 内存与计算有界 — 部分 OK；附 P1-5
7. 用户可用性 — 部分 OK（四态/错误聚焦/键盘/逐通道输入 OK，无 hook 循环）；附 P1-1/2/6、P2-4/6
8. Pi 真实入口 — OK（4 工具真实注册、参数模型/元数据/conformance 无结构性 fail）；附 P2-3
9. 旧协议兼容 — OK（style=None 逐字等价、旧 imageRef/raster-opacity 保留、生成物一致、1.9 无需迁移）
10. 与 PR 重复/冲突 — 无不可解决冲突（#2032 无文本冲突；#2022 仅共享 main.py/tools/__init__.py 的追加行）
11. 额外 — PDF 渲染前拒绝、未消费字段

### P1 原始发现 → 修复（全部已改并补回归）

| # | 发现（file:line） | 修复 | 回归 |
|---|---|---|---|
| P1-1 | `contracts.ts:193-205` 把后端 `defaults`（请求体 `stretch:"auto"`）当草稿展开，`autoStretch` undefined → 近似 2048 边统计被提交成 `manual/approximate=false`；UI 与 Pi 语义分叉 | `RasterDescriptor.defaults` 改为请求体类型；`defaultDraft` 显式 `autoStretch: true`；`draftFromCommitted` 按 `stretch.source` 归一 | `test_auto_stretch_is_approximate_and_pi_ui_identical`、`contracts.test.ts::review P1-1` |
| P1-2 | `contracts.ts:286-302` `isDraftDirty` 漏判 `perChannel`/`autoStretch` → RGB 逐通道拉伸与 auto→manual 切换无法提交（按钮恒灰） | dirty 判定纳入 autoStretch + 期望逐通道端点；RGB 全局端点由逐通道派生，避免死控件 | `contracts.test.ts::review P1-2 / P2-4` |
| P1-3 | `service.py:226-267` 一次提交拆成 2–3 笔 `apply_gis_mutation` → 撤销栈污染、失败残留半状态、错误回执谎称 nothing written | 新增 `mapspec_source.store_data` 的 `numeric_raster` typed passthrough；改为**单笔** `UpsertLayerIntent(source_data=entry)`（引擎 auto-init 骨架补 version）；`_apply_result_error` 改 `MUTATION_REJECTED`/422 | `test_single_mutation_first_mount_creates_versioned_spec_and_source`、`test_concurrent_commit_rejection_writes_nothing`；所有既有提交/测试适配 `mutations==1` |
| P1-4 | 登录用户导出后前端 `window.open` 不带 Bearer → `download_map_export` 401，功能假成功 | `delivery.persist_snapshot_artifact` **恒**签发 HMAC 能力 URL（与既有匿名路径同款） | `test_export_download_is_always_a_signed_capability` |
| P1-5 | 每个瓦片请求 `resolve_source` 重跑 `inspect_raster_artifact` 全波段 2048² 读（高光谱可 OOM） | 有界描述子 `_inspect_bounded`（≤16 波段、≤1024 边、截断披露）、`_read_band_meta` ≤64 波段、`_open` 统一 GDAL env、`_SOURCE_CACHE` 按 (session,ref,path,mtime,size) 5s TTL | `test_source_resolution_is_cached_and_band_bounded`、`test_descriptor_discloses_approximate_and_truncation` |
| P1-6 | UI probe 固定只查显示波段，拿不到「各波段原始值」；与 Pi 全波段语义分叉 | UI 默认请求全部波段（≤16）并提供多选控件；`runProbe` 传 `probeBands` 而非 `draft.bands` | 前端 `contracts.test.ts` 语义对齐 + HTTP 旅程 `test_descriptor_probe_preview_and_explicit_error_codes` |

### P2 处理

| # | 发现 | 处置 |
|---|---|---|
| P2-1 | `"grey"` 别名绕过 LUT → 提交成功但瓦片 502 | 已修：`canonical_colormap` 别名先归一 + 提交期 `colormap_lut(...,8)` 校验。回归 `test_colormap_aliases_resolve_to_a_real_lut` |
| P2-2 | `frozen_layers` 多次独立读 MapSpec，导出可能混用两修订 | 已修：一次读取冻结快照，`resolve_committed_expression` 接受传入 mapspec，末尾复核 revision；新增 `CAS_SUPERSEDED` 拒绝 |
| P2-3 | Pi 回执剥离 `mapspec` 与 UI 即时 reconcile 不同 | **有界上下文约束**，不把完整 MapSpec 塞进 LLM 回执；Pi 提交由既有 `apply_gis_mutation` 的 MapSpec 事件/SSE 通道 reconcile（与所有 agent 写工具同语义）。工具回执新增 `reconcile_via:"mapspec_event"` + `mapspec_revision` 明确披露；PR 列为已验证边界。 |
| P2-4 | RGB 全局滑块是死控件且白改 style_id | 已修：`buildExpressionRequest` 从逐通道派生全局端点，面板在 RGB 手动模式隐藏全局输入 |
| P2-5 | 导出位图固定 nearest 与屏幕重采样不一致 | 已修：`render_snapshot` 按 `spec.resampling` 选枚举 |
| P2-6 | 无 raster/tile HUD 图层时无首次挂载入口 | 已修：新增 `RasterAuthoringLauncher`，在 `context-panel.tsx` 的 layers 分支做 1 行局部挂载（不改布局骨架；PR 登记 #2032 冲突段） |
| P2-7 | 导出只画数值栅格层，矢量/底图被省略 | 保持披露（sidecar `omitted_layers` + `limits`），不伪称整图；PR「未测限制」如实说明。复用完整出版渲染链属 G15/出版域，本任务不接管。 |
| P2-8 | 与 #2022 共享 `main.py`/`tools/__init__.py`；openapi 快照吸收 #2035 漂移 | 合并前重新 fetch 校正；PR 明确列出冲突段与快照刷新原因（additive，含基线漂移 #2007） |

### P3 处理

- 截断披露恒 False → 已修（`requested` 用原始边，`used` 用降采样边）。
- SVG `<desc>` 未转义 `&` → 已修（`html.escape`）。
- MapSpec 校验失败被映射成 502 → 已修（`MUTATION_REJECTED`/422）。
- 坏 scale/offset tag 让整源不可用 → 已修（`_numeric_flag` 容错）。
- 卸载未 abort probe → 已修（`useEffect` cleanup）。
- 裸 `rasterio.open` → 描述子/元数据/dataset tag 统一走 `_open`（`rasterio_env` + `open_raster`）；`descriptor.py`/`probe.py`/`render.py` 仍为纯本地（已 scope 校验的）窗口读，属于与既有 `raster_tile_service` 不同的入口，登记为后续收敛项。
- `CommittedExpression.mapspec_revision` 未消费 → 保留作为导出冻结证据字段（现由 `frozen_layers` 显式传入）。
- PDF 工具在渲染前拒绝 → 已修。

## 复审（待）

第二轮将由同一独立 reviewer 冷读修复后的完整 diff 与测试证据，重点复核：
P1-1..6 是否真闭环、单笔事务是否真的不残留半状态、`store_data` typed
passthrough 是否影响其它源类型、有界缓存是否会在数据修订变化后串用、
UI 默认自动拉伸与逐通道提交是否可达、能力 URL 是否仍受属主校验。

## 基线既有失败（隔离证据，非本任务引入）

- `tests/unit/gis_tool_conformance/test_live_registry_audit.py::test_live_registry_completeness_ratchet`：
  `binding_unbacked 66 > ceiling 64`。在主工作区（BASE_SHA 未改动的同名文件）
  运行 `collect_live_facts(bootstrap=True)` 得到 `tool_count=400`、
  `binding_unbacked=66`，与本分支 `tool_count=404`、`binding_unbacked=66`
  完全一致 → master 基线即失败。本分支四个新工具均为 `planner:
  no_capability_binding`（not_evaluated），未增加任何债务；`metadata_incomplete`
  在补 `network=False`/`produced_refs` 后回到基线 158，`STATUS_FAIL` 从
  （初版误报 4 个）恢复为 0。
- 开放 issue `#2007` 已记录 master 上 OpenAPI 快照过期（#2035 的
  `UndoAgentCommitBody` 未刷新）；本任务按仓库既定 `API_SNAPSHOT_UPDATE=1`
  流程刷新，顺带吸收该基线漂移，PR 明确说明。
