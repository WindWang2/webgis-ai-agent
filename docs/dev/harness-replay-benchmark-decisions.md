# Decisions — Harness Replay + Benchmark + Explainability (R10)

配套: `docs/dev/harness-replay-benchmark-recon.md`（事实底座）、`docs/dev/harness-replay-benchmark-ledger.md`（里程碑账本）、`docs/adr/0183-harness-replay-benchmark-explainability.md`。
原则: Pi 仍是 Agent Host；本线只建 Pi 下方的 GIS-native replay/评测/解释能力；一切指标复用 #1269 基座。

## D1 — ReplayTrace = 打包层，不是第二套 trace

**决策**: ReplayTrace v1 以 18 阶段 `GisTraceChain` 为骨架（stage 名直接引用 `Stage` IntEnum），叠加 turn 包络（TurnEvidence.summary）、verdict 块（FINAL_VERDICT + MapCompletionResult 摘要 + gate result + goal satisfaction）、cost/timing 块、situation/governor/skill 的 versioned optional 预留字段。序列化为单个自包含 JSON artifact（`schema_version=1`，additive-only 演进，未知字段保留）。
**理由**: trace_v6 已在生产持久化（S13），再造轨迹存储违反防重复；但 64 条/session 窗口有损、verdict/cost 分散多处、无 situation/governor 位——打包层恰好补齐方向 10 需要的"完整 Harness 行为"单元。
**否决案**: 新事件总线（observability `emit_event` 零调用点，引入即是平行系统）；给 trace_store 加字段（热文件、共享 legacy）。

## D2 — Recorder 单点骑 S13 settle 缝，env-gated，fire-and-forget

**决策**: `ReplayRecorder.maybe_record_turn(session_id, turn_id)` 在 `agent_pi_bridge.py` turn settle 的 `finally` 块内、`persist_turn_chain`/turn summary 之后调用。recorder 内部：env 总闸（`HARNESS_REPLAY_RECORD`，默认关）→ 从 registry/文件**只读**收集（当轮 chain JSONL、TurnEvidence summary、session harness telemetry、MapCompletionResult SSE 缓存摘要）→ sanitize → 单文件原子写 → **自吞全部异常**（任何 recorder 失败不影响主链路，仅 warning log）。
**理由**: B2 要求生产接线；settle 缝是唯一能同时看到全 chain + outcome + verdict 的点；additive 单调用把与 #1274/#1277 的合并冲突面压到最小。
**否决案**: 钩进 `ToolDispatchService.dispatch`（#1279 正在里面改）；钩进 chat.py env block（#1275 正在替换）；改 trace_store 签名（共享面太宽）。

## D3 — 无新迁移、无新表；ratchet/质量事实全部走 #1269 基座

**决策**: 不加 Alembic 迁移。轨迹质量观测用 `record_quality_run(lane="replay", source="replay_bench", checks=[...])` 入现有四表（lane 为自由字符串截 20 字符，cartography_metrics_store.py:219 已核实）；基线走 `quality_ratchet_gate.py baseline --from-json`；ratchet 数学直接调 `evaluate_ratchet`。migrations/.alloc.json 不领号。
**理由**: 防重复 + 资源约束（无 DB schema 风险）；`collect_observation_rows(lanes=("replay",))` 天然可查。

## D4 — ADR-0183，独特文件名

**决策**: `docs/adr/0183-harness-replay-benchmark-explainability.md`。已核验 master ≤0179、open 分支占 0180×3/0181/0182×2，0183 未被任何分支占用。仓库已有 0180/0182 撞号先例（不同文件名并存），本号撞号风险最低。

## D5 — Sanitize 纪律：白名单 + 上限 + 剥离

**决策**: ReplayTrace.tool_calls 只存 `{tool_name, call_id, arguments(白名单键+bytes 计数), status, duration_ms, error_msg(截断)}`；**不存** `llm_payload` 全文、任何 prompt/CoT/消息历史、原始 geojson（只存 `geojson_ref` + digest）、raw_result（只存 `result_digest` + 尺寸）。环境变量键名模式 (`KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `AUTHORIZATION`) 递归剥离；字符串字段按类别上限截断（args 2KB/条、error 512B、整 trace 默认 512KB 超限降级为 digest-only 模式并标注 `truncated=true`）。时间戳统一 `turn_monotonic_ms`（相对 turn 起点单调毫秒）+ 墙钟仅存起点，digest 计算前全部规范化。
**理由**: B1/B2 硬约束（no secrets/CoT、bounded、stable ids）；`redact_provenance_args`（manifest.py:86）是仓库既有脱敏先例，对齐其风格。

## D6 — Replayer 分层重放（T1/T2/T3），决策重放而非 LLM 重生成

**决策**:
- **T1 证据级（全场景必跑）**: 以录制/编排的 tool_calls + receipts 为输入，重放 harness 证据管线（`PiAgentHarness.record_*` → `HarnessEvaluator.evaluate_evidence` → `derive_goal_satisfaction`），比对 verdict 结构化字段。
- **T2 变异级（带 map_actions 的场景）**: 把录制 `map_actions[].command`（纯 JSON intent）按序 apply 到全新内存 MapSpec kernel，比对 `mapspec_fingerprint`/`mutation_revision`/`cartography_findings`。
- **T3 dispatch 级（fixture-backed 场景）**: 用 fabric_fixtures 假服务 + offline guard 重发 recorded tool 调用，比对 receipt status/ref 形状。默认只在显式声明 `dispatch_backed=true` 的场景启用。
- 输出比对三分类: `exact`（白名单结构化字段相等）/ `tolerant`（数值指标 ±tol，ratchet 语义）/ `nondeterministic_text`（LLM 自然语言只验存在性+长度带，永不精确比对）。
- 确定性: 注入固定 epoch 时钟 + 种子化 id mint（`turn-<seededhex>`）+ 单进程顺序执行；conftest 的 offline guard 直接复用。
**理由**: vendor/pi 未初始化、tests pin USE_NEW_AGENT=false、无 LLM key——"重放 LLM 决策、重执行确定性下游"是唯一可离线且确定性的层；T2 是本系统真正的回归牙齿（指纹级）。geocompute `replay_trace` 的不变量校验器风格是仓库先例。
**否决案**: HTTP 层端到端重放（需要起服务+Node+LLM，违反资源与离线约束）。

## D7 — 场景语料 = 紧凑矩阵 + 确定性展开器，提交展开产物索引

**决策**: `app/lib/harness/replay/scenarios.py`（矩阵与展开逻辑）+ `tests/fixtures/replay/`（展开后的场景 JSON + 共享 fixture tool 结果目录 + CSV 索引账本）。核心矩阵按任务书 B4 十七类 × 数据形态轴展开出 **≥120 core 场景**（含 36 个 multi-turn 3–8 轮），另含 fault 场景组（B6 十类）。校验测试（`-m cartography`）保证：计数下限、类别覆盖、fixture 引用全解析、offline 可跑。
**理由**: 手写 100+ JSON 不可维护也不可 review；golden corpus `build_cases()` 与 ads 864 组矩阵是仓库既有先例；展开产物提交使语料 diff 可 review、外部工具可消费。
**multi-turn 情境持续性**: 多轮场景在同一 SessionStore/SessionPlan/MapSpec kernel 上顺序执行，断言第 N 轮 verdict 依赖第 N-1 轮的 map 状态（如"用户 pin 图层后再导出"），证明 harness 维持情境而非每轮 reset。

## D8 — Fault 注入 10 类，全部落 replayer 环境而非 monkeypatch 业务码

**决策**: fault 规格 `{type, target, params}` 由 replayer 在执行前编译进冻结环境：`source_unavailable`（fake server 拒连）、`timeout`（tool 响应延迟超时）、`invalid_tool_result`（畸形 receipt）、`stale_ref`（geojson_ref 指向不存在 ref）、`map_revision_conflict`（apply 时预占 revision）、`pi_restart`（turn 中断后 resume 语义）、`late_sse`（事件晚于 settle）、`renderer_failure`（render_status=issues 注入）、`judge_unavailable`（visual judge not_evaluated → fail-closed 断言）、`store_transient`（首次写失败重试）。每场景断言 fail-closed / fallback / resume 三者之一，禁止静默 pass。
**理由**: ads4 30 组 fault 矩阵（ADR-0174）是先例；在 replayer 边界注入避免给生产代码埋测试钩子。

## D9 — Ratchet provisional-first；intentional degradation 测试证明闸会红

**决策**: bench CLI 产出 ratchet 行 JSON；基线入库默认 `provisional`（只记录不拦截），激活留给维护者用现有 CLI `--activate`（ADR-0159 纪律）。仓库内必须带一个"故意劣化"测试：对同一场景注入劣化观测，断言 `evaluate_ratchet` 产生 violation 且 CLI check exit 1。waiver 复用既有 expiry 机制。
**理由**: 任务书 B7 明确禁止自动更新 baseline 掩盖退化；#1269 已把闸纪律建好，本线只接流。

## D10 — 范围裁剪

**决策（v1 不做）**: legacy ChatEngine path 的 recorder 接线（生产默认已是 Pi path；legacy 只在 tests pin）；前端只读 inspector UI（v1 交付 JSON+Markdown，Dev UI 留接口点）；CI workflow 改动；任何 #1274/#1275/#1276/#1277/#1278/#1279 功能面的实现或复制。
**接口点预留**: ReplayTrace `optional` 字段（situation_revision/governor/skill_id/plan steps 引用）+ `schema_version` 演进规则（additive-only，未知字段保留）写进 ADR；#1277 合并后 `PlanStep` evidence 可经 optional adapter 进 trace。

## D11 — 资源与门禁纪律

- 测试 marker: 全部 replay 测试打 `cartography` marker（确定性、无 Node/Chromium/LLM/network），并入既有发布闸 lane；不新增 pytest marker（防 CI lane 契约漂移）。
- bench CLI 是 `scripts/replay_bench.py` 独立入口（不进 pytest 收集）；并发默认 1，上限 `--jobs 2`；不受 #664 perf 隔离契约影响。
- 迭代期 `pytest --no-cov -q` scoped；全量回归在最终阶段串行一次。
