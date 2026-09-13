# ADR-0161: V11 W1 — 意图与配方的可学习化（证据库、反馈信号、配方亲和、记忆置信/过期、语料千条、澄清台账）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-cartography/v11-master（W1）
- 关联: ADR-0069（项目制图记忆）、ADR-0150（意图语义双轨）、ADR-0151（配方回退链 v4）、ADR-0159（质量事实库 0056）、AC-01（意图 recon 锚点）

## 1. 背景与靶心

缺口 G11：「下一张是否更好」无验收、无用户反馈闭环、无跨会话学习。勘察（W1.0）
实证：意图裁决、澄清请求、回退链尝试目前全部 fire-and-forget（内存 + Prometheus
计数器）；唯一的持久学习信号是项目级二值 `recipe_outcome` 事实（0021）与 per-session
文件 recovery ledger。W1 把三类信号变成**可查询、可回放、可聚合**的学习基座。

## 2. 决策一：迁移 0057（学习基座三表 + 记忆增列）

- `carto_intent_evidence`：一行 = 一次意图语义裁决完整快照（query 截断 512 +
  hash / task / fallback / matched_rules / candidates / slots / confidence /
  confidence_components / clarification / degraded_reason / lang / source）。
  可按会话/任务/时间过滤查询，按行回放。
- `carto_feedback_signals`：`{type, weight, decay_days}` 信号账本；词表闭集
  （palette_change / chart_kind_change / rephrase / accept / reject /
  repair_success / repair_failure / clarification_answered）；读取侧按
  半衰期衰减（0.5^(age/decay)，decay≤0 不衰减）。
- `carto_recipe_affinity`：recipe_id 唯一；成功/失败计数 + Laplace 平滑权重
  `(s-f)/(s+f+2)`（冷启动 0 = 中性）；`cold_start_weight` 保留字段。
- `carto_project_facts` **只增列**：`confidence`（None = 既有行为）与
  `expires_at`（None = 不过期）—— 既有字段零改动（repo 纪律）。
- 领号 0057（.alloc.json adaptive-cartography 段 0056–0065），单波单迁移。

## 3. 决策二：作用域分层（与 ADR-0069 的关系）

- ADR-0069 项目记忆 = **项目级事实**（先验而非证据，project 谓词锁定）；
- 本 ADR 的 `carto_recipe_affinity` = **引擎级手艺先验**（recipe 全局不分项目
  —— 配方好坏是引擎手艺，不是项目事实）；
- `carto_intent_evidence` / `carto_feedback_signals` = session/project 双可归因
  （会话记忆维度 + 项目维度并存）。
- 红线不变（ADR-0069 决策 2）：学习信号只影响下一次裁决的**起点**（排序先验、
  注入块），永不参与 verdict 计算、永不让检查跳过。

## 4. 决策三：生产缝（fail-safe，记账永不让主链路失败）

- **意图证据**：`capture_intent_adjudication`（独立短会话，与 memory_harvest
  同模式；测试可 `set_session_local_factory` 覆写）接入两缝：
  ①`plan_orchestrator` GIS Harness 附着点（主规划路径，带 session_id）；
  ②`resolve_intent_adaptive`（自适应路径；新增可选 `session_id` 参数，additive）。
- **配方亲和**：学习写入缝在 `memory_harvest._harvest_sync`（评审通过 =
  success，与项目 recipe_outcome 事实同缝同事务语义）；读取缝在
  `resolve_fallback_chain` 新增可选 `affinity` 参数 —— **同 priority 并列时**
  按权重降序取优（未登记 0.0 中性；权重仍同 → id 字典序），不改变 priority
  语义、可行集与确定性；planner 侧 `_chain_affinity_prior` fail-safe 读取
  （DB 不可用 → None = 无先验，链行为逐字节不变）。
- **澄清台账**：`clarification_metrics()` 从证据库聚合 hit_rate（回答/澄清）与
  false_trigger_rate（澄清/总量）；空库诚实返回（hit=1.0=未误触，trigger=0）。

## 5. 决策四：语料扩容 300 → 1000（zh 600 / en 400）

- 生成器入仓（`tests/cartography/expand_intent_corpus.py`）：**确定性、幂等、
  可审计** —— 变换仅作用于任务判定无关的句子框架（zh/en 各 10 模板轮转）；
  错拼变体只扰动主体/地名字符（任务关键词不动），显式标 `v11_typo`；
  `clarify` 恒 false（模糊条目只用人工审定的 10 条种子）。
- 新门禁（`test_intent_corpus_v11.py`）：overall ≥ 0.6933 + 0.05 = **0.7433**
  （实测 0.948）；en ≥ 0.9×zh；fallback_rate < 0.25（实测 3.5%）；17 任务族
  不缩族；错拼 ≥60 条；生成器 byte 级重放一致。
- **防劣化**：既有 300 条 +8pt 门禁（test_intent_adaptive）与 204 条闭环矩阵
  （test_closed_loop_corpus_v6）原样保留并继续全绿。

## 6. 决策五：记忆置信度与过期（W1.4）

- `record_fact` 新增可选 `confidence` / `expires_at`（upsert 双路写入）；
- `get_active_facts` 读取侧过滤 `expires_at`（NULL = 永不过期）—— 过期记忆
  不再注入，但不删除（可审计回溯）。

## 7. 首轮数值（台账）

- 语料 1000 条：task_hit_rate 0.976（definite 990）/ overall 0.948 /
  fallback 0.0354 / subject 0.8224 / scope 1.0；zh task 0.9424、en 0.98。
- 澄清：10 条模糊种子在扩容语料中保持 100% 触发；false_trigger 语义入
  `clarification_metrics`（台账数据源；报表随 W8 看板落盘）。
- 亲和：冷启动 0 中性；formula 单测锁定 (s-f)/(s+f+2)。

## 8. 风险与回滚

- 学习信号引入排序抖动 → 只在同 priority 并列生效 + 确定性公式 + 无先验
  行为逐字节不变（测试锁定）；
- 证据库膨胀 → 有界查询（limit≤200）+ 字段截断；量级治理归 W8 成本治理；
- 回滚点：tag `ac-v11-w1`；迁移 downgrade 反序回滚。
