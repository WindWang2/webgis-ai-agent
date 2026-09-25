# F09 — Trace / Replay Oracle v3 设计决策（ADR-0214 伴生文档）

前置：#1486（ADR-0212）已实现录制→重放闭环 v2。本线把「可回放」升级为「可判错」：
回放不只确认"没抛异常"，而是对录制时的事实给出**明确可失败的期望**，并把漂移
定位到具体 step / decision / receipt。勘察见 `f09-trace-replay-oracle-v3-recon.md`。

## D1 秘密净化下沉中立模块（WP8）

- 新 `app/lib/redaction.py`：`SECRET_KEY_MARKERS` / `SECRET_STRING_PATTERNS` /
  `is_secret_key` / `scrub_secret_strings`（自 replay/sanitize 原样搬移）。
- `replay/sanitize.py` 改为 `from app.lib.redaction import ...` + re-export（兼容面
  逐字不变）；`runtime/decision_record.py` 与 `runtime/gis_trace.py` 的 import 指向
  新家 —— 消除「生产 runtime 反向依赖测试 oracle 包」的方向性倒挂。
- fuzz/negative tests：种子化伪随机载荷 + 对抗秘密形态；不变量：idempotent、
  never-raises、任意输入不崩、注入秘密值经消毒后不出现在输出。

## D2 环境 fingerprint v2 + drift 分类（WP5）

- `recorder.collect_turn` 的 env 载荷 additive 扩展（`env_schema_version: 2`）：
  `python_version` / `platform` / `runtime_flags`（封闭白名单键 → bool）/
  `policy_versions`（capability_resolution / capability_dispatch_bind /
  plan_aggregate）/ `runtime_manifest_fingerprint` / `source_fingerprints`
  （复用 `capability_graph.source_fingerprints()`）。
- **封闭白名单纪律**：只收录影响行为的开关布尔（`GIS_CAPABILITY_DISPATCH_BIND` /
  `GOVERNOR_TOOL_SURFACE` / `GIS_ANALYSIS_REUSE` / `SPATIAL_GUARDRAILS` /
  `HARNESS_REPLAY_RECORD` / `CARTO_VISUAL_JUDGE`），绝不收录任意 env 值。
- `drift.env_drift(recorded, current)`：键级 diff → 分类（`registry` / `policy` /
  `runtime_flags` / `manifest` / `sources` / `runtime`），`behavioral: bool`
  （policy/flags/registry/sources 变化 = 行为面；python/platform 补丁版本 = 环境面）。
  drift ≠ fail，但「裸 hash 变了」升级为「哪一类环境事实变了」。
- `capability_registry_digest` 按 graph 指纹记忆化（与 graph 缓存同键），修录制
  热路径 ~255ms/turn 的重复投影；graph 对象身份或指纹变化即失效。

## D3 recorded 场景 expect 回填（WP2，去 green-by-construction）

- `build_trace` additive 新顶层字段 `dispatch_evidence`（≤16 条 bind 证据，id/code
  级、无参数无凭证）—— 否则逐 call 的 allowed 证据在 trace 里缺席，expect 无从回填。
- `roundtrip._trace_turn` 从录制事实派生 expect（`expect_source: "recorded"` tag）：
  - `gate.checks.<name>.evaluated/passed` ← `verdict.final_verdict.checks`；
  - `goal.status` ← `verdict.map_product.task_complete` / `outcome.outcome`
    （task_complete=True → "pass"；否则不造 pass 假期望，只钉 evaluated 面）；
  - `dispatch.<call_id>.allowed` ← `dispatch_evidence`（按 tool 名 + 出现序 join
    tool_calls；refused 带 capability）；
  - `decisions.selected` ← 决策索引（每条 decision 钉 selected 面摘要）。
- 纪律：**只期望录制时成立的事实**；证据缺席的维度不进 expect（不伪造）。
  多轮场景逐 turn 独立派生。场景 tag 追加 `expect_recorded`。

## D4 per-tool governor estimate/actual 入链（WP3）

- `TurnEvidence.add_resource_usage(entry)`（有界 ≤16 FIFO，threading.Lock 内）；
  `to_summary()` 新增 `resource_usage` 键。
- `dispatch_adapter.run` finally：把 `{tool, subsystem, attempt, status,
  estimate: {resource_class, wall_s, dims…}, actual: {wall_time_s, dims},
  ratio_wall}` 投影进 `TURN_EVIDENCE.get(turn_id)`（turn_id 缺席退回
  current_turn_evidence；都缺席 = 诚实丢弃）。观测面，绝不阻断、绝不抛。
- `build_trace` 填充预留 `trace.governor`：`{schema_version, entries(≤16),
  plan_cost_delta: {estimated_wall_s, actual_wall_s, ratio}}` —— turn 级 cost
  delta 定位资源策略漂移；entries 全部有界（wall 取整 ms、dims 白名单键）。
- metrics：`project_metrics` 新增 `replay.resource_entries` /
  `replay.plan_cost_ratio`（tolerant ratchet 行，不进 digest）。
- 录制场景 expect 可钉 `resources.<toolN>.recorded`（结构存在性，exact）；
  数值走 ratchet 行（计时天然抖动，绝不进 exact/digest）。

## D5 legacy 录制面统一（WP4）

- `execution_engine.py` 两个 settle 点（`chat()` 与 `chat_stream()` 的
  `emit_turn_summary(rt_ev)` 之后、`TURN_EVIDENCE.remove(turn_id)` 之前）各插入
  与 bridge 同款的 4 行 try/except `maybe_record_turn(...)`。
- legacy 无 map_product 传递 → `map_product=None`（degraded 诚实录制，recording
  tag 已有 `degraded` 语义）；final_text 就近取值，取不到传空串。
- env 总闸 `HARNESS_REPLAY_RECORD` 默认关 → 零开销语义不变。

## D6 receipt 级 ToolDispatchService 重放 T4（WP1）

- 新 `replay/receipt.py`：
  - `_RecordedProviderRegistry`：duck-typed registry —— `metadata(name)` 返回
    fixture 声明（capabilities/cost），`dispatch(name, args, session_id)` 回放
    canned raw result（由录制 receipt 重建：ref/error 形状；**绝不调用真实
    provider**）。
  - `_MemorySessionStore`：SessionStoreProtocol 最小实现（store/append_event/
    get_ref_descriptor/get_ref_descriptor_authorized/…），进程内 dict，有界。
  - `_dispatch_sandbox`：env 作用域 —— `GIS_ANALYSIS_REUSE=0` /
    `GOVERNOR_TOOL_SURFACE=0` / `SPATIAL_GUARDRAILS=0` /
    `GIS_RECOVERY_LEDGER=0` / `MAPSPEC_STORAGE_DIR=tmp`；monkeypatch
    `app.services.session_data.session_data_manager` 与
    `app.services.tool_dispatch_service.session_data_manager` 为内存替身；
    退出恢复。重放 session id 沿用 `rsess`/`rmut` 种子化纪律。
- `ToolDispatchService.__init__` 提 `session_data_manager` 为可选依赖
  （None = 既有模块级单例，生产零行为变化）—— 这是 T4 唯一的生产接线。
- 比对面（全部钉进 `expect.receipt.<call_id>`）：`status`（ok/error/repeated）、
  `geojson_ref` 与录制 ref 一致（内存 store 重建后回读）、`error_code`（错误
  折叠契约）。T4 覆盖 dispatch 合同：dedup（同参二次调用 → repeated）、
  capability bind 拒绝（fixture registry 声明 + 真实资格图）、错误折叠
  （error receipt → status=error + code）。
- `deferred_levels` 语义更新：T4 实装后 dispatch_backed 场景不再默认 deferred；
  仅当 receipt 重放不可行（依赖缺席等）时诚实披露 `receipt_redispatch_unavailable`。
- digest 纪律：T4 结果进 `ScenarioResult.replay_receipt`（status/ref/code 结构
  投影）并入基线比对；计时绝不进 digest。

## D7 differential replay（WP6）

- `write_baseline` additive 扩展 entries：`gate`（check name → passed/evaluated）、
  `mutations`（op/success/fingerprint）、`dispatch`（call_id → allowed/capability）、
  `decisions_digest`（已有）、`governor`（cost delta 摘要，D4 后）。
  `baseline_file_shape` 测试同步放宽（additive-only 向后兼容旧基线）。
- `bench.diff_reports(baseline_path, current_path)`：场景级 `digest_drift` →
  下钻结构化 delta，逐条带 `scenario/turn/aspect/key` 定位：
  - `gate_check_flip`（name, baseline_passed → current_passed）
  - `mutation_fingerprint_changed`（op, baseline_fp → current_fp）
  - `dispatch_allowed_flip`（call_id, baseline → current）
  - `decision_diff`（复用 `diff_decisions`：selected_changed / alternatives /
    added / removed，decision_id 对齐）
  - `goal_status_changed` / `receipt_status_changed`
- CLI `--diff <master.json>`：与 `--baseline` 可并用（diff 报告输出到 stdout/
  文件）；exit code 遵循既有 drift 纪律（有 delta → 1，明确指引）。

## D8 failure trace minimization（WP7）

- 新 `replay/shrink.py`：`shrink_scenario(scenario, oracle)` —— oracle =
  `async replay(scenario) -> bool（仍红）`；delta-debug 维度（优先级序）：
  1. turns 后缀裁剪（保留首个失败 turn 之前缀）；
  2. turn 内 ops 删除（分块折半 → 单元素）；
  3. faults 条目删除；
  4. mutations 条目删除。
- 有界：`max_rounds`（默认 48）+ `max_candidates`（默认 256）—— 绝不无界；
  确定性（固定分块策略，无随机）；结果 = 最小红场景 + `removed` 收据
  （被裁掉的元素清单，可审计）。
- CLI `--shrink <scenario_id>`：跑该场景，若红 → shrink → 打印最小复现 JSON。
- shrink 是纯消费侧：不改 replayer 语义，不改语料。

## 兼容性

- ReplayTrace：`dispatch_evidence` / `governor` / `env.*` 全部 additive；
  from_dict 未知字段保留纪律不变；behavior_digest 对新键自然纳入（录制新
  trace 才有，旧录制件 digest 不变）。
- Scenario：`expect_source`/`receipt` 为 additive 字段；corpus 140 场景文件
  不变 → committed baseline 不变。
- ToolDispatchService：唯一生产改动 = ctor 可选依赖（缺省行为逐位不变，
  测试钉死）。
- TurnEvidence.to_summary()：新增 `resource_usage` 键（结构化日志 additive；
  既有消费方按键读取不受影响）。

## Out of Scope

- 前端 inspector UI；LLM 重生成式重放；跨进程分布式重放；
  真实 provider 的网络级 fixture 录制（VCR 类）；
  收据级重放对 MapSpec 写路径的深度验证（沙箱内 lifecycle 属 T2）。
