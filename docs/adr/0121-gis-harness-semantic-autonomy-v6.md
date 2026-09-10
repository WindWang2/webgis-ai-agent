# ADR-0121: GIS Harness Semantic Autonomy V6 — Semantic Retrieval, Durable Context & Long-Horizon Autonomy

**Date:** 2026-09-09
**Status:** Proposed
**Branch:** `feat/harness-v6-semantic-autonomy`
**Baselines:** ADR-0118（Harness V5，可恢复/可诊断/渲染可证）· 审计 `.agent-work/harness-v6/00-baseline.md`

## Context

V5 交付了 durable trace、统一失败分类、progressive DatasetProfile、rendered-state
telemetry、subagent token 记账与 project resume anchor。V6 审计（file:line 证据
见 baseline G1-G8）确认八个结构性缺口：语义检索 hook 无默认实现（生产恒词法）、
无置信度/弃权、金标语料仅 66 条；resume anchor 只是恢复指针（无
durable/rebuildable/forbidden 分层）；remediation ledger 进程级内存（跨 worker
预算不一致、重启归零）；trace 读取全量 JSONL 解析；long-horizon 续行裁决散落；
rendered observation 无统一状态阶梯；chaos 场景无常设语料。

## Decisions

1. **D1 Hybrid Tool Retrieval**：`semantic_retrieval.py` 四路确定性信号 ——
   双语同义扩展词表（二次词法 0.5 降权融合，base 分不被污染）+ capability
   别名（口语短语 → 139 能力 id 精确反查）+ 方法论证据（12 方法族路由词 →
   方法 priority 加权 → 工具加成，只作检索信号不建第二 planner）+ 否定反证
   （「没有X」→ X 域词扣减）。embedding 检索器为可选第 5 路（默认 spec 指向
   内置实现，懒加载 + 失败 memoization + `TOOL_RETRIEVAL_EMBEDDING=0` 关停）；
   评测钉死纯确定性通道（模型本体进程级缓存，懒加载一次）。`GIS_TOOL_RETRIEVAL_V6=0` → 与 V5 逐位一致。
2. **D2 Confidence / Abstention**：`confidence = 0.5·level + 0.35·margin +
   0.15·coverage`；`ABSTAIN_THRESHOLD=0.35`。弃权不改变投影内容，只暴露
   「不确定」；生产 seam（`compute_turn_active_tools`）弃权时**不注入**动态面
   并链上披露（拒绝派发语义），native 前门含 `list_available_tools` 两跳通道。
   诚实边界：置信度单调区分 hit(0.687)>miss(0.614)>oos(0.510) 但重叠大，
   防乱选的主力仍是 dispatch 校验 + finalizer evidence，弃权是披露面。
3. **D3 Corpus 358**：金标 66→358（direct 208/近重复 52/hard_negative 52/
   ambiguous 32/out_of_scope 14，zh+en+混排）。V5 部分 valid 集放宽至语义等价
   工具（isochrone_analysis/multi_ring_buffer 等，must_not 语义不变）；
   修正 2 个语义错位查询；撤 3 个 CORE 结构性不可达案例。
   **词法判别力修复**（tool_retrieval.py）：单字 CJK 停用字零权重 + 其余单字
   ×0.25 + anti 仅多字 —— V3==V4off 契约不变。20 条 `anti_examples` 声明式
   负证据覆盖易混淆兄弟工具对（11 个 descriptor 文件）。**同语料对照：p@1 0.4944→0.5587（+6.4pp）、
   r@5 0.6731→0.7523、r@10 0.7289→0.8059、invalid 持平 0.25；oos 弃权
   21.4%、误弃权 0%、ECE 0.31。**
4. **D4 Durable Context 三分层**：`durable_context.py` 封闭词表
   （durable facts / rebuildable projections / forbidden；白名单外保守视为
   forbidden）。recovery_state（position/loops/history ≤16）live 于
   session-plane map_state、durable 于锚点 additive JSON 键（`recovery_state`
   + `reasoning_digest`，零新表零 migration）。LLM raw context/密钥/无界载荷
   永不持久化。
5. **D5 Persistent Recovery Ledger**：`recovery_ledger.py` ——
   (session, tool, failure_class)→attempts 的 durable 账本（session-plane
   JSON + flock + write-through）；dispatch **成功回写**清零该工具计数；
   TTL 惰性衰减按读取时钟；每 session ≤256 条 LRU；resume
   `copy_between_sessions` 有界续接（恢复不重获满额预算）。
   `classify_and_remediate` 通道选择：注入账本 > durable（有 session_id）>
   进程级兜底；不重复记账。
6. **D6 Trace Store V6**：分段布局（`trace_v6/seg_N.jsonl(.gz)` + manifest）。
   写路径 O(段)：append → 段满滚动 gzip 归档 → 整段淘汰（manifest 驱动零
   解析）→ 边界段记录级重写；**精确 64 条窗口**与 V5 逐记录 trim 语义对齐
   （FINAL_VERDICT 保护、无条件有界保持）。`read_chains_since(after_seq)`
   增量读（旧整段零解析跳过）；`last_seq` manifest O(1)；torn-tail 自愈；
   `iter_session_chains` 汇聚接口（只读证据面，非第二状态源）。V4/V5 单文件
   legacy 读取容忍 + seq 续写。
7. **D7 Long-Horizon Continuation**：`continuation.py` 单一纯函数裁决点
   `decide_continuation` —— 硬预算（durable ledger ≥3 或全回路余量 0）→
   `abort_with_disclosure`；cancelled/budget_exhausted → abort；渲染失败 →
   repair→reobserve；资格不足 → deepen_profile→requalify→abort（拒绝带病
   执行）；observation pending/unknown → reobserve。resume 后从 durable 证据
   重判，不机械 replay。接线：runtime_repair 耗尽/应用点挂
   `outcome.continuation`；不新开主循环、不做第二 planner。
8. **D8 Subagent Budget Class**：`BUDGET_CLASSES`（light/standard/heavy/
   research）—— token 上限为 class 独有维度；tool/heavy/wall 上限交集
   （class 只收紧不放宽）；`SubagentRole.budget_class` additive（默认
   standard，既有 role 数字全部 ≤ standard 上限 → 行为不收紧）；usage()
   携带 class 审计留痕；depth≤2/取消安全沿用 V5。
9. **D9 Observation State Ladder**：`observation_states.py` 封闭阶梯
   unknown→pending→mounted→loaded→rendered→data_present→semantically_correct
   （诚实弱态优先：无遥测 unknown、源错误 pending、feature_count=0 不谎报
   data_present、intent 核对只升级 data_present）；`to_workflow_health`
   （ok/degraded/partial/blocked）为 workflow runtime 统一消费词汇；
   `map_product_block` additive 键 `observation_health`（恒发射：缺席
   observation → blocked）。
10. **D10 Chaos Corpus**：`chaos_corpus.py` 17 条确定性场景（故障 → 不变量 →
    真实 pytest 节点），覆盖 Epic 目标 8 八类；元门测试断言语料行指向的测试
    真实存在。真实 `kill -9` 锁释放、跨 session 隔离、resume 预算续接等
    不变量测试钉死。

## Consequences

- 检索质量可度量、可回归（金标门 + 弃权/校准指标 + perf 结构预算）；
- 预算语义跨 worker/重启一致（durable ledger + 锚点续接）—— 「重启后无限
  重试复活」结构性消除；
- trace 读取从 O(窗口全解析) 降为 O(游标后段)；长程会话的增量消费有接口；
- 诚实纪律贯穿：observation ladder 三态、continuation abort 披露、oos
  弃权率/误弃权率分列、chaos 语料节点可验证。
- 已知限制：oos 弃权为部分信号（21.4%）；hard_negative 陷阱 top-5 命中
  （invalid 0.25）为暴露线非达标线；embedding 通道在无模型部署降级词法
  （评测不含 embedding，独立 skipif 通道）。
