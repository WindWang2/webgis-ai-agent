# G42 BASELINE — 有界目标补全执行

## 执行环境

- 主工作区：`/home/kevin/projects/webgis/webgis-ai-agent`（HEAD 816705218，本地 master 落后远端 380 commits，只读）
- 本 worktree：`/home/kevin/projects/webgis/webgis-worktrees/g42-goal-repair-execution-20261115T124804Z`
- 分支：`zcode/g42-goal-repair-execution-20261115T124804Z`
- **base SHA：`655f769ca17f17c5797da13b4c299b8fa600e370`**（= fetch 后 origin/master）
- 与研究快照差异：研究基线 34f4e3670 之后 **#2124（G40 符号与纹理资产）已合并**；#2116–#2122、#2124 全部在 base 内。当前**无开放 PR**。
- Pi pin：`vendor/pi` gitlink = `b4afdddf44e059c67ccf5097e5317d913521b470` = upstream latest stable **v1.5.2**（2026-11-10 发布）。**无需升级，保持 pin**。

## PR / review 通道核查（2026-11-15 实测）

- #2117/#2118/#2119/#2121/#2122/#2124：review submissions 空、inline review comments 空、conversation 空。
- #2120：无 review submissions / inline；**1 条 conversation = 独立复审第 2 轮记录**（P0=0，5 P1 已修）。
- 开放 issue 仅 **#2123**（旧 master 的 CI 基线失败报告：48 后端 / 16 前端 / 124 Ruff / 9 Bandit）——审计质量门，非本任务验收目标；不清理全仓，但本任务新增代码必须不引入新违规。
- G42 无前序任务占用：G09/G21 无 PR 不重认领；goal repair 执行接缝无任何现有 PR/分支占用。

## 已核实生产事实（本任务消费面）

| 事实 | 位置（base 655f769ca） |
|---|---|
| `goal_repair_plan` 由 finalizer 持久化在 map_product 块，**无任何执行消费者**（全仓 grep 证实仅 pipeline.py 写入） | `app/services/gis_harness/completion/pipeline.py:982-987` |
| 动作词表 10 个（reobserve/stale_reread/requalify/resolve_data/repair_cartography/reexport/export_product/run_delivery/retry_step/rerun_complete），`ACTION_REGISTRY` 有 path 但只是披露字符串 | `app/services/gis_harness/goal_satisfaction/repair_actions.py:24-67` |
| 硬 blocker 优先、≤6 动作、新证据规则 `new_evidence_required_per_round` | 同上 `:105-120,155-194` |
| 完成卡只读展示 verdict/missing/next_action，无交互 | `frontend/components/chat/goal-satisfaction-card.tsx` |
| SSE 载荷投影（只读存储块，不重算） | `app/services/gis_harness/goal_satisfaction/projection.py:bounded_payload` |
| 原终验重触发入口：`maybe_finalize_map_product(session_id, reason, final_gate=True)`——非 READY 会话绕过去重门重验；revision/行指纹/观察代次变化自然破门 | `app/services/gis_harness/completion/pipeline.py:1124-1189` |
| settle 收口统一入口 `run_settle_effects`（E1 final gate → workflow → state → checkpoint → disclosure） | `app/services/harness_kernel/settle.py:340` |
| runtime_repair 有预算门（loop_retry_admissible/loop_charge）+ CAS + 轮数 ledger，**adapter 不得双扣** | `app/services/gis_harness/runtime_repair.py:426+,523-560` |
| 统一派发 `ToolDispatchService.dispatch(tc, session_id, executed_tools)`；session gate 先于全局 wave 槽 | `app/services/tool_dispatch_service.py:348-362,1046-1136` |
| G30 生命周期参考：durable job + 幂等键 + `run_async_job` + owner(turn/tool_call) 绑定 + busy 语义 + 有界等待 | `app/services/gis_harness/skill_jobs/runner.py` |
| durable job 基础设施 `submit_async_job(task_type, params, idempotency_key, kind, owner_id, owner_token, org_id, session_id, project_id)`（AnalysisTask 行） | `app/services/data_operations/runner.py:375` |
| 地图导出真实工具：`webgis_map_export`（pdf/svg/png；blocking 默认；expected_revision CAS）→ export_mixed_map + persist_mixed_export | `app/tools/mixed_map_export_tools.py:63-153` |
| 导出回执写 `gis_chapter["export_receipts"]`（record_export_lineage），evaluator 从章节读 | `app/services/export_lineage.py:231`、`goal_satisfaction/evidence.py:242-267` |
| 数据交付工具族：`delivery_capabilities` / `preview_data_delivery` / `submit_data_delivery` / `get_data_delivery`（损失确认必须覆盖 preview.required_acknowledgments） | `app/tools/data_delivery_tools.py`、`data_delivery/service.py:232-304` |
| 交付回执经 `_collect_delivery`（按 session 过滤 + get_delivery_job 授权）进证据 | `goal_satisfaction/receipts.py` |
| 需求派生：`export:<fmt>`（intent.export_intents）；`delivery_expected = fmt ∈ DATA_DELIVERY_FORMATS` | `goal_satisfaction/requirements.py:143-153` |
| 观察通道：前端 POST `/sessions/{sid}/cartographic-observation` → map_state `_cartographic_observation`（latest-wins，服务端盖章 revision） | `app/services/gis_harness/render_observation.py:1-40` |
| 能背书 fulfilled 的只有 deterministic/structural 证据 | `goal_satisfaction/contracts.py:86-97` |
| writer gate：`pi-writer:{session}` 单写者；G30 回调内不重入 writer | `app/services/chat/pi_writer_gate.py` |

## 关键设计决定（依 prompt §三/§四）

1. **start 工具零等待**：Pi 回调内 `webgis_goal_repair_start` 只绑定 attempt（durable job 幂等提交）并立即返回有限计划视图；动作执行体是独立 asyncio 任务（G30 runner 同款），不嵌套持 wave 槽（R37）。
2. **attempt = durable job 行**（kind="goal_repair"）：幂等键 `goal_repair:{session}:{goal_id}:{plan_version}` 兑现单 claim；不新建 DB 表、不新建调度引擎。
3. **复用原终验**：attempt 收口调用 `maybe_finalize_map_product(reason="goal_repair_attempt", final_gate=True)`——同一 evaluator、同一持久化路径，不写第二份 goal report。
4. **delivery_expected 修正绑定**：`export:<fmt>` 需求在 fmt ∈ DATA_DELIVERY_FORMATS 时执行面绑 `run_delivery`（submit_data_delivery），否则绑地图导出（webgis_map_export）；预览期用 `preview_data_delivery`（只读）披露 required acknowledgments，未确认零提交。
5. reobserve 不伪造：attempt 置 waiting_observation，等前端真实 POST 新观察（同 revision 新 seq），超时有界 → blocked/waiting 披露。
