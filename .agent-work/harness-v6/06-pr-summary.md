# Harness V6 PR Summary — Semantic Retrieval, Durable Context & Long-Horizon Autonomy

Branch `feat/harness-v6-semantic-autonomy`：8a33e3a5..HEAD（11 commits）
- 本任务按要求不等待/不依赖线上 CI/CD，全部本地验证。

## Problem / Motivation

V5 交付了可恢复、可诊断、渲染可证的 runtime（ADR-0118）。V6 的目标是在真实
项目级情境中让系统**持续理解任务、稳定检索工具、维护长期执行上下文、跨
worker 恢复、持久化 remediation 预算**，并以 rendered evidence 驱动自诊断与
局部修复。审计（`.agent-work/harness-v6/00-baseline.md`，file:line 证据）确认
八个结构性缺口：语义检索 hook 无默认实现、无置信度/弃权、金标仅 66 条；
resume anchor 只是恢复指针；remediation ledger 进程级内存（重启归零/跨 worker
不一致）；trace 全量 JSONL 解析；long-horizon 续行裁决散落；observation 无
统一状态阶梯；chaos 场景无常设语料。

## Current-state audit

`.agent-work/harness-v6/00-baseline.md` —— V5 已落地清单（D1-D9 不重做）+
V6 缺口 census（G1-G8，每条带 file:line）。V5 关键事实：trace flock/seq/
幂等已存在；RemediationLedger 进程级（failure_taxonomy.py:252）；retrieval
hook 无默认实现（tool_surface_v3.py:40）；金标 66 条、p@1=0.6515/invalid=0.3333。

## Architecture（ADR-0119，docs/adr/0119-gis-harness-semantic-autonomy-v6.md）

D1 hybrid 检索（4+1 路确定性信号）· D2 置信度/弃权 · D3 语料 358 ·
D4 durable context 三分层 · D5 durable recovery ledger · D6 trace 分段/增量读 ·
D7 long-horizon continuation 裁决点 · D8 subagent budget class ·
D9 observation 状态阶梯 · D10 chaos corpus。

生产链路闭环：
`NL query → hybrid retrieval（W2-W4）→ TOOL_SURFACE 链事件 → tool dispatch →
typed failure + durable 记账（V5+W6）→ 成功回写/预算耗尽 abort（W6）→
continuation 裁决（W8）→ resume：锚点 + recovery_state + 账本续接（W5+W6）→
rendered-state 阶梯 + observation_health（W10）→ finalizer intent↔rendered（V5）`

## Implementation waves

W1 审计 → W2-W4 检索/语料/置信度 → W5 durable context → W6 durable ledger →
W7 trace V6 → W8 continuation → W9 subagent budget class → W10 observation
ladder → W11-W12 chaos corpus+不变量 → W13 perf 结构预算 → W14 ADR/docs →
两轮 review 修复。

## Key code paths

- `app/services/chat/semantic_retrieval.py`（新）：双语扩展词表/capability
  别名/方法论证据/否定反证/embedding 检索器（模型进程级缓存）
- `app/services/chat/tool_surface_v3.py`：select() V6 hybrid 阶段 + 置信度；
  `pi_native_surface.compute_turn_active_tools` 弃权收缩面 + 模型面披露
- `app/services/chat/tool_retrieval.py`：单字 CJK 判别力修复（V6 门控）
- `app/services/gis_harness/recovery_ledger.py`（新）+ `failure_taxonomy.py`
  通道选择 + `tool_dispatch_service` 成功回写
- `app/services/gis_harness/trace_store.py`：分段布局 + manifest + heal
- `app/services/gis_harness/durable_context.py`（新）/ `resume_anchor.py`
  锚点采集/重注入/账本续接
- `app/services/gis_harness/continuation.py`（新）+ `runtime_repair` 接线
- `app/services/gis_harness/observation_states.py`（新）+
  `completion/pipeline.map_product_block` observation_health
- `app/services/subagent_roles.py`/`subagent.py`：BUDGET_CLASSES 交集 + token 闸
- `app/evaluation/retrieval_eval_corpus.py`（358 金标）/ `chaos_corpus.py`（17 场景）

## Data / persistence changes

- **零新表、零 migration**。新增 session-plane 文件：
  `.webgis-agent/<sid>/recovery_ledger.json`（durable 账本）与
  `.webgis-agent/<sid>/trace_v6/`（分段 trace + manifest）；V4/V5
  `trace_chains.jsonl` 读取容忍、只读保留、seq 续写。
- `workflow_resume_anchors.anchor` JSON additive 键：`recovery_state`/
  `reasoning_digest`。gis_chapter `map_product` additive 键：
  `observation_health`。

## API / contract changes

- `POST /sessions/resume-from-anchor` 响应 additive 键
  `recovery_budget_carried`；observation 上报响应 `runtime_repair.continuation`
  additive。`SurfaceSelection.as_dict` additive（confidence/abstained/
  abstain_reason）。无 breaking 变更；`GIS_TOOL_RETRIEVAL_V6=0` 一键回到
  V5 逐位行为（打分数学含在内，回归钉死）。

## UI changes

无前端改动（observation telemetry 仍为 V5 契约；observation_health 为后端
投影键）。

## Security implications

- 弃权/低置信度不再静默乱选（模型面披露 + 两跳指引）；tier-3 红线不变。
- durable context 三分层：LLM raw context/token/密钥/白名单外键永不持久化；
  resume 授权语义不变（owner 严格匹配 + 匿名双拒，recovery 注入在授权后）。
- session_id 字符过滤阻断路径遍历；anti_examples 注册期收口（≤8 条/≤200 字）。

## Performance baseline / results

- 同语料 358 条对照（`GIS_TOOL_RETRIEVAL_V6=0` vs `1`）：
  **p@1 0.4944→0.5587（+6.4pp）/ r@5 0.6731→0.7523 / r@10 0.7289→0.8059 /
  invalid 持平 0.25**；oos 弃权 21.4%、误弃权 0%、ECE（全案例分桶）钉线。
- 358 条全量评测 ~3.5s（结构预算门 ≤60s）；单次 select ≤2s 哨兵；
  trace 增量读由文件访问计数证明（旧段零解析）；last_seq manifest O(1)。
- embedding 为 additive 增强：无模型部署零影响（有界失败 + memoization）。

## Local test matrix（exact results）

- **changed-scope 总回归：1152 passed**（gis_harness 全量 + 检索/账本/trace/
  subagent/surface/turn-context/descriptor 全套件）
- retrieval 门 6 + semantic 单测 12 + recovery ledger 11（含双进程并发）+
  trace V5 契约 8（多进程零丢行/幂等/trim/V4 兼容对 V6 实现全绿）+ trace V6 6
  + durable context/continuation 14 + observation ladder 7 + chaos 不变量 6
  （含真实 kill -9 锁释放）+ subagent budget class 7 + perf 3
- perf lane：3 passed（词表有界/评测预算/单次延迟）
- lint：ruff 全绿（app/ + tests/）
- post-rebase（master 未移动，up-to-date）复测：同上全绿

## Review findings / fixes（两轮独立 review，≤2 subagents）

- **Round 1（架构/正确性/状态机/一致性/边界/兼容）REQUEST-CHANGES**：
  1 CRITICAL（embedding 指纹调用必死+假测试覆盖声明）+ 5 MAJOR（kill-switch
  契约不成立/ECE 只对命中分桶/token 闸未接线/abort 不可达/manifest 无自愈）
  + 12 MINOR —— **全部修复**，修复过程中发现并修复一个真实 trim 回归
  （孤儿段文件→误 heal→窗口失真）。明细：`05-review-findings.md`。
- **Round 2（性能/安全/并发/资源/UX/维护性）APPROVE-WITH-FIXES**：
  1 MAJOR（embedding 模型每查询重载）+ 8 MINOR —— MAJOR 全修，MINOR 修复
  6 项、2 项文档化为 known limitations。明细：`05-review-findings.md`。

## Rebase / integration verification

`git fetch origin` → master 仍为 8a33e3a5 → rebase up-to-date；ADR 编号
0119 无冲突（master 无新增 0119-*）；零 migration（head 仍 0033）；CHANGELOG
additive 段无冲突；rebase 后 changed-scope 全量复测通过。

## Backward compatibility

- `GIS_TOOL_RETRIEVAL_V6=0`：检索打分+hybrid+置信度全部回到 V5 逐位行为。
- V4/V5 单文件 trace 布局读取容忍、seq 续写；旧客户端 observation（无新
  telemetry 字段）照常工作（V5 optional 门控保持）。
- `map_product.observation_health`/锚点新 JSON 键均为 additive，旧读者忽略。

## Known limitations

1. oos 弃权为部分信号（21.4%）：置信度对 hit/miss/oos 单调可分但重叠大；
   防乱选主力在 dispatch 校验 + finalizer evidence，弃权是披露面。
2. hard_negative top-5 陷阱命中（invalid 0.25）是暴露线非达标线：兄弟工具
   同族语义使 top-5 全避让不现实；已钉线防回归。
3. trace 全局写锁为已知取舍（正确性换吞吐；窗口有界、锁持有 ms 级）——
   按 session 分锁列为 follow-up。
4. dispatch 路径同步账本 IO 落事件循环（≤256 条 JSON、ms 级上界）——接受。
5. embedding 通道在无模型部署降级词法（评测门不含 embedding）。

## Follow-up candidates

- 按 session 分锁的 trace 写路径；manifest 二段提交。
- replan 回路的生产驱动点接入（LOOP_BUDGETS 目前只含有驱动点回路）。
- finalization 路径直接消费 decide_continuation（当前经 runtime_repair）。
- 金标语料 358 → 1000+（结构与生成纪律已就绪）。
- embedding 通道的量化/批量索引与独立延迟门。
