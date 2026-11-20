# G43 BASELINE — 本轮实际上下文回执与可控项目资料工作台

- worktree: `/home/kevin/projects/webgis/webgis-worktrees/g43-turn-context-control-20261115T124713Z`
- branch: `zcode/g43-turn-context-control-20261115T124713Z`
- base SHA: `655f769ca17f17c5797da13b4c299b8fa600e370`（master，含已合并 G40 #2124）
- 研究快照基线 `34f4e3670` 已被超越：G40 #2124 已合并（资产工作台），按已合并接口对待，不重新认领。
- 开放 PR：无。开放 issue：#2123（CI 基线报告——非本任务目标，作为质量参考）。
- Pi pin：`earendil-works/pi` v1.5.2 / `b4afdddf44e059c67ccf5097e5317d913521b470`（G22 已对齐；本任务不改 vendor）。
- 迁移头：单一 head `g40_cartographic_symbol_assets`（#2122 将 0107/g36/g3701 合为 `bcf4e5b51f43`，G40 叠加其上）。
  本任务新迁移 down_revision = `g40_cartographic_symbol_assets`（运行时再核）。
- ID 保留已登记：`$(git-common-dir)/webgis-id-reservation.lock`（migration g43_turn_context_receipts、ADR-0295、G43-J*、g43_ 测试前缀）。

## 已核关键事实（对照任务书 §二）

| 事实 | 现状核对 |
|---|---|
| `bind_turn_prompt` 丢弃 receipt | 属实（pi_turn_context.py:282 `assembled, _receipt` 后只返回字符串） |
| 无生产 receipt 持久化 | 属实：全仓仅 context_assembly 三文件引用 receipt；无 API/UI 消费者 |
| `actual_tokens` 是渲染估算 | 属实（estimate_message_tokens，同一 estimator） |
| knowledge:card 单 block 粒度 | 属实（ProjectKnowledgeCard 只有 items/truncated/omitted 计数，无条目 ids） |
| 记忆/知识 builder 独立 source_scope | 属实（ScopedText.source_scope，project_scoped） |
| 知识卡读取提交 demotion | 属实（load_project_knowledge_card → demote_changed_entries + db.commit） |
| `ensure_session_plan_slot` 有副作用 | 属实（fetch_shared_facts 内创建 plan slot） |
| G37 模型/预算门在装配后 | 属实（bridge `_apply_session_model_for_turn` 在 bind 之后、RPC 之前） |
| disposition handled/queued/started | 属实（bridge 3477 处仅特判 handled；queued/started 均继续） |
| 记忆 retire/activate 管理面已有 | 属实（project.py /carto-memory CRUD，本任务不重复） |
| fence/scrub 已有 | 属实（fence.py marker 中和 + secret 双扫） |
| 预拼 cartography 块路径 | 属实：kill-switch off 时 route 预拼（chat.py:1092），经 legacy_compat→同一批 providers |

## 源矩阵（WP01 核心结论）

可选 priors（用户可优先/排除，条目级）：
- `pk:<entry_id>` — project knowledge entry（权威版本 = version_token，随 authority liveness 失效）
- `cm:<fact_id>` — cartography memory fact（active 事实；权威版本 = last_verified_at + fingerprint）
- `gm:block` — GIS 空间记忆整块（org 级投影文本，块级粒度）

不可由本面板关闭（控制/权威域）：user_message、cartography_verdict、session_plan、environment、tool_surface、active_tools、turn_marker、v6_blocks、harness_state、representation_binding、gis_state_rebuild、evicted_refs、gis_context_card（权威工作上下文卡，ADR-0206）。

## 用词契约

- 「本轮提交给 Pi 的资料」= 宿主本 turn 新装配 prompt 块（有 receipt 证据）；≠ 模型完整读过的全部上下文。
- included（入块）/ omitted（预算/裁剪）/ excluded_by_policy（用户排除）/ stale（权威失效）。
- 状态机：`assembled → not_sent(reason) | submitted(disposition) | unknown`；disposition ∈ started/queued/handled 独立记录；turn_outcome ∈ completed/failed/cancelled 独立记录。
- 排除只影响新装配；不声称从 native 历史遗忘。
