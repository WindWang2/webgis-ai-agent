# G42 DEDUP MATRIX — 对最新 master（655f769ca）逐项判定

判定词：present（已存在，转兼容回归）/ partial / missing（本任务实现）/ owned-by-other / blocked。

| 能力项 | 判定 | 生产调用链证据 |
|---|---|---|
| goal_repair_plan 生成（规则→动作映射、≤6、blocker 优先） | **present** | `completion/pipeline.py:982-987` 写块；`repair_actions.py:123-194` 纯映射；全仓无执行消费者（grep 证实） |
| 修复计划执行接缝（preview/start/status/cancel） | **missing** | 无任何 API route / @tool / 前端入口消费 `goal_repair_plan` |
| attempt 持久化 + 单 claim | **missing**（复用 present 的 durable job 基础设施） | `AnalysisTask` 表 + `DurableJobStore.create`（幂等键唯一）+ `submit_async_job`（`data_operations/runner.py:375`） |
| Pi 工具回调内启动执行（绑定 turn、无递归 prompt、无第二 writer） | **missing**（模式 present：G30 `gis_skill_job_start` 绑定 `current_runtime_context().turn_id`） | `app/tools/skill_job_tools.py:131-197`；本任务 start **不等待**（G30 的 45s 等待在 session 并发=1 下有自等待风险，不照搬） |
| UI 完成卡交互（启动/观察/取消/恢复/下载） | **missing**（卡片 present 但只读） | `goal-satisfaction-card.tsx` 无交互；`chat-tab.tsx:405` 只挂载 |
| 真实地图导出动作 | **present**（工具面） | `webgis_map_export`（`mixed_map_export_tools.py:103`）→ `export_mixed_map`+`persist_mixed_export` → `record_export_lineage` 写 `gis_chapter["export_receipts"]` |
| 真实数据交付动作 | **present**（工具面） | `submit_data_delivery`（`data_delivery_tools.py:112`）→ `submit_delivery`（损失确认闸 + durable job）→ `_collect_delivery` 进证据 |
| cartography 修复动作 | **present** | `run_runtime_repair`（`runtime_repair.py:426`，自带预算/CAS/轮数账本；chat.py:2261 唯一生产调用点） |
| 观察重取（reobserve） | **missing**（通道 present：前端 `use-cartographic-observation` 每次 reconcile POST） | `render_observation.py`；等待新 seq 属新接缝 |
| 终验重触发 | **present**（入口存在，无 goal-repair 消费者） | `maybe_finalize_map_product(final_gate=True)`；先例 `visual_repairs.py:476`、`chat.py:2183` |
| 预算闸 | **present** | `loop_budget.loop_retry_admissible/loop_charge`；`ACTION_BUDGET_CLASS` 已映射 |
| G28 evaluator / 证据 / 需求派生 | **present**（不重做、不放宽） | `evaluator.py:854`、`evidence.py`、`requirements.py:143-153` |
| G22 恢复 / G37 budget / G36 images / G29 交互 | present（他域，仅兼容） | — |
| G40 符号资产 | present（#2124 已合并，只按其真实接口消费，不重新认领） | — |
| G43 context receipt | **owned-by-other**（未合入；本任务不依赖、不改其数据结构） | — |
| 迁移需求 | **none**（attempt 复用 AnalysisTask 行，无新表/列） | 最新迁移头在 #2122 合并后由 base 决定；本任务零迁移 |

## 结论

本领域全部用户闭环（计划→授权→执行→证据→重终验→UI）为 missing，其余支撑面均 present 且必须复用。无重复实现风险；主要冲突面是 `app/tools/__init__.py`、`app/main.py`、`frontend/messages/*/chat.json`、`chat-tab.tsx` 的加性接线。
