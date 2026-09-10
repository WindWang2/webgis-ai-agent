# Harness V6 Baseline（只读审计 @ origin/master 8a33e3a5）

Branch: `feat/harness-v6-semantic-autonomy` · Worktree: `../wt-harness-v6` · Baseline: `8a33e3a5`

## V5 已落地（ADR-0118，全部合并 —— 不重做）
- **D1 durable trace**：`trace_store.py` flock + 单调 seq + `(turn_id,total_records)` 幂等 settle + FINAL_VERDICT trim 保护 + registry pin/unpin（8 进程零丢行契约测试）。
- **D2 统一失败分类**：`failure_taxonomy.py` 11 类 HarnessFailureClass + planning/geocompute 适配器 + `classify_and_remediate` dispatch seam 单点 + `RemediationLedger`（**进程级** LRU≤512 + TTL 1h）+ 全表 max_attempts≤3。
- **D3 CRS/经度硬化**：`app/lib/gis/longitude.py` + typed InvalidCRS（KNOWN-GAP #1 xfail 转正）。
- **D4 Progressive DatasetProfile**：`deepen_profile` cheap→deep + longitude_facts + planner fact bundle 透传。
- **D5 rendered-state observation**：`render_observation.py` 逐层 render_complete/source_status/feature_count + chart rendered/data_points 校验（optional 门控）。
- **D6 subagent accounting**：`subagent.py` llm_usage roll-up + parent_budget overlay + lineage 事件。
- **D7 retrieval V5 评测**：`retrieval_eval_corpus.py` 66 条人工金标（direct 30/近重复 6 对/hard_negative 12/ambiguous 12）；**V5 基线指标：p@1=0.6515, r@5=0.8093, r@10=0.8674, invalid=0.3333（hard_negative 内 0.5）**（`tests/unit/test_retrieval_eval_v5.py:26-33` 钉线）。
- **D8 project resume anchor**：`resume_anchor.py` + 表 `workflow_resume_anchors`（migration 0032）+ fail-closed 授权 + ref 重水合/ref_map 重写/dangling 披露。
- **D9 runtime fault-injection corpus** + 端到端恢复 scenario。

## V6 缺口 census（file:line 证据）

### G1 Semantic Retrieval（goal 1）
1. 语义检索 hook **无默认实现**：`tool_surface_v3.py:40` `_SEMANTIC_RETRIEVER_SPEC=os.getenv("TOOL_RETRIEVAL_SEMANTIC","")` —— 未设 env 时 `_load_semantic_retriever()` 返回 None，生产恒词法。embedding 基建存在（`app/services/rag/faiss_store.py:134` sentence-transformers 384-dim，懒加载、offline 可有界失败）但未接入工具检索。
2. 无 hybrid 融合：lexical（`tool_retrieval.py` 权重表）+ capability 精确命中（`tool_surface_v3.py:534`）+ 数据画像域（L539）为**朴素加法**，无方法论证据（workflow_v4/methodology.py 12 方法族/44 候选未参与检索）。
3. 无置信度/弃权：`select()`（L461-598）恒返回 top-k，低置信度时照常派发（违反 V6「拒绝乱选」）。
4. 语料仅 66 条（goal：数百级→1000+ 可扩展）；无 calibration/abstention 指标；无 out-of-scope 类（registry 不存在的能力）。

### G2 Durable Context（goal 2）
- `resume_anchor.py` 只是恢复**指针**（user_goal+chapter 关键块+trace 游标+ref 清单）；无 durable-facts / rebuildable-projection / forbidden 分层模型；reasoning trace 摘要与 recovery state 不入锚点；turn 边界不自动落 project 级上下文。

### G3 Persistent Remediation Ledger（goal 3）
- `failure_taxonomy.py:252-292`：`RemediationLedger` **进程级内存**（docstring 自认「进程级=诚实口径；会话级持久预算由 finalizer/repair memory 负责」）；跨 worker 不一致；重启后预算归零 → 可无限重试循环复活。
- dispatch **成功路径不回写**预算（成功后 attempts 不清零，TTL 1h 才衰减）。

### G4 Trace Store V6（goal 4）
- `trace_store.py:239 read_chains` / `L259 last_seq` 每次全文件 JSONL 解析；trim 在锁内全量 read→rewrite（L177-196）；单文件滚动窗口 64 条无分段/压缩；无增量游标读 API；无跨进程汇聚接口（GeoCompute trace bridge 未核实到消费方）。

### G5 Long-Horizon（goal 5）
- resume anchor 存在但 resume 后的**继续执行状态机**（数据资格不足→deepen→重资格→继续；渲染失败→分类→remediation→重试→re-verify）散在 finalizer/runtime_repair，无统一 continuation 裁决点；预算耗尽 abort 路径有（remediation_for）但与 resume 的衔接未闭环。

### G6 Subagent（goal 6）
- llm_usage/roll-up/depth≤2 已有；缺：按角色/模型的 **budget class** 词表（现每 role 固定 tool_calls/heavy/wall 数字，无 class 概念）；cancel/timeout 路径的主 turn 安全已有测试但无 chaos 级验证。

### G7 Rendered-state（goal 7）
- 现状 mounted/visible/render_complete/source_status/feature_count + chart rendered/pts（`render_observation.py:216-353`）；缺**统一状态阶梯** mounted→loaded→rendered→data_present→semantically_correct 与 pending/unknown 诚实三态的显式词表；finalizer 以「工具调用成功」与真实 evidence 的边界仍靠散落校验；workflow blocked/degraded/partial 无统一消费接口。

### G8 Chaos（goal 8）
- V5 runtime_corpus 有 fault-injection 8 类；缺：客户端断连、重复 turn、Pi bridge cancellation、Redis 短故障、worker 重启、trace 写中断、resume dangling ref、render telemetry 迟到等 chaos 场景对**锁泄漏/无限重试/预算丢失/跨 session 污染**的系统验证。

## 资源/约束（沿用 V5 + 更新）
- Alembic head = `0033_geocompute_v6_cluster`。本 Epic 原则上**不加表**（session-plane 文件 + 既有表 additive JSON 键），若必须加表用 0034 独立 revision。
- ADR 编号：并行 Epic 同号惯例存在（六个 0118-*）；本 Epic 取 `0119-gis-harness-semantic-autonomy-v6.md`，rebase 时复查。
- Ruff E4/E7/E9/F；pytest asyncio_mode=auto timeout=60s；CI lane `-m "not perf and not cartography and not real_services"`。
- 本 Epic 执行层 ≤2 subagents（两轮 review 各一）。
- embedding 模型为可选依赖（懒加载、offline 有界失败）—— 语义检索默认路径必须**零模型依赖可完整工作**（词法+扩展词表+capability graph+方法论证据），embedding 为 additive 增强。
