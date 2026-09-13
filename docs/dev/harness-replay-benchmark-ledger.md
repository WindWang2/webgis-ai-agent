# Ledger — Harness Replay + Benchmark + Explainability (R10)

规则: 每个里程碑追加一节；只记事实（改动文件、契约、测试原文摘要、证据、回滚面、未解决项）。

## M0 — Phase 0 勘察与基线（2026-09-14）

- 基线 SHA: `origin/master = 580b33e9`（Merge PR #1272）；fetch --all --prune 已执行。
- worktree: `../webgis-ai-agent-r10`，branch `harness/replay-benchmark-explainability-v1`，无夹带改动。
- 勘察: Subagent A 深读生产链/评测设施/契约/9 个 open-merged PR diffstat/ADR 0150-0182/文档惯例，产出报告（主仓 tmp/ 归档，不入库）；主 Agent 复核关键锚点（lane 自由字符串、trace_store V6、Stage IntEnum、settle 缝、pytest markers、.alloc.json）。
- open PR 对账: #1270（低碰撞）、#1273（中）、#1274/#1275/#1277/#1279（高热区，全部规避或单点 additive）、#1276/#1278（低-中）。已合并复用基座: #1269（ratchet/fact store/golden）、#1271（wave ratchet）、#1272（offline fixture/guard）。
- ADR: claim 0183（全网未占用）。
- 文档: recon/decisions 两篇落 docs/dev/（本文件为第三篇）。
- 未解决项: 无阻断项。风险登记见 recon §8。

## M1 — ReplayTrace schema + determinism + sanitizer + B0 投影器（2026-09-14）

- 改动: `app/lib/harness/replay/{__init__,schema,determinism,sanitize,metrics}.py` + `tests/harness_replay/test_replay_schema.py`（17 tests）。
- 契约: ReplayTrace v1（additive-only 演进，未知字段保留）；behavior_digest（语义投影：剥墙钟/关联 id/计时精度）；sanitize（秘密键 REDACTED、禁值键 digest 块、字符串秘密正则剥离、超限降级 digest-only）。
- 关键发现: 生产链 payload 经 `bound_meta` 把嵌套 dict repr 化且嵌套 secrets 不脱敏 → sanitizer 补 `ast.literal_eval` 安全还原 + 字符串级秘密剥离。
- 测试: 17/17 绿 + ruff 绿。回滚面: 纯新增文件。

## M2 — Recorder + 生产接线（2026-09-14）

- 改动: `app/lib/harness/replay/recorder.py`；`app/agent_pi_bridge.py` 两处 settle（stream_prompt finally + prompt settle）各 +1 个 try 包裹的 `maybe_record_turn` 调用。
- 契约: `HARNESS_REPLAY_RECORD`/`HARNESS_REPLAY_DIR` env 总闸；64 条/session FIFO 保留；never-raises；排队期早退路径不接（无轨迹可录）。
- 测试: recorder 6/6 绿；`tests/test_pi_integration.py` 24 通过 + 1 失败（heartbeat flake，已在干净 master 复现同败 = 预存，非本线回归）。
- 回滚面: bridge revert 2 个 hunk 即回 master 行为。

## M3 — Offline replayer T1/T2（2026-09-14）

- 改动: `app/lib/harness/replay/replayer.py`；`tests/harness_replay/{test_replay_replayer.py,replay_judge.py}` + `tests/fixtures/replay/map.png`。
- 契约: Scenario/ScenarioOp/TurnSpec schema v1；T1 冻结 ops → 真实 harness/gate/L5；T2 mutations → 真实 lifecycle facade（`source_profile`/`layer_upsert` 等 13 op）；比对 exact/tolerant/nondeterministic_text；replay_digest 含 gate score（canned 数据的纯函数）。
- 关键发现: 生产信任阶梯要求 session 持有的 frontend observation（freshness/指纹收敛/style/图层在场）→ 绿场景 fixture 携带完整 observation；占位符 `$cartographic_fingerprint`（同 `cartographic_fingerprint` 生产函数）/`$session_id`；T2 指纹用剥 session 别名（ref/ref_id/data_fingerprint）的归一化指纹；离线 L5 走 `CARTO_VISUAL_JUDGE` env 缝 + 确定性 fixture judge（`tests.harness_replay.replay_judge:deterministic_judge`）。
- 测试: 35/35 绿 + ruff 绿。
- 未解决项: T3（ToolDispatchService 假服务级重放）留接口点（`dispatch_backed` 字段 → not_run 不静默）；CQ 绿路径的 observation fixture 仅覆盖单点图层形态，多图层/表达式样式形态留给语料扩展。


## M4 — Scenario corpus（2026-09-14）

- 改动: `app/lib/harness/replay/scenarios.py`、`tests/harness_replay/test_replay_corpus.py`、展开产物 `tests/fixtures/replay/scenarios/`（140 JSON + index.csv）。
- 语料: 104 core 单轮（13 类 × 8 变体）+ 36 多轮（3–5 轮，4 类 × 9 变体）= 140；26 条带故障规格。
- 期望即语义: expect 树钉生产信任阶梯的裁决语义（MSV 会话级比例诚实、CQ fail-closed、恢复语义），对确定性 actuals 验证；非 snapshot。
- 实证: circle/fill/line/heatmap/raster(+source.bounds) 图层形态逐一通过 `evaluate_cartography_semantics`；legend 等价检查要求数据驱动 paint 深度配对（master 自身测试覆盖面，语料不复验，见 decisions）。
- 测试: 语料 7 tests（规模下限/类别覆盖/roundtrip/全量重放绿/确定性/故意劣化翻红）。

## M5 — Fault injection + ratchet 接流（2026-09-14）

- 改动: `app/lib/harness/replay/{faults,ratchet}.py` + 2 个测试文件（13 + 6 tests）。
- 故障 10 类全部 fail-closed 契约化（gate_red/cursor_fail/cq_not_pass/goal_not_evaluated/recovered）；renderer_failure 诚实映射为前端观测不可信（style_loaded=false）。
- ratchet: 重放行 → `build_baseline_entries`（provisional-first）/ `evaluate_ratchet` / `record_quality_run(lane="replay")`；零新表；intentional degradation 测试证明 active 基线会拦截；waiver suppress/expire 验证。

## M6 — Explainability + bench CLI + perf profiles + triage（2026-09-14）

- 改动: `app/lib/harness/replay/{explain,triage,bench}.py`、`scripts/replay_bench.py`、`tests/harness_replay/test_replay_bench_cli.py`（9 tests）。
- explain: 录制轨迹与离线重放双入口因果链 bundle（JSON+Markdown），披露 rejected/fallback/override/invalidation/missing-evidence。
- triage: 六分类 + 证据路径，禁止裸 "snapshot changed"。
- bench: suite/seed/offline/顺序有界（视觉裁判 env 注入禁并发，`--jobs` 保留为 1）/resume/--compare(digest drift/new/missing)/json|csv|md/--only-failed；perf profile small/medium/large 为合成描述符放大（语义不变，large 仅手动）。
- CLI 冒烟: `python scripts/replay_bench.py --suite core --limit 8` 8/8 绿（exit 0）。
- lint: ruff 全绿（app/lib/harness/replay + tests/harness_replay + scripts/replay_bench）。

## M7 — 回归、独立 review、PR（2026-09-14）

- 回归: `pytest -m cartography --no-cov -q` → **1003 passed, 0 failed**（含本线 72 个 replay 测试并入发布闸）；`tests/test_pi_integration.py` 24 过 + 1 预存 flake（heartbeat，干净 master 同败）；`tests/quality/` 406 过 — 3 个生成物新鲜度闸因新增 app 文件过期 → 已跑 `gen_quality_manifest/gen_quality_report/check_generated_staleness --update` 刷新修复；SQL f-string 扫描失败与 trio 参数化 errors 在干净 master 同现（ADS-V1 文件 / 缺 trio 依赖，预存）。
- master 是否前进: fetch 后 origin/master 仍 = 580b33e9（= 本线 merge-base），无需 rebase。
- 独立 review（Subagent B，四轴）判定 fail：**1 P0 + 8 P1 + 8 P2**，修复情况：
  - **[P0] 提取层键名与生产发射器不匹配（真实录制全空）** → schema.py 双键名兼容（`call_id/tool/args/latency_ms/query/task/recipe_id/candidates.selected`）+ 无 id TOOL_RESULTS 按工具名顺序回填 + **真实发射形态回归钉**（TestProductionEmitterShapes）。发现：dispatch 面 `arg_keys` 在生产链即被 bound_meta 上游 [REDACTED]——参数形状本就不携带，提取层保持空参是诚实行为。
  - [P1] faults 未接 bench → run_one 在环境边界 apply_faults + run_suite 断言契约（degradation_present 语义）；[P1] bench→ratchet 断链 → `--ratchet-rows`/`--record`；[P1] --offline 静默无效 → 强制置位 + 安装失败 exit 2；[P1] digest 计时抖动 → tool_calls/链记录 latency/duration 只留存在性；[P1] compare_exact 多余 actual 列表项假绿 → 长度差 diff；[P1] explain_trace missing-evidence 死代码 → FINAL_VERDICT 阶段载荷推导 + 阶段缺席披露；[P1] sanitize 绕过（X-Api-Key/passphrase/AKIA/bearer:?/PEM）→ 键归一化 + 新模式；[P1] T2 写共享生产存储 → run_token + 沙箱存储目录 CM（跨 run 指纹稳定、零污染）。
  - [P2] 全部处理：512KB 整体预算 + truncated 落地；resume 损坏容忍 + 原子写 + 僵尸条目过滤；turn_id 字符集守卫；**140 提交 fixtures 与生成器逐字节 parity 钉**；text-diff 与 ok 解耦（nondeterministic_text 永不作语义失败）；recon/ADR/decisions 措辞与代码对齐（registry 读取、104+36 计数、--jobs 顺序语义、D2 收集面）；from_dict 未知字段 to_dict 再发出；explain cosmetic。
- 最终状态: tests/harness_replay **72 passed**；ruff（replay 包 + tests + scripts）全绿；CLI 冒烟（--offline --ratchet-rows --format json）exit 0。
- 未解决项（记入 PR）: T3 dispatch 级重放留接口点；CQ 绿路径 observation fixture 仅单点图层形态；bench 红场景二次重放成本（换取 triage/explanation，已文档化）。
