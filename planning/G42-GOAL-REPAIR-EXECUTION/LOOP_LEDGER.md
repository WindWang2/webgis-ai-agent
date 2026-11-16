# G42 LOOP_LEDGER — 执行账本

## 轮次记录

### L1 研究与账本（base 655f769ca）
- fetch 最新 master；#2117–#2122/#2124 评审通道 21 个全读（仅 #2120 一条复审记录）；开放 PR=0、issue=#2123（基线，非本域）。
- Pi pin v1.5.2 = upstream latest → 保持。
- Explore agent（3 subagent 槽位 #1）完成 12 接缝测绘。

### L2 实现（独占模块 + 加性接线）
- `app/services/gis_harness/goal_repair_execution/`：contracts/bindings/preview(service)/store/actions/runner/service。
- `app/api/routes/goal_repair.py`、`app/tools/goal_repair_tools.py`、`app/tools/__init__.py`、`app/main.py` 接线。
- 前端：types/api/panel + chat-tab 挂载 + map-panel 重观察事件 + i18n zh/en。
- 测试：单测 4 文件 + Pi 旅程 + Playwright j42。

### L3 验证与修复（发现并修复 5 个真实缺陷）
1. **JSON 列变更检测**：`write_summary_patch`/`_mark_runner_lost` 同对象回赋不落库 → 新 dict 赋值（no_progress 对账会静默丢失）。
2. **路由归属守卫**：`_owned_session` 把 user dict 传给 `verify_session_owner`（期望 user_id 字符串）→ 恒 404（旅程抓出）。
3. **Pi 直调 owner 身份缺失**：桥接 JobOrigin 不带 owner_id（既有缺口）→ `start_repair_attempt` 服务端从 Conversation 行派生权威身份。
4. **交付回执族从不收集（既有生产 bug）**：`receipts._collect_delivery` 的 `Query[:N].all()` 在 SQLAlchemy 2.x 抛 AttributeError 被吞 → delivery 证据面恒空（G28 遗留）。修为 `limit(N).all()`。
5. **evaluator 缺席分支无 failed_rule（既有缺口）**：export/delivery 回执缺席只填 missing → repair_plan_for 永不生成该类动作（G42 无从消费）。补设 `failed_rule=R_EXPORT_ABSENT`（与 _RULE_ACTIONS 键同名，本就为映射设计）。

另修复（本域必要、加性）：
- `registry` ref 透明解引用 skip 名单补 `preview/submit_data_delivery` 的 `spec`（G30/G38 同款；否则经派发的交付规格被整份展开）。
- `mixed_map_export_tools` 工具路径补 db+owner 上下文（UI REST 路径一直有 lineage；工具路径缺席 → agent 导出从不产生 goal 可见证据）。
- `database.py` aiosqlite connect_args 补 busy timeout=30（与同步侧 V7 对称；双引擎并发写下异步侧 5s 即抛 database is locked）。
- runner：交付/观察等待期传 `handle.checkpoint`（协作取消及时生效）；取消捕获补 `OperationCancelled`（回执保留）。

### L4 验证结果（全部真实执行）
- 后端单测：`tests/unit/gis_harness/goal_repair_execution/` 25 passed（R02/R05/R06/R07/R09/R11/R12/R14/R16/R24/R26/R28/R29 覆盖）。
- 受影响既有套件：goal_satisfaction/repair_actions/finalizer/wiring 195 passed；tool_registry + skill_jobs 20 passed；delivery 56 passed（1 个 pyarrow 版本环境失败 `test_fabric_parquet_fast_path`——materialization 返回缺 path 键，不在本任务调用图，venv 环境差异）。
- Pi 旅程（PI_JOURNEY_E2E=1，TOOL_WAVE_*_CONCURRENCY=1）：`test_pi_g42_goal_repair_journey` passed —— 真实终验产计划 → tool_search 发现 → preview 回执 → start 零等待（R37 无嵌套持槽）→ 统一派发真实交付（2 行 shapefile_zip）→ 交付文件回读核验 → 重终验 partial→satisfied（R02/R15/R31/R32/R35）。
- 浏览器：j42 2 passed（面板交互/损失确认闸/回执下载/终验对照/无计划收敛）+ j17 回归 passed。
- 前端单测 62 passed（types/i18n/chat reducer）；typecheck 仅剩 2 个基线错误（cartography-result-card.tsx，#2123，非本任务文件）。
- Ruff：新增/修改文件 clean（receipts.py 的 2 个告警为基线遗留，git show HEAD 证实）。

## 待办
- 独立 review → 修复 → 复审。
- fetch 最新 master 合流 + 受影响回归 + commit/push/PR。

## 资源纪律记录
- 重阶段（vendor build / Pi 旅程 / Playwright / pytest 全量）均经 `webgis-heavy-stage.lock` 串行；pytest 单进程；playwright --workers=1；vitest 默认。
