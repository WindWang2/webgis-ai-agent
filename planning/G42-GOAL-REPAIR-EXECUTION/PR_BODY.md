# PR：feat(g42) 有界目标补全执行 —— goal_repair_plan 从披露接成完整用户闭环

## 用户问题

G28 终验已生成 `goal_repair_plan`（规则→有限修复动作），但它只作为披露持久化在
map_product 块里：用户在任务完成卡上看到「partial / 缺 export 回执」却没有入口去
补全；Pi 也只能口头建议。同时存在四个真实缺口，使「数据成果交付」类需求在生产上
**永远无法满足**：

1. `receipts._collect_delivery` 的 `Query[:N].all()`（SQLAlchemy 2.x 切片即执行，
   返回 list）抛 `AttributeError` 且被外层吞掉 → **交付回执族恒为空**；
2. evaluator 的导出/交付回执缺席分支不设 `failed_rule` → repair_plan_for 对
   `export_receipt_absent` / `delivery_receipt_absent` **从不生成修复动作**；
3. `webgis_map_export` 工具路径缺 db/owner 上下文 → record_export_lineage
   fail-closed 跳过 → **经 agent 的地图导出从不产生 goal 可见证据**（UI REST 路径有）；
4. Pi 直调派发的 JobOrigin 不携带 owner 身份 → 需要登录身份的下游服务（如交付
   预览）在 agent 链上被拒。

## 行为增量（本 PR）

- **预览（零副作用）**：`GET /api/v1/goal-repair/sessions/{sid}/preview` 与
  `webgis_goal_repair_preview` —— 从存储块服务端重建绑定矩阵：每个计划动作获得
  executable / needs_acknowledgment（G18 只读预览披露损失确认词表）/
  needs_user_input / unsupported / blocked 裁决 + 真实执行入口与服务端派生参数源
  （客户端不可自报 owner/tool/参数/权限）。
- **启动（幂等单 claim、零等待）**：attempt = durable job 行
  （`goal_repair:{session}:{goal}:{plan_version}` 幂等键；显式重试=新 attempt 带父链）。
  Pi 回调与 UI 均立即返回；执行体独立 asyncio 任务（run_async_job 同族），逐动作经
  统一 ToolDispatchService 派发既有生产工具 —— 不新建 agent loop/调度器/事实源，
  不嵌套持 wave 槽（TOOL_WAVE_*_CONCURRENCY=1 下真实验证，R37），不递归 prompt、
  不另持 `pi-writer`。
- **动作面（复用既有服务）**：地图导出（webgis_map_export，blocking 保留+CAS 修订）；
  数据交付（动作时 preview 复检 → 损失确认闸 → submit_data_delivery → 有界等待
  job；未确认=零提交）；repair_cartography（run_runtime_repair，预算内部自理不双扣）；
  reobserve（有界等待当前修订的真实浏览器新观察，超时=waiting_observation 披露，
  绝不伪造 verified）；stale_reread/requalify 只读重读；resolve_data/retry_step/
  rerun_complete typed blocked（无完整实参/幂等保证不自动重放）。
- **原终验对账（单一真相源）**：动作收口后经既有
  `maybe_finalize_map_product(final_gate=True)` 重新裁决；attempt 只记录 verdict
  before/after。goal 指纹换代 → 后续动作 stale、attempt superseded。
- **取消/恢复/对账**：两阶段协作取消（等待期也响应 checkpoint）；取消后已积累回执
  保留可回看；进程崩溃后读路径惰性对账（waiting_delivery 收口 / runner 丢失 →
  outcome_unknown，不盲重放）；同计划+同证据签名重复执行 → typed NO_PROGRESS。
- **UI**：完成卡（非 satisfied）下挂载 GoalRepairPanel —— 预览动作与裁决、损失确认
  勾选（未勾全不启动）、启动/取消、逐动作回执与真实下载链、终验前后 verdict 对照；
  旧消息可恢复历史 attempt；重观察经加性 window 事件触发 map-panel 既有 reconcile。
- **附带修复（既有缺口，均为本域必要）**：上述 4 个生产缺口 + registry 交付工具
  `spec` 键补 ref 透明解引用 skip 名单（G30/G38 同款；否则经派发的 DeliverySpec 被
  整份展开）+ aiosqlite connect_args 补 busy timeout 30s（与同步侧 V7 对称）。

## 复用与范围

- 只加薄接线：`tools/__init__.py`、`main.py`、chat-tab 挂载、map-panel 事件监听（2 行）、
  i18n（双语 parity）。独占主体在 `goal_repair_execution/` 新模块。
- 不改 G43（#2126 开放中）任何数据结构；G35/#2125 无交集；迁移零新增（attempt 复用
  AnalysisTask）。
- Pi pin 保持 v1.5.2（= upstream latest，无升级收益）。

## 实际验证（全部真实执行；不等待线上 CI）

- 后端单测 25 passed（`tests/unit/gis_harness/goal_repair_execution/`）：绑定矩阵/
  单 claim/显式重试父链/no_progress/busy/篡改拒绝/取消竞态/supersede/真实交付闭环
  （shapefile_zip 损失确认词表 + 文件回读 + **重终验 partial→satisfied 断言**）/
  真实地图导出闭环（G20 提交链→统一派发→真实 PDF 文件+lineage 入章节）。
- 受影响既有回归：goal_satisfaction/repair_actions/finalizer/wiring 195 passed；
  tool_registry+skill_jobs 20 passed；delivery 56 passed。
- **真实 Pi 旅程**（PI_JOURNEY_E2E=1，TOOL_WAVE_*_CONCURRENCY=1）passed：
  登录会话 → turn1 文本收口触发**真实终验产出真实修复计划** → turn2 stub LLM 经
  tool_search 两跳发现并调 preview → turn3 start **零等待返回**（turn 正常 settle，
  无自等待）→ HTTP 轮询 attempt → 统一派发 submit_data_delivery → G18 真实 durable
  job + shapefile_zip 落盘 → 回执服务端核验（rows=2）→ 重终验 **partial→satisfied**，
  export:gpkg 需求 fulfilled。
- 浏览器：j42 2 passed（面板全交互 + 无计划收敛）；j17 回归 passed；前端单测 62
  passed；i18n parity 通过。
- typecheck：新增/修改文件零错误（仅剩 2 个 #2123 基线错误于 cartography-result-card.tsx，
  本任务未触碰）；Ruff：本域文件 clean（receipts.py 2 个告警为基线遗留，`git show` 证实）。

## 已知边界

- 交付等待上限 120s（超时=waiting_delivery + 读路径惰性对账收口并重终验）；
  reobserve 等待 45s（浏览器离线=waiting_observation，不伪造证据）。
- venv 环境下 `tests/delivery/test_service_flow.py::test_fabric_parquet_fast_path`
  失败（pyarrow 版本差异使 materialize 返回缺 `path` 键）——不在本任务调用图，主
  仓 CI 栈未复现口径未变。
- attempt 恒等键极端竞态（同键终态复用窗口）下，后到请求会看到前一次终态视图而非
  新建行 —— 再点重试即新建（幂等语义保持，无双执行）。

## Review / Fix / Rereview

- 独立 review（未参与实现的 reviewer，base..工作树全量 diff）：见
  `planning/G42-GOAL-REPAIR-EXECUTION/REVIEW.md`。
- （占位：评审结论与修复记录在交付前填充。）

## 迁移/回退

- 零迁移（无新表/列）；回退 = revert 单个 merge commit，既有 goal_repair_plan 披露
  面与 G28 行为不变。
