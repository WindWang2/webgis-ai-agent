# Recon — Harness Replay + Benchmark + Explainability (方向 10 / R10)

- 基线: `origin/master = 580b33e9`（Merge PR #1272 adaptive-data-supply/v1-master）
- 勘察日期: 2026-09-14；深度勘察由 Subagent A 执行（只读），主 Agent 复核关键锚点后定稿
- 线: `harness/replay-benchmark-explainability-v1`（独立 worktree）
- 结论先行: **18 阶段 GisTraceChain + trace_store V6 就是天然的 trajectory 骨架**；ratchet/质量事实库/golden/offline-guard 全部复用；本线新建的是 ReplayTrace 打包 schema、env-gated recorder、离线确定性 replayer、场景语料、fault 矩阵、ratchet 投影、explainability bundle 和 bench CLI。**不新建任何平行 planner/store/registry/quality 指标体系。**

---

## 1. 生产调用链（before）

Pi path 是生产默认 host（`USE_NEW_AGENT` 默认 True；tests 在 `tests/conftest.py:140` pin false，测试永不 spawn Node Pi 子进程）。

```
[HTTP] POST /api/chat/stream                      app/api/routes/chat.py:851  (chat_stream)
  │  auth/owner guard, mint session, DUP-1 resume branch (:894-957)
  │  S1  TurnEventBuffer(session_key, message)               chat.py:987      # 同 turn 的 SSE resume ring（进程内）
  │  S2  _record_frontend_cartographic_observation           chat.py:972      # 前端 map_state → session observation
  │  S3  _build_cartography_turn_context / env block         chat.py:981,985  # 情境/上下文组装（#1275 situation 将替换 env block）
  ▼
[Pi bridge] PiBridge.stream_prompt               app/agent_pi_bridge.py:2020
  │  turn_id minted "turn-<uuid4hex12>"           agent_pi_bridge.py:115
  │  S4  rt_ctx.bind_runtime_context(request/session/turn/run) :1036 + stream_prompt
  │        RuntimeContext frozen dataclass        app/lib/runtime/context.py:30, bind:106
  │  S5  TurnEvidence 注册（turn 键控 registry）    app/lib/runtime/evidence.py:119, :361
  │        → settle 时 to_summary()/emit_turn_summary   evidence.py:268, :391   # 每轮 timing/work/tokens/warnings
  │  S6  GisTraceChain（18 阶段，每 turn 一条）      app/lib/runtime/gis_trace.py:30 (Stage 1..18), registry.record:173
  │  Pi vendor events → SSE: map_event_to_sse     app/services/chat/pi_event_mapper.py:332
  │  turn_id 注入 step_result                      agent_pi_bridge.py:120, :2399
  ▼
[Pi extension (Node)] vendor/pi（git 子模块 earendil-works/pi，本地未 checkout）
  │  app/extensions/webgis-tools/index.mjs: native tools + 单代理 webgis_execute（ADR-0103）
  ▼
[Tool callback] POST /pi-tools/execute           app/api/routes/pi_tools.py:51（signed turn token）
  ▼
[dispatch_tool]                                  app/agent_pi_bridge.py:429 → _dispatch_tool_bound :474
  │  resolve_pi_tool_call（proxy/native/reject）   app/services/chat/pi_native_surface.py
  │  tier>=3 refuse / 存在性检查                    agent_pi_bridge.py:522-547
  │  SessionPlan slot ensured                      agent_pi_bridge.py:516 → session_plan.ensure_session_plan_slot:383
  │  S7  RuntimeContext+TurnEvidence 回绑           agent_pi_bridge.py:570-575
  │  S8  JobOrigin(session/run/turn/tool)           agent_pi_bridge.py:595, use_origin :606
  ▼
[ToolDispatchService.dispatch]                   app/services/tool_dispatch_service.py:329
  │  dedup / wave gate ADR-0100 / tool_cache / ref 解析
  │  → receipt = ToolDispatchResult              tool_dispatch_service.py:156
  │       status("ok"/"repeated"/"error") llm_payload slim_event geojson_ref raw_result
  │       error_msg map_actions[{action_id,command,requested}] ref_descriptor background_job_ids
  │  S9  tool_metrics.record_tool_call/event       app/services/tool_metrics.py:192,296（JSONL）
  │  S10 ToolCallEvent → PiAgentHarness            app/lib/harness/tool_call_event.py:14
  │        bridge: cancelled :626 / exception :647 / final :944-996（record_event :996）
  │        record_map_action_issued                pi_agent_harness.py:582（bridge :987）
  ▼
[post-dispatch fan-out]（全 best-effort, agent_pi_bridge.py:706-1032）
  │  dispatch-result cache（HTTP↔SSE rendezvous）  :301/:706
  │  SessionPlan.apply_tool_result                 session_plan.py:541（成功 :710 失败 :843）
  │  maybe_finalize_map_product                    completion/pipeline.py:577 → run_map_finalization:124
  │  workflow instance / RuntimePhase              gis_harness/workflow_instance.py, runtime_state_machine.py:462(:320)
  │  typed DAG projection                          gis_harness/runtime_bridge.py
  │  S11 chain stages 9/10/11/13 recorded           agent_pi_bridge.py:1016-1030
  ▼
[SSE 回流] tool_execution_end → step_result(geojson_ref) + SessionPlan SSE   agent_pi_bridge.py:2389-2394
  ▼
[Cartography quality loop]                       app/services/cartography_runtime.py
  │  evaluate_cartographic_session :527（session lock 串行；fail-closed not_evaluated :503）
  │  S12 质量事实库 hook                            cartography_runtime.py:58 → record_quality_run
  │        cartography_metrics_store.py:146（migration 0056 四表; CARTO_METRICS_STORE_ENABLED 总闸）
  │  visual judge（record_only 默认）               app/lib/harness/visual_evaluator.py:329/:505
  ▼
[World state] MapSpec lifecycle                   app/services/mapspec/lifecycle_engine.py
  │  MutationIntent 19-intent union :954; MapSpecResult :56 / BatchResult :154
  │  mutation_revision 单调; checkpoint ring        mapspec/checkpoint.py:253,380
  │  read model build_world_state                  app/services/gis_world_state/state.py:101
  ▼
[Turn settle]                                    agent_pi_bridge.py:2478-2545（finally）
  │  finalization 重跑; Stage 18 USER_OUTPUT        :2352-2364
  │  S13 chain JSONL 持久化                         trace_store.py:564 persist_turn_chain（:448 persist_chain, :593 read_chains）
  │  S14 outcome settle + turn summary INFO log     evidence.py settle（Outcome :45）; "[turn] {...}"
  ▼
[done SSE] sse_event("done", {session_id})       agent_pi_bridge.py:2471
```

Legacy ChatEngine path（`USE_NEW_AGENT=false` 回退）: `chat.py:1112` → `execution_engine.py:1778 chat_stream`；共享 ToolDispatchService、cartography runtime（`record_cartographic_dispatch_evidence`）、quality fact store、tool_metrics。**本线 v1 只接 Pi path（生产默认）；legacy path 不接**（见 decisions D10）。

### 1.1 既有 recorder seams（recorder 必须骑这些缝，不发明新缝）

| Seam | 机制 | 现状持久化 |
|---|---|---|
| S1 TurnEventBuffer | 进程内 ring，DUP-1 resume | 否 |
| S4 RuntimeContext | ContextVar 关联（req/sess/turn/run/proj/trace） | 仅日志 |
| S5 TurnEvidence | turn 键控 registry + `emit_turn_summary` INFO 日志 | 仅日志 |
| S6/S13 GisTraceChain 18 阶段 | `persist_turn_chain` → `<DATA_DIR>/.webgis-agent/<sid>/trace_v6/seg_N.jsonl(.gz)`；单 session 窗口 64 条（FINAL_VERDICT 防裁剪, trace_store.py:62）；`GIS_TRACE_PERSIST=0` 总闸 | **是**（有损窗口） |
| S9 tool_metrics | 后台写 JSONL（ADR-0044），`aggregator_snapshot() :367` | 是（logs 轮转） |
| S10 PiAgentHarness | session 键控进程内（`cartography_runtime.py:181/:1500`） | 部分（从 session map_state 水合 :361） |
| S12 quality fact store | `record_quality_run` → migration 0056 四表，90d/5000run 保留 | 是 |
| observability events | `emit_event`（EVENT_CATALOG 白名单） | **app/ 内零调用点（events.py:105 自己注明）——不能假设它在生产流动** |
| geocompute trace | `emit` + `replay_trace(events)` 确定性不变量校验器（replay.py:31） | ring + logs |
| GISRuntimeTrace | gis_harness/trace.py:78（finalization/repair/observation 计数） | 内存 |
| turn trace（ADR-0101 W8） | app/lib/runtime/trace.py:153 TraceRegistry（17 事件种类，docstring 自述 "debug bundle / replay 输入"） | 内存 |

关键含义：**真正在流动的持久面是 trace_v6 JSONL + turn summary 日志 + tool_metrics JSONL + 质量事实库**。recorder 的职责是把前两者在 settle 时刻原子打包成单个自包含 artifact（补上 64 条窗口的有损问题），而不是新造事件总线。

---

## 2. 可复用评测/回归设施清单

| 设施 | 路径 | 公开 API（精确） | 接线状态 |
|---|---|---|---|
| Evidence harness | `app/lib/harness/pi_agent_harness.py:279` | `PiAgentHarness(session_id, *, ref_resolver, mapspec_validator, cartography_state_reader, map_action_reader)`; `record_tool_call(...):378`; `record_tool_result(...):426`; `record_event(ToolCallEvent):562`; `record_map_action_issued(...):582`; `evaluate_with_evidence(...):673`; `evaluate_all(...):2080`; `MAX_EVENTS=1000` FIFO :290 | 生产（session harness） |
| Quality gate | `app/lib/harness/evaluator.py:48` | `HarnessEvaluator.evaluate_evidence(evidence_result, *, require_evaluated=True, ...) :94`（not-evaluated≠pass 策略; `not_evaluated_policy_fail`/`not_applicable_exempt` 理由词表）; `generate_markdown_report :250`; `DEFAULT_THRESHOLDS :16` | 生产 |
| Scenario runner | `app/tools/harness_runner.py:13` | `run_benchmark_scenario(...)`（构建 harness→record→evaluate_all→写质量事实 `source="harness_evaluator"`） | 工具/测试 |
| Cartography ratchet（ADR-0159） | `app/services/cartography_ratchet.py` | `Observation(scene_id, check_id, value, wave=None):75`; `Baseline(...):91`; `RatchetViolation:103`; `aggregate_observations(rows, quantile=0.66):148`; `aggregate_observations_by_wave:161`; `evaluate_ratchet(observations, baselines, waivers=(), tolerance_pct=5.0):228`; `build_baseline_entries:275`; `write_baselines:301`; `load_baselines:348`; `activate_baselines:375`; `add_waiver:398`; `collect_observation_rows(lanes, since_runs):457` | 本地 gate CLI |
| 质量事实库 | `app/services/cartography_metrics_store.py:146` | `async record_quality_run(*, lane, source, checks, session_id, scene_id, passed, summary, gate_scores)`; `query_quality_trend:249`; **lane 是自由字符串截断 20 字符（:219），`"replay"` 无需任何约束变更**; `CARTO_METRICS_STORE_ENABLED` 总闸 | 生产（fire-and-forget） |
| Ratchet CLI | `scripts/quality_ratchet_gate.py` | `baseline [--activate|--from-json runs.json]`（离线模式直接吃 `[{scene_id,check_id,value}]`）; `check`（违规 exit 1）; `waive` | 本地 gate |
| 像素 golden | `app/lib/cartography/golden_diff.py:146` | `compare_golden(...)`（±16/通道, ≥98%）; `golden_validation.py:41 validate_golden_pair` | 测试+nightly 9 场景 |
| JSON golden corpus | `tests/cartography/golden_corpus/`（`build_cases()` 模式） | 确定性、无浏览器 | `-m cartography` lane |
| Intent corpus | `tests/cartography/corpus_harness.py` | `load_corpus/evaluate_corpus`（300 条双语 intent 语料 + hit-rate 评分） | 测试 |
| Perf harness | `tests/benchmarks/test_perf_harness.py` | marker `perf`; median-of-7; WARN 1.75×/FAIL 4×; `PERF_UPDATE_BASELINES=1`; marker 隔离契约（#664） | CI perf lane |
| Offline guard | `tests/data/offline_guard.py` | `ADS_FORCE_OFFLINE=1` socket 阻断器 + scoped `offline_socket_guard()`（conftest autouse 已 arm） | tests |
| Fabric fake 服务 | `tests/data/fabric_fixtures.py` | `fake_source_server`/`patched_safe_sessions`（OGC/WFS/STAC/ArcGIS/PostGIS canned; host `ads-fixture.invalid`） | tests |
| Fault 注入先例 | `tests/data/test_ads4_fault_matrix.py` | 30 组 fault 矩阵（ADR-0174） | tests |
| geocompute replay 先例 | `app/services/geocompute/replay.py:31` | `replay_trace(events) -> {valid, violations, nodes, runs}` 确定性状态机不变量校验 | 生产模块/测试消费 |
| Exec bundle | `app/services/geocompute/reproducibility.py` | manifest/plan fingerprint; REPRODUCIBLE/STALE/... 分级 | 生产 |
| 失败分类先例 | `app/services/gis_harness/failure_taxonomy.py` + completion finding codes（completion/contracts.py:36-62） | triage 词表基础 | 生产 |

---

## 3. 关键契约（ReplayTrace 的字段来源）

- **ToolCallEvent**（tool_call_event.py:14, dataclass）: `tool_call_id, tool_name, arguments, duration_ms, is_error, error_msg, cache_hit, session_id, result, arg_bytes, result_bytes`。
- **ToolDispatchResult receipt**（tool_dispatch_service.py:156）: `status∈{ok,repeated,error}, llm_payload, slim_event, geojson_ref, raw_result, error_msg, map_actions[{action_id,command,requested}], ref_descriptor, background_job_ids`。
- **SessionPlan**（session_plan.py:67, pydantic; ADR-0076）: `envelope_id/session_id/user_goal/gis_chapter/progress[CapabilityProgress]/replaced/superseded/previous_goal/updated_at`。**master 无 schema_version/turns/steps**——#1277 以 additive 方式加（`schema_version/created_at/revision/turns/steps/decisions/recovery`）。
- **MapSpec lifecycle**: schema {1.0,1.1,1.2} additive-only（mapspec_schema.py:19）；`MutationIntent` 19-intent union（lifecycle_engine.py:954）；`MapSpecResult{is_compiled, cartography_findings, cartographic_review, mapspec_fingerprint, runtime_observation_seq, mutation_revision, superseded, error_code, origin}` :56；checkpoint snapshot/rollback（checkpoint.py:253/380）；`MapSpecStore._fingerprint_sync :142`。
- **World-state 读模型**: `build_world_state(session_id)`（gis_world_state/state.py:101）；变异事实 = map_state 键 `_cartographic_mutation_revision/_current_cartographic_fingerprint/_cartographic_observation/_provenance[ProvenanceEntry]`（provenance.py:31）。
- **TurnEvidence.to_summary**（evidence.py:268）: `correlation{request_id,session_id,turn_id,run_id}`, `outcome{outcome,failure_class,detail}`（Outcome: succeeded/failed/cancelled/superseded/partial/not_evaluated）, `timing_ms{...}`, `work{...}`, `llm_usage{prompt,completion,total,reports}`, `warnings[]`。
- **Completion verdict**: `MapCompletionResult`（completion/contracts.py:521）: `status∈{pending,needs_repair,complete,failed}`, `findings[code 词表 :36-62]`, `repairs_applied`, `render_status`, `final_map_status∈{verified,verified_with_degradation,failed,unknown}`, `product_verdict` 等。Stage 17 FINAL_VERDICT 持久化且防裁剪。
- **Cartographic review / visual judge**: `VisualJudgeReport`（visual_evaluator.py:112）: `status∈{evaluated,not_evaluated}, critiques[VisualCritique{dimension,severity,...}], mode, fingerprint, screenshot_digest`; L5 `derive_goal_satisfaction :505` → `{status∈pass/fail/not_evaluated, reason}`。
- **Gate result**: `evaluate_evidence` → `{overall_passed, metrics, thresholds, checks{name:{score,target,passed,evaluated,reason}}, run_id, session_id}`。
- **Artifacts/provenance**: `register_artifact(..., profile_digest)`（artifact_registry.py:454）; `compute_content_fingerprint(tool_name, tool_result)`（fingerprint.py:145）; `redact_provenance_args`（manifest.py:86，脱敏先例）。
- **18 阶段 Stage IntEnum**（gis_trace.py:30）: USER_INTENT→PARSED_INTENT→TASK_ONTOLOGY→DATA_PROFILE→CANDIDATE_WORKFLOWS→SELECTED_WORKFLOW→TOOL_SURFACE→MODEL_ROUTING→TOOL_CALLS→ARGUMENTS→TOOL_RESULTS→ARTIFACT_CREATION→MAP_MUTATIONS→MAP_OBSERVATION→VERIFICATION→REPAIR→FINAL_VERDICT→USER_OUTPUT。`GisTraceChain{turn_id, session_id, max_per_stage=8}`, `ChainRecord{stage,ts,payload}`, `completeness():111`。

---

## 4. 测试纪律与环境开关

- pytest: `asyncio_mode=auto`; `timeout=60 (thread)`; `addopts --cov=app`（迭代用 `--no-cov`）; markers `heavy/perf/cartography/real_services`。**`cartography` = 确定性发布闸：无 Node/Chromium/LLM/network——replay 测试的全部约束与之吻合**。
- xdist 可用（requirements-dev），近期线惯例 `pytest tests/unit -q -n 2`；CI 主 lane 串行带覆盖率。
- conftest env baseline ~180 键（USE_NEW_AGENT=false、USE_REDIS=false、sqlite、空 provider key、MAP_QUALITY_GATE_MODE=enforce 等）；autouse: auth-bypass pin、offline embedding、`ADS_FORCE_OFFLINE` socket guard、`QUALITY_ORDER_SEED`。
- 相关开关：`GIS_TRACE_PERSIST/GIS_TRACE_FSYNC/GIS_TRACE_COMPRESS`（trace_store.py:65-74）; `CARTO_METRICS_STORE_ENABLED`; `CARTO_VISUAL_JUDGE*`; `ADS_FORCE_OFFLINE`。
- 无 freezegun。确定性靠 fixture 语料 + 单调 revision + 注入式时钟（本线 replayer 自带注入时钟/确定性 id，见 decisions D6）。
- vendor/pi 子模块本地未初始化 → **任何需要 Pi 子进程的 replay 都不可行（测试环境），replay 决策重放而非 LLM 重生成**。
- 已知本地预存失败（hk1 ledger M7 记录）：`test_pi_integration.py::test_stream_prompt_emits_heartbeats_during_silence`（计时敏感）；frontend `use-sse-stream.test.ts`（本地 next-intl 解析）。Windows 无 fcntl → trace_store 降级进程锁。

---

## 5. 与 open/recent PR 的重叠矩阵

| PR / 线 | 触达面（相对 master diffstat） | 引入契约 | 对本线碰撞 | 本线预留的 versioned optional 字段 |
|---|---|---|---|---|
| #1270 fix/ci-adaptive-hygiene | 10 files（generated manifest + conftest 4 行 + mapspec CLI alias） | CI hygiene | **低** | 无 |
| #1273 quality/review-optimize-loop | 66 files（gis_harness planner/recipes/completion、cartography semantic_checks 等） | verdict 语义增强 | **中**（scorer 读 completion/verdict） | quality run `summary` 内 gate_scores 邻接键 |
| #1274 pi-typed-tool-surface | 17 files（pi_input_gate 新建、pi_native_surface、**agent_pi_bridge**） | surface byte budget、pre-dispatch gate、Stage.TOOL_SURFACE 载荷键 | **高**（同缝） | receipt: `validation{status,reason,schema_budget_dropped}`; turn `drops[]` |
| #1275 gis-situation-world-model | 27 files（新 `app/services/gis_situation/**`、chat.py env block 替换） | `SitFact`/`GISSituation`、复合 revision 三元组、schema JSON | **高**（情境是轨迹数据本身） | trace: `situation_revision{mutation,observation,interaction}` + sitfact status 词表 |
| #1276 gis-capability-graph | 24 files（qualification_v8/candidate_planner_v8 激活、`resolve_capabilities`） | 能力图四投影、C9 benchmark 先例 | **中** | chain CANDIDATE/SELECTED_WORKFLOW 内 capability node id |
| #1277 pi-native-kernel-sessionplan | 27 files（新 `app/services/harness_kernel/`、session_plan additive、**agent_pi_bridge**） | SessionPlan v2 additive 字段、PlanStep/StepEvidence、`session_plan_step` SSE | **高**（turn/step evidence 形状） | trace: step 对齐 `PlanStep`、`turns/steps/decisions/recovery` optional FIFO 引用 |
| #1278 gis-skill-procedure-library | 41 files（新 skill library） | `SkillContract`/procedure IR/resolver | **低-中**（procedure 可喂语料） | PARSED_INTENT/SELECTED_WORKFLOW 内 `skill_id` |
| #1279 resource-cost-governor | 39 files（新 `app/services/governor/`、**ToolDispatchService.dispatch 内 ~15 行**） | rg.v1 Estimate/Decision/Certainty、`GOVERNOR_MODE=observe` | **高**（receipt/成本会计同域） | receipt/turn: `governor{decision,certainty,defer_ms,degrade}`、`resource_usage` |
| #1269 merged ADR-0159 | 质量事实库/ratchet/golden | 上面 §2 | **复用基座** | lane 词表加 `replay` |
| #1271 merged AC-V11 | wave ratchet `aggregate_observations_by_wave` | wave 维度 | **复用**（benchmark wave 语义） | — |
| #1272 merged ADS-V1 | data_fabric matrix/facts、offline fixture/guard、FactsStore.ratchet_check | fixture/guard/ledger 模式 | **复用** | — |

**ADR 占号**：master 最高 0179；分支已占 0180×3 / 0181×1 / 0182×2。**0183 全网未占用** → 本线 claim `docs/adr/0183-harness-replay-benchmark-explainability.md`（独特文件名，与任何分支无文件冲突）。

---

## 6. 任务书假设 → 实际代码 → 调整后的实现

| 任务书假设 | 实际代码（执行时事实） | 调整 |
|---|---|---|
| "建立 trajectory-level regression system" 需要新 trace 体系 | 18 阶段 chain + trace_v6 已持久化（但有 64 条/session 窗口） | 不造第二套 trace；ReplayTrace 是**打包/规范化层**（补窗口丢失、加 verdict/cost/situation/governor 预留字段） |
| "在 production seams 加 recorder" | S13 settle 持久化点是唯一全量、确定性触达全部 18 阶段的点 | recorder 挂 settle 末尾单点调用（env-gated），不碰 dispatch 内部（避开 #1274/#1279 热区） |
| "offline replayer: mock/frozen tool responses; replay plan/evaluation" | vendor/pi 未初始化、tests pin USE_NEW_AGENT=false、无 LLM | replay **决策重放**（LLM 决策作为录制输入），重执行确定性下游（evidence→gate→verdict→mutation re-apply），分层 T1/T2/T3 |
| "把 trajectory metrics 接到现有 ratchet" | ratchet 已支持离线 `--from-json`；lane 自由字符串 | 投影成 `[{scene_id=scenario_id, check_id="replay.<dim>", value}]` + `record_quality_run(lane="replay")`；**不激活 baseline**（provisional 纪律） |
| "至少 100 核心场景" | golden corpus `build_cases()` 代码生成先例；ads 864 组矩阵先例 | 紧凑场景矩阵（代码内）+ 确定性展开器 → 提交展开 JSON 索引 + 校验测试（≥100 core、≥30 multi-turn、类别全覆盖） |
| "重放 compare semantic outputs" | TurnEvidence.summary/FINAL_VERDICT/MapCompletionResult/gate result 皆结构化 | exact 字段白名单精确比对 + 容忍度量阈值比对 + LLM 文本字段只做规范化摘要存在性检查（B3 三分类落地） |
| 方向 1/2/5/7 未合并 | #1274-#1279 均 open | 全部以 versioned optional 字段预留（§5 末列），不阻塞、不复制 |
| 方向 7 Goal Evaluator 若已合并直接消费 | 未合并（L5 `derive_goal_satisfaction` 在 master，属制图闭环） | 消费 master 的 `derive_goal_satisfaction` + gate result；#1277 合并后其 kernel step evidence 可作 trace 上游（optional adapter 预留） |

---

## 7. 复用 / 扩展 / 不做 / 新建 清单

**复用（零复制）**：Stage 1-18 + GisTraceChain/ChainRecord + trace_store V6（轨迹骨架与持久化）；TurnEvidence.to_summary（turn 包络）；PiAgentHarness + HarnessEvaluator（评分器）；cartography_ratchet 全套 + quality_ratchet_gate CLI `--from-json`；record_quality_run（lane="replay"）；golden_diff + JSON golden corpus；offline_guard + fabric_fixtures；failure_taxonomy + completion finding codes（triage）；perf harness 基线策略；`<line>-{recon,decisions,ledger}.md` 文档惯例。

**扩展（additive）**：`app/lib/harness/replay/**`（新子包）；`record_quality_run` lane 词表 +`replay`；agent_pi_bridge settle 块 +1 个 env-gated recorder 调用；SessionPlan/situation/governor optional 字段预留（仅 schema 注释与字段位）。

**不做**：不重放 Pi 子进程/LLM（vendor/pi 未初始化；测试 pin false）；不碰 `ToolDispatchService.dispatch` 内部（#1279 热区）；不碰 chat.py env block（#1275 热区）；不实现 typed tool surface/situation/capability graph/kernel 任何一部分；不激活 ratchet baseline；不给 legacy ChatEngine path 接 recorder（v1）；不加 CI workflow；不加迁移（无新表）；不动 #1270 的 CI hygiene。

**新建**：ReplayTrace v1 schema（打包层）；ReplayRecorder（settle 缝 env-gated 打包）；offline replayer（T1 证据级 / T2 变异级 / T3 dispatch 级分层）；scenario corpus 矩阵+展开器（≥100 core / ≥30 multi-turn / fault 注入规格）；fault injection 10 类；ratchet 投影器；explainability bundle（JSON+Markdown 因果链）；`scripts/replay_bench.py` CLI（suite/seed/offline/bounded-parallel/compare/only-failed/resume）；triage 分类器（六分类，禁止"snapshot changed"式裸报告）。

---

## 8. 风险登记

1. agent_pi_bridge settle 块是 #1274/#1277 热区 → 本线只加**单个 additive 函数调用**，合并冲突面 ≈ 1-3 行。
2. trace payload 可能含 prompt 片段 → recorder 强制 sanitize（见 decisions D5）：白名单字段 + 秘密/CoT 剥离 + 字节上限。
3. 64-record/session 窗口使生产 chain 有损 → recorder 在 settle 时刻（窗口裁剪前语义上之后但文件已写）读**当轮 chain 文件**打包；实测若窗口已裁剪当轮记录则降级 completeness 标注（不静默丢）。
4. Windows：fcntl 缺失 → trace_store 进程锁降级；replay 全部单进程内，无跨进程锁依赖。
5. 已知 master 本地预存失败（§4）不做门禁锚点；最终 PR 对照复跑归因。
6. perf marker 隔离契约（#664）：bench CLI 不进 pytest 收集路径（scripts/ 独立入口），不会污染 perf lane。
