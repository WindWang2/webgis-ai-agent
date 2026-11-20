# G42 PLAN — 有界目标补全执行

## 模块布局（独占主体）

```
app/services/gis_harness/goal_repair_execution/
  __init__.py       # 公开面
  contracts.py      # typed 错误码 / 绑定与 attempt 视图
  bindings.py       # WP01 动作可执行矩阵（plan + 活状态 → 绑定裁决）
  preview.py        # WP02 版本化补全预览（零副作用）
  store.py          # WP03 attempt 持久化（AnalysisTask 行 + 读模型 + 恢复对账）
  runner.py         # WP04/07/08/09/11 顺序执行 + 前置复核 + 预算 + 取消 + 重终验
  actions.py        # WP05/06 每动作执行体（统一派发 / 既有服务）
app/api/routes/goal_repair.py      # UI API（auth: require_owned_session）
app/tools/goal_repair_tools.py     # Pi 工具 webgis_goal_repair_preview/start/status/cancel
frontend/lib/types/goal-repair.ts  # 类型 + 解析（非法→null）
frontend/lib/api/goal-repair.ts    # apiFetch 客户端
frontend/components/chat/goal-repair-panel.tsx  # 完成卡下的交互面板
messages zh-CN/en-US chat.json     # chat.goalRepair.*（双语 parity）
```

共享薄接线（加性）：`app/tools/__init__.py`（注册）、`app/main.py`（router）、
`chat-tab.tsx`（挂载面板）、`goal-satisfaction-card.tsx`（不动，面板独立挂载）。

## 关键语义

- **attempt = AnalysisTask 行**：task_type `goal_repair_attempt`、kind `goal_repair`、
  幂等键 `goal_repair:{session}:{goal_id}:{plan_version}`（单 claim）；显式重试 =
  新 attempt（键加 `:r{n}`，parameters.parent_attempt 保留父链）。
- **start 零等待**：Pi 回调与 UI 均立即返回；执行体独立 asyncio 任务（G30 同款
  run_async_job），外层不持 wave 槽等内层（R37/R38）。
- **动作执行面**：
  - export（pdf/png/svg）→ 派发 `webgis_map_export`（blocking 默认保留；expected_revision=CAS）；
  - run_delivery（fmt ∈ DATA_DELIVERY_FORMATS）→ 服务端从章节行 bound_ref 派生 DeliverySpec →
    动作时 preview 复检 → `submit_data_delivery`（required_acknowledgments 缺席 = typed 阻断，零提交）；
  - repair_cartography → `run_runtime_repair`（内部已扣预算，adapter 不双扣）；
  - reobserve → 有界等待前端新观察（同 revision 新 seq；R20 超时=waiting/blocked 披露）；
  - stale_reread / requalify / resolve_data → 只读重读/重判/分类，typed 结果；
  - retry_step / rerun_complete → 仅当原计划行工具 `deterministic=True` 且输入 ref 在案，否则 typed blocked。
- **重终验**：动作收口后 `maybe_finalize_map_product(reason="goal_repair_attempt", final_gate=True)`
  → 同一 evaluator / 同一持久化路径；attempt 只记录 verdict before/after（不写第二份 report）。
- **supersede/漂移**：goal_key 与 mapspec revision 快照在 preview 落定；动作前复核（R12/R13）；
  export CAS superseded → attempt stale。
- **取消**：两阶段（cancel_requested_at + 步间检查点）；已产生产物如实披露（R28）；
  进程崩溃后按幂等键/章节回执对账，不可确认 → outcome_unknown（R26）。
- **no_progress**：同 plan 指纹 + 同证据签名 + 无新事实 → typed NO_PROGRESS 拒绝新 attempt（R14）。

## 测试布局

- 单元：`tests/unit/gis_harness/goal_repair_execution/`（绑定矩阵/预览/store 竞态/runner 语义/取消/预算/no_progress）
- 集成（真实服务，无 Pi）：attempt → 动作 → 重终验 全链（isolated DB fixture）
- Pi 旅程：`tests/integration/test_pi_g42_goal_repair_journey.py`（stub LLM + 生产扩展 + 真实导出/交付 + 重终验）
- 浏览器：playwright journey（面板交互 + SSE）+ 重资源锁纪律
