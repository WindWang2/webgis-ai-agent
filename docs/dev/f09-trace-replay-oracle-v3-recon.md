# F09 — Harness Trace / Replay Oracle v3：Recon（执行时快照）

- 执行日期：2026-09-26
- 基线：`origin/master = 9e1ad22907e99cd7b4721153294448ce6496c717`（2026-09-24，merge #1494 dependabot pandas）
- 分支：`zcode/f09-trace-replay-oracle-v3-20260926-9e1ad229`；worktree：`../wt-webgis-f09-trace-replay-oracle-v3-20260926-9e1ad229`
- 任务书 seed snapshot 的 2026-09-25 观察在本执行时仍成立（origin/master 无新提交）。

## 1. GitHub 状态（执行时）

- **Open PR**：仅 #1489（dependabot docker node 22→25，只改 `Dockerfile`/`Dockerfile.prod`）——与本方向零交集。
- **最近 merged**（30+）：#1490–#1496 全部 dependabot；功能性波次 = #1479–#1488（2026-09-21 合入）。
- **Open issues**：#1436（前端 i18n，无关）、#1377（audit 延期跟踪，无 trace/replay 相关项）。
- 本方向直接前置 = **#1486**（trace/replay 闭环 v2，branch `harness/trace-replay-closed-loop-v2`）。

## 2. Overlap / Already Done / Still Missing / Must Not Touch / Integration Seams

### Overlap（无——不复制任何 open PR 工作）
#1489 是 dependabot docker 改动，无文件交集。

### Already Done（#1486 及更早，本线禁止重做）
- ReplayTrace v1 打包层（`replay/schema.py`，additive-only，512KB 预算）+ settle 缝录制器（`recorder.py`，env-gated、never-raises、64 条/session 淘汰）。
- T1 证据级 / T2 变异级（真实 `mapspec_store` + 沙箱目录）/ T3 bind-gate（生产同函数 `check_tool_capability_at_dispatch`）离线重放（`replayer.py`）。
- 140 场景语料（`scenarios.py` + `tests/fixtures/replay/scenarios/`）+ 10 型故障注入（`faults.py`）+ ratchet 接流 + bench CLI（`bench.py` + `scripts/replay_bench.py`）+ committed 基线（`tests/fixtures/replay/baseline.json`）。
- 决策溯源：`DecisionRecord`（内容地址 decision_id）、`GisTraceChain` 结构化决策通道（域感知消毒）、`replay/drift.py`（registry digest / rederive / diff_decisions）。
- `roundtrip.py`：录制件 → Scenario（multi-turn 合并、degraded 诚实 tag）。

### Still Missing（= 本方向 8 个 WP，#1486 明确遗留）
| #1486 遗留声明 | 证据（origin/master） | 本线 WP |
|---|---|---|
| receipt 级经 ToolDispatchService 重发不做（`replayer.py:495` `deferred_levels=["receipt_redispatch"]`） | ToolDispatchService 全部副作用面无离线替身 | WP1 |
| recorded 场景 expect 回填（`roundtrip.py:70` 恒 `expect={}` → green-by-construction，ADR §3 已登记） | `ScenarioResult.ok = not exact_diffs`，空 expect 恒绿 | WP2 |
| per-tool governor estimate/actual 未入链（`ReplayTrace.governor` 预留 None；TurnEvidence 无资源维） | `dispatch_adapter.run` finally 的 join 逻辑只活在局部作用域 | WP3 |
| legacy ChatEngine path 无录制（`maybe_record_turn` 全仓仅 bridge 两处调用） | `execution_engine.py` settle 点（~1437 / ~2824）无录制 | WP4 |
| env fingerprint 只有 `registry_digest` | `recorder.py:121-130` 是 env 唯一键 | WP5 |
| 无 master-vs-branch 结构化 delta（`compare_results` 只报 digest 漂移） | `bench.py:218-248` | WP6 |
| 无 failure trace minimization | 无 shrink 模块 | WP7 |
| `scrub_secret_strings` 未下沉中立模块（`decision_record.py:54-59`、`gis_trace.py:80-82` 反向依赖 replay 包，ADR §3 已登记） | 生产 runtime → 测试 oracle 模块的方向性依赖 | WP8 |

### Must Not Touch
- 不重写已稳定的 replay 核心契约（schema/replayer 语义 additive-only）。
- 不动 `tests/fixtures/replay/baseline.json` 的 140 条 digest（除非语料变化显式重建——本线不改语料场景）。
- 不在 replay 中写真实 session/MapSpec/外部系统；不绕过 qualification/safety gate。
- 热区（近 60 天 commit 密度）：`agent_pi_bridge.py`(108)、`tool_dispatch_service.py`(62)、`execution_engine.py`(58) —— 只贴既有缝插入，不做结构重构。
- 本地主工作目录（master 分支，含未提交的 audit 改动）与本任务无关，禁止触碰。

### Integration Seams（实现接缝）
1. **redaction 中立化**：新建 `app/lib/redaction.py`；`replay/sanitize.py` 改为 re-export；`decision_record.py`/`gis_trace.py` 的 import 指向新家。
2. **env v2**：`recorder.collect_turn` 的 `env_payload` 扩展；`drift.py` 增加 `env_drift` 分类投影 + `capability_registry_digest` 按 graph 指纹记忆化。
3. **trace 补 dispatch 证据**：`build_trace` additive 拷入 `turn_summary["capability_dispatches"]` → 新顶层字段 `dispatch_evidence`；`roundtrip._trace_turn` 由 `verdict.final_verdict` / `verdict.map_product` / `decisions` / `dispatch_evidence` 派生 expect。
4. **governor 入链**：`TurnEvidence.add_resource_usage`（有界 ≤16 FIFO）→ `to_summary()["resource_usage"]` → `build_trace` 填 `trace.governor`；`dispatch_adapter.run` finally 投影（estimate 主维 + actual + wall + attempt + drift 比率）。
5. **legacy 录制**：`execution_engine.py` 两个 settle 点各插 4 行 try/except（与 bridge 同款；map_product 缺席 → degraded 诚实录制）。
6. **T4 receipt 级**：`ToolDispatchService` 构造器提 `session_data_manager` 为可选依赖（兼容缺省 = 模块级单例，零行为变化）；重放侧 fake registry（回放 canned receipt）+ 内存 session store stub + `fire_broadcast=None` + env 三闸关闭（`GIS_ANALYSIS_REUSE/GOVERNOR_TOOL_SURFACE/SPATIAL_GUARDRAILS`）+ `GIS_RECOVERY_LEDGER=0` + `MAPSPEC_STORAGE_DIR` 指向沙箱 tmp。
7. **结构化 baseline/diff**：`write_baseline` 增 structured 投影（gate checks / mutations / dispatch / decisions，additive）；`bench.diff_reports` 归并器（digest_drift → turn/check/call/decision 粒度定位）；CLI `--diff`。
8. **shrink**：`replay/shrink.py`（delta-debug over turns/ops/faults/mutations，oracle = OfflineReplayer + ok 判定）；CLI `--shrink`。

## 3. 已知风险（继承 + 本线缓解）

- **green-by-construction**：WP2 修复；修复后 recorded 场景的 expect 必须来自录制时的真实事实（verdict/dispatch/decisions），并带 `expect_source=recorded` tag。
- **T4 副作用面**：session store（patch stub）、event_log（同 stub）、WS 广播（None）、artifact registry（走 stub store）、recovery ledger（env 关 + MAPSPEC_STORAGE_DIR 沙箱化）、MapSpec 展示授权（canned receipt 形态不触发 mapspec 写；沙箱 store 兜底）。
- **秘密面**：`user_input/final_text` 有界+值级 scrub（只匹配高置信形态）→ WP8 下沉 + fuzz/negative tests 钉死；env v2 只收封闭白名单布尔，绝不收任意 env 值。
- **性能**：`capability_registry_digest` 无缓存（录制开闸 ~255ms/turn）→ WP5 按 graph fingerprint 记忆化；bench 顺序执行纪律不动。
- **master 本地分叉**：本地 master 与 origin/master 已分叉（本地含 16 个未推送 audit commits，分叉点早于 #1479–#1488）；本线全部工作基于 origin/master=9e1ad229，与本地 master 无关。

## 4. 测试基线（执行时）

- `tests/harness_replay/` 8 文件 82 测试（schema 18 / replayer 14 / corpus 8 / faults 10 / ratchet 6 / bench CLI 9 / recorder / roundtrip 17）。
- committed baseline：140/140 绿（`profile=small, seed=0`）。
- 本线新增测试全部落 `tests/harness_replay/`（+ 单测进 `tests/unit/`），串行低并发运行。
