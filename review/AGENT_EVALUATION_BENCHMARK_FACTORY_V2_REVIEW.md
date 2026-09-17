# Review — GIS Agent Evaluation & Benchmark Factory V2

- **Branch:** `eval/gis-agent-benchmark-factory-v2` @ `../webgis-wt-eval-factory-v2`
- **Baseline:** `origin/master` `faa453a8935101378c23eb6694a42c3616d9c670`（2026-09-16 fetch 确认，PR 前复核未移动）
- **Reviewer:** Subagent B（独立对抗 review，2-subagent 预算内）+ 主 agent 修复轮
- **Commits:** `605a5a94`（factory 主体）→ `ccca5951`（ruff）→ `bfde230f`（CLI）→ `7709275a`（review 修复轮）
- **Local evidence only; no online CI wait; do not auto-merge.**

## 1. Review 覆盖面与逐项结论

| 角度 | 结论 | 依据 |
|---|---|---|
| Architecture / contract drift | PASS | diff 仅触碰自由区（app/evaluation/**、tests/quality/test_v2_*、tests/unit/gis_harness/test_v2_runner_tiers.py、scripts/gis_bench_v2.py、.gitignore、.agent-work/）；无第二系统（mission 驱动器走真实 MissionRuntimeService 工厂缝；语料为审定表 house style；tier 镜像 `_run_v3_contract_tier`） |
| Correctness / fail-open | PASS（修复后） | P1 fail-open 已红测→修复（见 §3）；全部 catch 追加 failures；`run_case` 异常即败；无虚构指标 |
| Concurrency / cancellation / idempotency | PASS | 假钟 `finally` 恢复（异常/提前 return 均安全）；`GIS_SKILL_POLICY` 在 resolve 时读取 → save/restore 窗口有效；终态幂等探针断言 cancel 后 complete() 必拒；stale-writer 走真实 FencingError CAS |
| Security / tenancy | PASS | 跨租户证据断言 UNSUPPORTED（生产 verify_claim）；双向 ratchet 把检测器回归转为红测；mission 驱动器 sqlite 内存 StaticPool + dispose，零真实 DB；新模块零网络/零 LLM（grep 证据） |
| Performance / memory / context | PASS（修复后） | manifest 构建缓存（原 5.5s/次 → 摊平）；单测 max ~11s < 60s 预算 |
| Backward compatibility | PASS | 既有 quality corpus + cases matrix 回归 28 绿（73s）；case.py 全部 opt-in 字段；markdown 表头新增 9 列沿用 master 自身 V3 列扩展先例，`test_markdown_shape_unchanged` 锁定 |
| Observability / evidence honesty | PASS | 每个新指标 None-when-undeclared（专测 `test_undeclared_v2_fields_are_honest_none`）；failure 消息带 expected/got；mission 证据为结构化 state/frontier/recovery 计数，无 CoT |
| Benchmark leakage / self-fulfilling | PASS | 期望表带日期审定注释；现状锚显式标记（known-gap/known-escalation/known-unsafe 且双向检测断言）；version_hash 哈希案例本身，与 runner 无循环 |
| Report / CLI | PASS（修复后） | diff 桶覆盖回归/修复/指标漂移/增删；Windows UTF-8 stdout 兜底；退出码语义修复（§3） |

## 2. 测试与验证证据（PR 前，同 commit）

- V2 全套（9 文件）：**73 passed**（新增 P1 回归测试后；`--no-cov -q`，~60s）
- 相邻回归：quality 消费方 31 绿；conformance/runtime/goal 23 绿；harness_replay 72 绿
- Oracle 稳定性：`python scripts/gis_bench_v2.py --out run1` → `--out run2 --baseline run1/report.json`
  → `new_failures=0 fixed=0 metric_moved=0 new_cases=0 missing_cases=0`（**连续执行两遍口径一致**）
- ruff 全绿；`git diff --check origin/master...HEAD` 空；无 conflict markers；diff 无 binary/coverage/temp

## 3. Findings 与处置

### P1（复现 → 红测试 → 修复 → 回归 ✅）
- **`runner.py::_run_turns` — `expected_scope_binding="new"` 在空 scope 时静默通过（fail-open）。**
  解析器回归（完全丢失 scope）会让换绑行假绿。Reviewer 复现：turn query 无 scope → `coreference_binding_ok=True`。
  修复 `7709275a`：`new` 契约要求非空新前件（`not scope_name or == last_scope` → 失败）；
  红测试 `test_rebind_with_unresolved_scope_fails` 先红后绿；hard_negative 语料全量回归不受影响（run1→run3 diff 全零）。

### P2（已修复 ✅）
- **CLI 无基线时永远 exit 1**：known-escalation 行按设计 fail 被 naïve 计入。
  修复：预期检出行分区为 `report["expected_failures"]`，只有非预期失败才 exit 1（首跑可绿）。
- **manifest 无缓存**：`corpus_manifest()` 每次全量重建 18 语料（实测 5.53s / 33,369 cases）。
  修复：`corpus_cases()` 按注册名缓存（语料确定性由构建期守卫保证）；`register()` 时失效。

### P3（记录，不阻塞）
1. `index.py` 跨语料 prefix 守卫只做注册级归属，未校验 id 实际前缀（dup-id 检测是真实的）。
2. `report.py` `metric_moved` 中 `k != "elapsed_ms"` 为死代码（entry 已不含 elapsed）——无害保留。
3. DECISIONS.md D6 "render_markdown byte-compatible" 措辞不严格：表头新增 9 列（先例一致，有测试锁定）。
4. mission 假钟不越真实墙钟的不变量已加运行时守卫（`7709275a`）；`_CLOCK_LAG_S=2h` 与场景推进上限的推导仍以注释承载。
5. `test_iter_all_cases_matches_manifest_counts` 硬编码 benchmark-case 语料名单（有意白名单；新增语料需同步）。
6. xdist 在本环境未安装（`-n 2` 不可用）——测试以 monkeypatch 作用域 env + save/restore 保证并行安全构造，未做实机 xdist 验证。

### 误报澄清
- 良性本地工具（`query_local_poi`/`get_local_admin_boundary`）描述符声明 `network=True`（数据服务语义）
  → security 语料不使用绝对 `forbid_network_tools` 契约（属 quality 离线族），containment 以 twin-diff 升级检测承载。
- `SEC-escalation-*` 两行在 CaseResult 层是 fail —— 这正是其契约（检测器必须抓出）；
  语料测试断言检出签名，CLI 分区为预期失败。基准报告读者应读 `expected_failures` 而非裸 failed 数。

## 4. 真实产品发现（基准的产出，非本分支缺陷）

1. **注入语义劫持**（SEC-escalation-en/zh）：祈使注入壳把任务劫持到 vegetation_index/ndvi，
   引入 network 工具 `fetch_dem`/`compute_ndvi` —— plan 层无注入防护。建议后续 hardening（本分支不改产品）。
2. **默认 YlOrRd k=5 deuteranopia ΔE=8.99 < 10**：默认色带可访问性缺口（Viridis ≈13.4 安全）。已锚定 known-unsafe。
3. **EN rate 措辞缺口**："highest … density" 落 distribution_overview 而非密度分析。已锚定 known-gap。
4. **verify_claim 信任传入 claim 对象而非 store 状态**：矛盾标记写回 store 后需 re-fetch 再裁决 —— API 语义陷阱，
   已在 evidence tier 显式处理（store 为事实源）；建议上游（#1335 面）评估是否收敛。

## 5. 与最新 master / open PR 交叉

- PR 前复核（第二次 fetch --all --prune）：master 仍 `faa453a8`，无新增 merged commits。
- Open PR：#1335、#1336（启动即已知）、**#1351（执行中出现）**。#1351 触碰
  `app/lib/data/**`、`app/services/data_quality/**`、`tests/data/**`、`tests/perf/**` —— 与本分支**零文件重叠**。
- 唯一共享路径：`.goal-loop-ledger.md`（#1351 亦添加）→ 合并时 add/add 良性冲突，任取/合并双方内容即可（过程账本，无语义）。
- 无需 integration PR。

## 6. 已知边界

- 视觉轴维持 honest `not_evaluated`（无 VLM；确定性事实经 layout/CVD/template 轴供给）。
- security corpus 审定真值锚定 2026-09-16 planner 行为；注入 hardening 落地后 SEC 行应有意识翻新（ratchet 语义）。
- mission 驱动器不走 `claim_ingest` 热路径缝（#1335 热区；证据面由 evidence tier 独立覆盖）。
- 本地环境无 xdist/PostGIS/真实 LLM；全部验证为离线确定性口径。
