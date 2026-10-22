# G05 勘察报告：WebGIS Agent 真实制图任务级端到端验收基础设施

基线：origin/master = ee8c14f8（PR #1758 已并入），分支 feat/g05-harness-task-acceptance-vnext。
本文所有断言均已用 ripgrep/Read 在本 worktree 实测核实（`路径:行号`）。

## 0. 核心结论（先读）

1. **G05 的"task-level 场景→断言链"所需的全链路构件已存在且版本化齐全**：MapSpec（TypedDict 快照 + revision store）、RequirementIR（requirement_document.v1）、GoalSatisfaction（goal_satisfaction.v1，含 `not_evaluated` 语义）、RenderWorkEnvelope（render_work.v1）、Cartographic Product Manifest（cartographic_product_manifest.v1）、Interaction IR（interaction_ir.v1）。G05 应做**投影/断言层**，不是第二 oracle。
2. **离线重放基建成熟但层位低于 G05 目标**：`app/lib/harness/replay/`（T1 证据级/T2 变异级/T3 bind-gate/T4 receipt 级，141 条场景 fixture）重放的是"确定性管线决策面"，不含真实 NL→intent→plan 的 task 级链；#1755 的 33 个 h02 场景是**导出语义 conformance**（spec→verify_status），需 G05 做 task 级投影。
3. **无 tests/acceptance 目录**（`ls tests/` 无此项，实测），G05 新增 `tests/acceptance/gis_agent/` 零命名冲突。
4. **与 #1759/#1760 文件冲突风险集中在 16 个生产文件**（见 §6）；G05 计划的 tests/acceptance/、scripts/、docs/quality/ 新增均无冲突；`pytest.ini`（markers）与 `tests/conftest.py`（env 基线）是仅有的两个可能被迫修改的共享文件。
5. **全量后端 suite 有存量挂起（#1753：串行 ~80% 永久挂起）**，G05 一律走定向 lane（`pytest tests/acceptance/gis_agent -m ...`），与 h01/h02 PR 同策略（docs/dev/h01-pr-body.md:38）。

## 1. 真实入口调用链（用户消息 → 最终地图/答复）

```
POST /api/v1/chat/stream (SSE)                      app/api/routes/chat.py:1113
  └─ _ensure_pi_bridge_available → PiBridgePool     app/api/routes/chat.py:698; app/agent_pi_bridge.py:3044,3084
  └─ PiBridge.stream_prompt（Node Pi 子进程）       app/agent_pi_bridge.py:2370（prompt:1924）
      └─ Pi 扩展回调 POST /api/v1/pi-tools/execute  app/api/routes/pi_tools.py:51-52
          └─ PiBridge.dispatch_tool                 app/agent_pi_bridge.py:468
              └─ kernel begin_step                  app/agent_pi_bridge.py:590 → app/services/harness_kernel/runtime.py:805
              └─ _get_shared_dispatch_service       app/agent_pi_bridge.py:263 → ToolDispatchService
  └─ ToolDispatchService.dispatch                   app/services/tool_dispatch_service.py:259(class),301(dispatch)
      └─ ToolRegistry.execute                       app/tools/registry.py:473(class ToolRegistry)
          └─ gis_harness 工具注册                   app/services/gis_harness/tools.py:255(register_gis_harness_tools)
              ├─ webgis_map_intent（MapIntentArgs）  app/services/gis_harness/tools.py:32
              └─ webgis_map_product（MapProductArgs）app/services/gis_harness/tools.py:42
  └─ 规划链（确定性，无 LLM）
      ├─ MapProductPlanner（Intent→Recipe→Capability→Algorithm→Plan）app/services/gis_harness/planner.py:669；MapProductPlan:594
      ├─ compile_workflow / WorkflowCompilation     app/services/gis_harness/workflow_compiler.py:133,76
      ├─ compile_workflow_v4（typed DAG）           app/services/gis_harness/workflow_v4/compiler_v4.py:94
      └─ 数据资格 qualify_data_role / DataQualification  app/services/gis_harness/data_qualification.py:402,177
  └─ turn 结算 settle_turn（E1..E6 效果编排）        app/services/harness_kernel/settle.py:597(settle_turn),322(run_settle_effects),211(HarnessSettleInput)
      ├─ maybe_finalize_map_product                 app/services/gis_harness/completion/pipeline.py:1040
      │    （task_complete + goal_satisfaction 投影 pipeline.py:914,1501）
      └─ finalization_sse_payload → SSE map_product 披露
  └─ 前端渲染/ACK 闭环
      ├─ RenderWorkEnvelope（后端投影）             app/lib/cartography/render_work_envelope.py:44,116
      ├─ get_render_work_envelope / emit            app/services/render_work_supply.py:190,96
      ├─ 前端镜像 + apply/ACK                        frontend/lib/render-protocol/render-work.ts:19（RENDER_WORK_SCHEMA_VERSION='render_work.v1'）、render-apply-ack.ts
      ├─ POST /chat/sessions/{id}/cartographic-observation  app/api/routes/chat.py:1769
      └─ POST /chat/sessions/{id}/map-action-ack     app/api/routes/chat.py:2178（_persist_map_action_acks_locked:2113）
  └─ 导出（含 Product Manifest）                    app/api/routes/map.py:368-400（verify_export_semantic_consistency → {filename}.manifest.json sidecar）
```

要点补充：
- **Pi 路径不走 legacy 规划链**：Pi 用单一 `webgis_execute` 代理工具 + 原生 GIS 工具，`CanonicalPlan/decision_log` 仅 legacy 路径（chat.py:1241 架构注记，#726 裁决）。`webgis_execute` 定义在 Pi 扩展 `app/extensions/webgis-tools/index.mjs:34,294`。
- **harness_kernel**：`GISSessionRuntime`（runtime.py:583；get_runtime:1605）管理 begin_turn:600/end_turn:697/begin_step:805；`settle_turn` 幂等（同键重放收据）、stale 防护、E1..E6 never-raise（settle.py:597 docstring）。
- **完成判据**：GoalSatisfaction 是"任务是否完成"的独立裁决面——不是第二个 L5；证据分级红线 `deterministic > structural > visual/assisted`，visual 证据不能单独判 fulfilled（goal_satisfaction/contracts.py:1-17 docstring）。`evaluate_goal_satisfaction` 唯一纯函数评估点（evaluator.py:524）。**当前生产接线薄弱**：调用方仅 `app/evaluation/goal_satisfaction_corpus.py:25,1099` 与单测（rg 实测），settle 面经 completion/pipeline.py:1501 的 goal satisfaction 投影随 task_complete 持久化。
- **最终答复文本**：Pi 回 result.final_text → chat.py `_persist_pi_transcript`（chat.py:1293，on_turn_result 回调 :1408）落 assistant 消息；失败语义走 HonestTurnFailure→502（chat.py:1092-1102）。
- **Legacy 路径**（USE_NEW_AGENT=0 时）：ChatEngine（app/services/chat_engine.py），有独立规划链，G05 不应依赖。

## 2. 现有测试/评测资产盘点（复用而非重造）

### 2.1 Replay Oracle v3（tests/harness_replay/ + app/lib/harness/replay/）
- **库**：`app/lib/harness/replay/__init__.py` 模块清单；`schema.py:45 ReplayTrace`（18 阶段证据链打包，schema_version=1，additive-only）；`replayer.py:508 OfflineReplayer`、`:533 replay_scenario`、`:44 SCENARIO_SCHEMA_VERSION=1`、`:102 Scenario`（turns/ops/mutations/expect/dispatch_backed/receipt_backed/tool_registry）。
- **层级**：T1 证据级 / T2 变异级 / T3 bind-gate / T4 receipt 级真实 dispatch 沙箱重放（ADR-0262 D6 实装）；声明未跑→`not_run` 诚实翻红（replayer.py:564-572）。
- **场景语料**：`scenarios.py` 生成器（≥100 core 单轮 + ≥30 多轮，17 类别，tests/harness_replay/test_replay_corpus.py:21-50 钉死）；fixture 提交在 `tests/fixtures/replay/scenarios/`（141 个 JSON，实测计数），与生成器逐字节 pin（test_replay_corpus.py:52-66）。
- **测试文件**：test_replay_corpus / test_replay_replayer / test_replay_faults（10 类故障注入）/ test_replay_ratchet / test_roundtrip_closed_loop / test_receipt_level_replay 等 16 个。
- **运行**：`pytest tests/harness_replay/ -m cartography --no-cov`（文件内 `pytestmark = pytest.mark.cartography`）；CLI `python scripts/replay_bench.py`（suite/seed/offline/compare/baseline，scripts/replay_bench.py:26 main）。
- **G05 关系**：ReplayTrace 已含 `mutations/artifacts/verdict/outcome/llm_usage`——G05 场景断言可从 trace 取证，但**不得改 replayer 语义**（不做第二 replay oracle 红线）。

### 2.2 science oracles（tests/science_oracles/）
- 结构：`tests/science_oracles/_contract.py`（数据集契约：非空/唯一/expect.kind 合法）+ `test_oracle_replay.py`（`all_domains()` glob 全量重放）+ 按域 12 个文件（crs_units/edge_cases/geodetector/geostat/network/point_pattern/regression/sar/science_v4/v5/spectral/statistics/terrain）。
- 数据在 `tests/science_oracles/data/`；生成脚本 `scripts/gen_science_oracles.py`、`scripts/regen_science_oracle_manifest.py`。
- 覆盖：GIS 科学计算算法级 oracle（exact/allclose/error），非 agent task 级。

### 2.3 Cartography golden corpus（#1755 已并入，commit 812e594b）
- **结构化 golden 主库**：`tests/cartography/golden_corpus/`（corpus.py 生成 ≥500 用例；test_golden_corpus.py:37 钉规模；`goldens/` 578 个 JSON，实测计数；刷新需 `GOLDEN_CORPUS_UPDATE=1`）。
- **h02 导出 conformance 33 场景**：`tests/cartography/golden_corpus/h02_scenarios/scenarios.json`（positive_count=20 + negative_count=13 = **33**，schema 键齐全）+ `generate.py`；消费端 `tests/cartography/test_h02_golden_factory.py:29-31`（每场景走 build_product_manifest/verify_export_semantic_consistency/thematic spec_to_paint，期望 verify_status∈{pass/warning/fail/unsupported/not_evaluated}）；TS 侧 `frontend/lib/map-kit/h02-scenarios.compile.test.ts` 同源消费。
- **定位**：低层导出 conformance（MapSpec JSON→manifest 语义验证），**无 NL 意图、无规划、无渲染执行**——G05 需要在其上加 task 级投影（用户目标→…→manifest 终态）。
- 其他：render_work_corpus（contract_manifest.json + render_work_golden.json + generate.py）、carto_ir_corpus、representation_corpus、fixtures/、golden/（mutation_parity_corpus.golden.json）。
- 运行：`pytest -m cartography --no-cov --timeout=120`（scripts/ci-local.sh:104-105 的 cartography-smoke lane 同款）。

### 2.4 harness_kernel completion 判据测试
- `tests/test_harness_kernel_e2e_scenarios.py`：**7 个最小验收场景**（S1 成都小学→plan 生命周期 / S2 同信封 followup / S3 失败重试不重复副作用 / S4 SSE 断线重连状态单调 / S5 Pi 重启会话恢复 / S6 legacy host parity / S7 MapSpec+cartography 共存无死锁），行号 133-385；走**真实 Pi dispatch 缝**（dispatch_tool，无 LLM/无 Pi 子进程）；数据依赖注记：query_local_poi 依赖本机 gd_pois.gpkg，能力/失败链用 heatmap_data inline geojson（文件头 docstring 1-28 行）。**这是 G05 task 场景 runner 的最近亲**。

### 2.5 其他既有 fixtures
- `tests/fixtures/gis_samples.py`：确定性合成样本（point_fc:31/polygon_fc:50/line_fc:74/bad_crs_fc:101/invalid_geometry_fc:108/missing_field_fc:124/zero_variance_fc:133/temporal_fc:141/tiny_raster:163；成都 bbox 内，ADR-0245 Wave 18）。
- `tests/fixtures/pi_mocks.py`：Pi 子进程 I/O mock（make_readline:13/make_mock_process:26/事件工厂 63-141）。
- `tests/fixtures/replay/baseline.json`（replay bench 基线）；`tests/fixtures/chaos.py`（故障注册表）。
- `tests/cartography/fixtures/`、`tests/fixtures/product_corpus/`、`dataset_semantic_corpus/`、`export_semantic_corpus/`。

## 3. 可替换 fake LLM / 工具 / dataset provider

- **Pi 路径 LLM 在 Node 子进程内**——Python 侧测试不打 LLM，而是打 **dispatch 缝**（`dispatch_tool`）或 **HTTP pi-tools 回调**：
  - `tests/test_harness_kernel_e2e_scenarios.py` 的模式：`set_tool_registry(ToolRegistry)` + init_tools + 真实 dispatch_tool（见 §2.4）。
  - `tests/fixtures/pi_mocks.py` 的 mock process 工厂（伪造 Pi stdout 行协议）。
- **ScriptedToolProvider + FaultInjector**（执行面 fake，最可复用）：`app/lib/harness/lab/fakes.py:73 ScriptedToolProvider`（(tool,args) 精确匹配→cursor 回退的脚本化收据）、`:160 FaultInjector`（latency/timeout/disconnect/cancel/malformed_result/duplicate_event/restart 故障矩阵）、`:21 LabClock`（虚拟时钟）；契约测试 `tests/lab/test_lab_fakes.py`；lab runner/spec/settlement 在 `app/lib/harness/lab/{runner,spec,settlement}.py`。
- **T4 receipt 沙箱**：ToolDispatchService 构造参数 `session_data`（ADR-0262 D6，tool_dispatch_service.py:274-281）允许注入进程内沙箱替身做零副作用重放——G05 失败注入/跨会话恢复测试可直接复用该缝。
- **dataset provider**：`tests/fixtures/gis_samples.py`（合成 FC/raster）；`tests/data/fabric_fixtures.py`（fake_source_server/patched_safe_sessions，tests/conftest.py:497 引入）。
- **legacy LLM provider fake**（若需要 legacy 面）：tests/conftest.py 已钉 `LLM_API_KEY=your-api-key-here` 等假 env（conftest.py:28-30）；chat_engine 系测试自带 mock，G05 不应走此面。

## 4. Python 测试环境

- **配置**：`pytest.ini`（rootdir 级，非 pyproject [tool.pytest]）：testpaths=tests、pythonpath=.、asyncio_mode=auto、**timeout=180 timeout_method=thread**（:6-7）、默认 addopts 带 `--cov=app`（:19）；markers：heavy/perf/**cartography**/real_services（:22-27）。
- **解释器/安装**：无 uv/poetry 锁；requirements.txt→requirements.lock（uv 编译）+ .venv；requires-python >=3.12,<3.14（pyproject.toml:8-20）；ci-local.sh 把 `.venv/bin` 前置 PATH（scripts/ci-local.sh:27）。本机 .venv 曾漂移 3.14.7（pyproject 注记，勿迁就）。
- **运行命令**（与 .github/workflows/production.yml 逐字对齐，tests/test_ci_local_gate_contract.py 钉住）：
  - `scripts/ci-local.sh`（全量门禁）或 `--fast`；后端 lane：`pytest --cov-fail-under=75 -m "not perf and not cartography and not real_services" -q`（ci-local.sh:97-98）。
  - cartography lane：`pytest -m cartography --no-cov --timeout=120 --timeout-method=thread -q`（ci-local.sh:114-115）。
  - quality 车道：`scripts/quality` → `scripts/quality_runner.py`（scripts/quality:1-3）。
- **已知挂起**：#1753 全量串行 ~80% 永久挂起（docs/dev/h01-pr-body.md:38；h02 PR 同样绕行，docs/dev/h02-pr-body.md:34）→ **G05 一律定向 lane + 显式 --timeout**，不得触发全量。
- **契约红线**：ruff（app/tests/scripts 全域，`ruff check app/ tests/ scripts/ main.py manage.py`，ci-local.sh:40）+ mypy allowlist ratchet + import boundaries（ADR-0216，scripts/check_import_boundaries.py）——G05 新增 scripts/ 自动进 ruff 面（ci-local.sh:22-24 注记）。

## 5. 缺口分析（G05 目标 vs 现状矩阵）

| G05 目标 | 现状 | 缭口 |
|---|---|---|
| task-level 场景（NL 目标→数据→方法→MapSpec→渲染/导出→答复） | replay corpus 是**管线级**（ops/mutations 脚本）；h02 是**导出语义级**；kernel e2e 是**7 个最小场景** | **全缺**：没有"用户 NL→最终答复+地图产物"逐环节断言的场景 DSL（ScenarioContract）与 20+ 场景库 |
| 多轮 | replay 有 ≥30 多轮（turns 3-8，test_replay_corpus.py:27-29）；kernel S2 有 followup | 多轮均在管线层；task 级多轮（澄清/追加约束/改图）场景缺 |
| 跨会话恢复 | kernel S5 Pi 重启恢复（test_harness_kernel_e2e_scenarios.py:298）；resume_anchor.py/recovery_ledger.py 存在 | task 级"中断→恢复→仍满足 goal_contract"验收缺 |
| 失败注入 | replay 10 类 fault（test_replay_faults）；lab FaultInjector 8 类执行面故障 | 注入面齐全但**归因到 task 失败语义**（哪环节坏→taxonomy 归因）缺 |
| 判错归因 taxonomy | HarnessFailureClass 11 类 + typed remediation（app/services/gis_harness/failure_taxonomy.py:44,21-35）；turn_journal 因果树（app/services/turn_journal/diagnostics.py build_causal_tree_sync；scripts/turn_journal_inspect.py） | 分类词汇是**工具执行面**的；缺"task 级失败归因"（intent/plan/data/render/export/答复 哪环）taxonomy 与断言语法 |
| not_evaluated 语义 | RequirementState/GoalVerdict 均含 NOT_EVALUATED（goal_satisfaction/contracts.py:38-52；裁决缺证据时默认值 :214）；h02 manifest verify_status 含 not_evaluated | 已有词汇；缺 G05 场景对 not_evaluated 的**期望声明与统计口径** |
| 覆盖热区图 | replay bench 有 metrics/ratchet（app/lib/harness/replay/{metrics,ratchet}.py）；cartography ratchet | 缺 task 级 coverage 报告（场景×链路环节矩阵）与本地 run/compare/report CLI |
| 本地 CLI | scripts/replay_bench.py（compare/baseline/only-failed）、scripts/quality、scripts/gis_bench_v2.py | 缺 G05 专属入口（场景运行/对比/报告），应薄壳复用 replay_bench 形态 |

**#1755 场景的层次判定**：33 个 h02 场景输入是 MapSpec JSON（scenarios.json 的 spec.layers/layout），断言 verify_export_semantic_consistency 的 status——属"给定 spec 的导出 conformance"。G05 需要**更高层 task 投影**：场景输入是用户 NL 目标+数据承诺，断言链覆盖 intent→plan→MapSpec 生成→（导出时才出现的）manifest 终态。h02 可作为 G05 导出环节的**期望源**（复用 spec→status 的判定函数，不复制场景）。

## 6. 与 #1758（已并）/ #1759 / #1760 的文件交叠矩阵

#1758（ee8c14f8，swarm-review-fixes）：已并入基线，无冲突面；其改动含 app/agent_pi_bridge.py(+8)、workflows 等（git show --stat 实测）——G05 基于 #1758 之后的状态开发即可。

#1759（宣称改动，均已实测存在）：app/services/dataset_semantics/{builder,context_bridge,projections,provenance,qualification_bridge,reuse,store}.py、app/services/gis_harness/data_qualification.py、workflow_compiler.py、workflow_v4/、app/services/project_runtime/{contract,freeze,promotion,rebind,resume,store,validation}.py、app/tools/semantic_tools.py。
#1760（宣称改动）：app/lib/gis/{algorithm_resolver,scientific_preconditions}.py（存在）；**app/lib/gis/method_use_contract.py 与 science_final_gate.py 在本基线 MISSING（实测）→ 系 #1760 新增文件**；app/services/gis_harness/{planner,product_compiler,product_runtime,tools}.py、app/services/workflow_engine.py、app/tools/registry.py。

| G05 计划面 | 当前内容 | 冲突 | 风险与对策 |
|---|---|---|---|
| tests/acceptance/gis_agent/（新增） | tests/ 下无 acceptance 目录（实测） | **无** | 纯新增。注意 tests/test_pytest_module_name_uniqueness.py 与 #1702 教训：模块名必须唯一 |
| scripts/ 新入口（如 g05_acceptance.py） | scripts/ 已 60+ 脚本，无同名 | 无（命名避开 gis_bench_v2/replay_bench） | 会进 ruff scripts/** 通配（ci-local.sh:23-26）——首提交即过 ruff |
| docs/quality/ 新文档 | 生成物目录（README.md:5-7） | 低 | 手写 md 不进生成物清单（gen_quality_manifest 只扫登记面），命名加 g05- 前缀 |
| planning/g05/（本文件） | 新增 | 无 | — |
| **共享文件被迫修改面** | | | |
| tests/conftest.py（497 行，env 基线） | conftest.py:16-80 _ENV_BASELINE | **中**：G05 若需钉新 env（如 G05_* 开关）须追加键；#1759/#1760 若也加会 textual 冲突。对策：尽量用场景内 monkeypatch/setdefault，不动 _ENV_BASELINE |
| pytest.ini（markers） | markers 4 个（:22-27） | **中**：G05 新 marker（如 acceptance）需登记；与 PR 交叠概率低但存在。对策：优先复用 cartography marker，避免新 marker |
| tests/harness_replay/（高层投影只读复用） | 16 测试文件 | 低：若 G05 只 import 其库/fixture 则零改动；若加"task 投影"测试文件进该目录需与 #1759（不动此目录）无冲突，但与 replay 维护者可能语义竞争 | 对策：G05 投影测试放 tests/acceptance/gis_agent/，**只读引用** app/lib/harness/replay/* |
| tests/test_harness_kernel_e2e_scenarios.py | S1-S7 | 低：G05 不改它，新场景独立成文件 | 复用其 registry/sid fixture 模式（拷贝模式而非 import 私有 fixture） |
| pyproject.toml | mypy/ruff 配置 | 低：G05 新增文件需进 mypy allowlist 评估；scripts 通配已覆盖 | 若需登记 allowlist 则小冲突 |

**G05 绝对禁区**（任务书+#1759/#1760 面）：dataset_semantics/、project_runtime/、semantic_tools.py、algorithm_resolver/scientific_preconditions（+PR 新增 method_use_contract/science_final_gate）、planner/product_compiler/product_runtime/tools/data_qualification/workflow_compiler/workflow_v4、workflow_engine.py、registry.py、agent_pi_bridge.py 热路径、dispatch 热路径（tool_dispatch_service.py）。实测确认这些全部位于 #1759/#1760 改动清单内或生产热路径。

## 7. MapSpec / RequirementIR / RenderWork / Manifest 序列化格式与取回

| 产物 | 权威定义 | 版本/revision | 取回方法（从 session/trace） |
|---|---|---|---|
| MapSpec 快照 | `MapSpecSnapshotPayload`（TypedDict, total=False）app/services/mapspec/intents.py:121；层/源/组件 payload :36-99 | spec 内 `version`；store 侧 `_cartographic_mutation_revision`（单调，mapspec/store.py:280,409-410）+ revisions/ 磁盘快照 :323 | `mapspec_store.get_mapspec(session_id)`（adapter app/services/mapspec_store.py:1-4 委派 lifecycle engine）；store 类 mapspec/store.py:166；ReplayTrace.mutations/artifacts（schema.py:70-72） |
| RequirementDocument | `MapRequirementSpec`/`GISIntentSpec`（pydantic v2, extra="forbid"）app/services/gis_harness/requirement_ir/contracts.py:315,264 | `requirement_document.v1`（:33 REQUIREMENT_DOCUMENT_SCHEMA；:372 schema_version 字段） | 持久化经 service.py（load_state/save_state:122-138，挂在 session store map_state）；`load_state(session_store, session_id)` |
| GoalContract/Report | goal_satisfaction/contracts.py:157（schema_version=goal_contract.v1） | `goal_contract.v1` / `goal_satisfaction.v1`（:24-25） | 纯函数 `resolve_goal_contract`/`evaluate_goal_satisfaction`（evaluator.py:144,524）现算；持久化在 map_product["goal_satisfaction"] 块（projection.py:39 goal_line_from_block；completion/pipeline.py:1501） |
| RenderWorkEnvelope | `RenderWorkEnvelope` dataclass app/lib/cartography/render_work_envelope.py:116（DataDependency:65/LayerWork:79/ComponentWork:90） | `render_work.v1`（:44；前端镜像 frontend/lib/render-protocol/render-work.ts:19） | `get_render_work_envelope`（app/services/render_work_supply.py:190，进程内缓存+state 投影）；确定性 work_id/work_fingerprint（无时间戳） |
| Cartographic Product Manifest | `build_product_manifest`/`verify_export_semantic_consistency` 纯函数 app/lib/cartography/product_manifest.py:125,243 | `cartographic_product_manifest.v1`（:24） | 导出时落 `{filename}.manifest.json` sidecar（app/api/routes/map.py:386-400）；G05 断言读该 sidecar 或直接调纯函数 |
| Interaction IR | interaction_ir/contracts.py:33 SCHEMA_VERSION=`interaction_ir.v1`；kind 词表+合并策略+authority | 事件信封 schema_version:258 | ingest/projection/replay（interaction_ir/{ingest,projection,replay}.py；replay_scenario:49）；replay_corpus 在 tests/unit/gis_harness/interaction_ir/ |
| ReplayTrace（取证容器） | app/lib/harness/replay/schema.py:45 | schema_version=1 additive-only | recorder（settle 缝 env-gated）；G05 从 trace.mutations/verdict/outcome 取数 |
| TurnEvidence | app/lib/runtime/evidence.py:119（registry:365） | outcome/timing/work/llm_usage 摘要 | settle 时刻打包进 ReplayTrace（schema.py docstring） |
| GisTraceChain（18 阶段） | app/services/gis_harness/trace.py:53-97（STAGE_FINALIZATION 等） | 阶段常量 | `as_dict()` 进 ReplayTrace.chain（schema.py:5 docstring、:63 chain 字段） |

## 8. 建议的 G05 模块切分与缝合点

**纯新增（零交叠）**：
1. `tests/acceptance/gis_agent/`（或镜像 planning/g05 命名）：ScenarioContract DSL（用户 NL 目标/数据承诺/方法期望/MapSpec 期望/导出与答复期望/not_evaluated 声明）、20+ 场景 YAML/JSON + loader、runner（复用 §3 fake 基建驱动 dispatch 缝）、failure-taxonomy task 级归因断言。新 marker 若必需才登记 pytest.ini。
2. `scripts/g05_task_acceptance.py`：run/compare/report CLI（薄壳，形态抄 scripts/replay_bench.py:26 的 argparse 结构；coverage 热区图输出到 docs/quality/ 或 reports/）。
3. `docs/quality/g05-*.md`：语义文档（非生成物）。
4. `planning/g05/`：PLAN/WORKLOG（本 RECON 已就位）。

**复用读取（import，不改）**：
- 断言原语：`evaluate_goal_satisfaction`/`resolve_goal_contract`、`build_product_manifest`/`verify_export_semantic_consistency`、render_work_envelope 投影、`HarnessFailureClass.classify_harness_failure`、MapSpecStore 读 API。
- 执行基建：`ScriptedToolProvider/FaultInjector/LabClock`（app/lib/harness/lab/fakes.py）、`tests/fixtures/gis_samples.py`、`tests/fixtures/pi_mocks.py`、T4 `session_data` 沙箱注入（tool_dispatch_service.py:273）、kernel e2e 的 registry/sid fixture 模式。
- 取证：ReplayTrace/OfflineReplayer 结果结构、turn_journal diagnostics（build_causal_tree_sync）。

**缝合点（风险面，最小接触）**：
- dispatch 缝：`app/agent_pi_bridge.py:468 dispatch_tool`（只调用不修改）；kernel `get_runtime`（runtime.py:1605）。
- settle 缝：`settle_turn`（settle.py:597）——G05 靠 turn 完成后的 trace/map_product/manifest 取数，不在 settle 内插钩子。
- 导出缝：`app/api/routes/map.py` 导出端点产出的 `.manifest.json` sidecar（map.py:386-400）——G05 断言读文件，不改导出代码。
- 唯二可能动的共享文件：tests/conftest.py（尽量不动 _ENV_BASELINE，用场景级 monkeypatch）、pytest.ini（优先不加 marker）。

**明确不做**（红线复核）：第二 replay oracle（app/lib/harness/replay/ 语义不改）、生产 dispatch 热路径、#1759/#1760 全部文件清单（§6）。
