# GIS Harness Reliability Lab（G07）

本线把既有 Harness Replay / Lab / Ratchet / Cartographic Evidence / Recovery Policy 连成可审计、可复现的工程证据，而不是另建监控、规划、渲染或会话平台。实时基线为 `origin/master=585812ee4e2d75612f2b124e6fe6587af3deca5c`；后续集成 SHA 以 `planning/G07-HARNESS-RELIABILITY-LAB/PR_HANDOFF.md` 为准。

## 1. 唯一事实源与完成证据阶梯

`app/lib/harness/replay/journey.py` 是只读投影，输入是已有 ReplayTrace，不驱动生产执行：

`intent → plan → tool dispatch → data result/ref → mutation → render observation → export → settlement`

每层保留规范 ordinal、录制列表中的 observed ordinal、来源 fingerprint、session/turn/goal/operation/correlation（若实际存在）和 typed reason。没有 goal/operation/correlation ID 时显示为空并暴露缺口，不把 request ID 改名伪造。

| 声明 | 可接受的证据 | 不能替代它的材料 |
|---|---|---|
| command accepted / tool dispatched | TOOL_CALLS、真实 dispatch/bind 收据 | 用户提示词或模型声称将调用 |
| tool result validated | 非错误 TOOL_RESULTS 与匹配的 call ID | 重复冲突、悬空结果、错误码 |
| artifact persisted | 与结果 ref 匹配的 ARTIFACT_CREATION / artifact ref | 只有 ref 字符串或 LLM summary |
| MapSpec applied | revision、mutation receipt、权威 fingerprint | assembled 但未提交的图层 dict |
| map rendered and observed | 真实 map observation，且无 context loss/tiles 缺口 | MapSpec、静态截图、FakeVLM、map_idle 单独出现 |
| goal satisfied | 必要阶段成立后的 settlement | final_text、旧图、任务外的成功 |
| publication/export verified | 成功终态的导出 artifact 与 digest/ref | 导出按钮、普通文件名、失败中途的 ref |

`not_evaluated` 不得算 pass；P0 科学错误、身份/跨会话污染、冲突结果、锁丢失和伪完成是硬失败，不能被平均分掩盖。具体 reason codes 见 [Failure Taxonomy](GIS_HARNESS_FAILURE_TAXONOMY.md)。

### 只读诊断

```bash
python scripts/harness_journey_diagnose.py \
  --input /path/to/replay-trace.json \
  --session-id <session> --turn-id <turn> \
  --mode offline_replay --max-bytes 32768
```

输出包含缺失证据、最后有效阶段、执行模式、运行时 fingerprint 和保守恢复资格。输入最大 2 MiB，输出默认 32 KiB、上限 64 KiB，按真实 UTF-8 bytes 计算。跨 scope 读取只返回 `permission_denied`，不回显其他 owner 的 ID。CLI 不落报告文件，除非显式 `--output`，所以没有新增保留库或 TTL 状态。

## 2. 执行模式不能混称

| 模式 | 证明什么 | 不证明什么 |
|---|---|---|
| `offline_replay` | T1 evaluator/L5、T2 MapSpec lifecycle、T3 bind gate、T4 真实 ToolDispatchService + recorded provider | 真实外部 GIS 服务、真实模型、浏览器像素 |
| `live_dispatch_stub_llm` | 真实 dispatch_tool / ToolRegistry / ToolDispatchService / GIS fixture / MapSpec / settle | NL→tool-call 模型决策、线上数据、真实渲染 |
| `browser_renderer` | 本地真实应用壳、MapLibre 观察和导出交互（仅实际运行时） | mock `page.route` 代表生产 API、静态 fixture 代表地图观察 |
| `live_services_opt_in` | 显式外部模型/数据/服务 | 默认验收不运行，不作为本地 PR 通过条件 |

当前实测：

- 12 个黄金能力组共 **30 个真实 dispatch 场景全部通过**，报告 `reports/g07/golden.json`；其中 10 组由双侧场景构成，跨城与超限选择由 focused 车道补齐。数据为固定种子的本地合成 fixture。
- Pi v1.5.0 固定 pin 已构建，`python scripts/pi_doctor.py --json` 的 vendor/build/extension/surface/rpc/env 全部 PASS，含真实 handshake、turn 和 settled。显式 `PI_JOURNEY_E2E=1` 的真实 Pi + stub LLM 旅程亦 **1 passed, 32.17s**；默认 skip 的门控原因已记录。Pi 升级不是本线功能。
- 真实浏览器 smoke 首跑 **7 passed / 2 skipped**，但第二跑 J9 三维资产出现 `mounted` 15s 超时（6 passed / 1 failed / 2 skipped），因此不作为稳定生产门；API 为 mock，真实后端未验证。外部 live services 为 `not_run`。

## 3. 十二组黄金任务

事实源是 `tests/fixtures/reliability/golden_tasks.json`，只引用现有场景与 expectation ID。每组包含成功/失败 Oracle、参考空间、量纲、nodata、引用所有权和未验证边界。

| 组 | 真实场景/ focused 证据 | 关键 Oracle |
|---|---|---|
| 点分布 | F01 / F18 | 授权 ref 与错误 CRS/范围可区分 |
| 热力资格 | F02a / F02b | 40 点可热力，3 点回退点图并留 fallback |
| 分区统计 | F03 / F04a / FD2 / F11 | 计数守恒、显式分母、单位冲突不完成 |
| 网络可达 | F28-03 / F29-03 / F28-02 | 真 Dijkstra；无路网 typed blocked；秒与米分开 |
| 多层连接 | F07 / F29-01 / F09 | 空间谓词与 M:N 披露、错误 CRS 不吸收 |
| 跨城范围 | `test_pi_gis_usability_v2.py` | 地名/bbox 双向 guard，缺证据不猜归属 |
| 选择上限 | F24-02 + `test_reliability_selection_scope.py` | >8 披露 omitted，部分选择不得 usable |
| 时间地图 | F05 / F28-04 | 时间语义保留，同年伪双期拒绝 |
| 样式增量 | F12 / FD3 / F04b | 目标组件变化、pin 保留、错误方法不完成 |
| 出版导出 | F16 / F22-01 / F21-03 / F27-11 | 导出语义、静态媒介降级、bearing 落 spec |
| 取消恢复 | F13 / F14 / F27-10 / F26-03 | cancelled≠completed，恢复不读其他 owner |
| 离线降级 | F03 / F08 / F15 / F28-02 | 本地计算与外部缺失/权限的 typed 降级 |

复现：

```bash
python scripts/harness_reliability_golden.py --out reports/g07/golden.json
python -m pytest \
  tests/acceptance/gis_agent/test_reliability_golden.py \
  tests/acceptance/gis_agent/test_reliability_cli.py \
  tests/unit/gis_harness/test_reliability_selection_scope.py --no-cov -q
```

默认不访问公网、不启动付费模型、最多 2 个 pytest worker。报告明确记录 `llm_decision=scripted_not_evaluated`、`browser_renderer=not_run` 和 `live_services_opt_in=not_run`。

## 4. 故障与恢复

G07 在既有 replay faults 与锁/完结测试之上补跨边界负例：lock loss、cancel/timeout/disconnect、late turn、动态工具卸载、重复/冲突回调、WebGL context loss、缺 tiles、PostGIS 错误、导出失败。相同重复回调是 `partial`，冲突回调是 `fail`；不把 at-most-once 与 exactly-once 混为一谈。

四组受控恢复实验位于 `tests/unit/gis_harness/recovery_policy/test_reliability_recovery_experiments.py`：缺观察重验、瞬态读失败重试、跨城需新数据、持久编辑禁止自动 replay。动作只来自现有 RecoveryPolicy；没有评测侧直接应用 MapSpec 或修改用户数据的路径。

## 5. 性能与 ratchet

原始 benchmark 及环境/样本/重复数/before/after 汇总见 [GIS_HARNESS_RELIABILITY_BENCHMARK.md](GIS_HARNESS_RELIABILITY_BENCHMARK.md) 和 `docs/quality/evidence/` 下对应 JSON。计量严格区分 wall time、CPU、max RSS、payload UTF-8 bytes、artifact bytes 和 emitted events；不把字符数当字节数，不修改既有 `perf/budgets.json` 阈值。

`app/lib/harness/replay/metrics.py` 只向现有 ratchet 增加 record-only 行：

- `gate.replay.journey_overall` / `gate.replay.render_observed`
- `replay.journey_findings` / `replay.cross_session_violations`

旧 baseline 保持原样，新指标先记录，成熟后才可评审是否 gate。`not_evaluated` 不生成通过分；跨会话计数是硬失败信号，不能被其他维度平均。

## 6. 本地车道

```bash
# 快速证据/故障投影
python -m pytest tests/harness_replay/test_journey_evidence.py \
  tests/harness_replay/test_journey_faults.py \
  tests/harness_replay/test_journey_diagnostic.py \
  tests/harness_replay/test_journey_metrics.py --no-cov -q

# 既有 Replay/Lab 回归
python -m pytest tests/lab tests/harness_replay --no-cov -q

# 恢复与 scope
python -m pytest tests/unit/gis_harness/recovery_policy \
  tests/unit/gis_harness/interaction_ir \
  tests/test_gis_situation_analysis_scope.py --no-cov -q

# 固定版本 Pi 构建与诊断（子模块初始化一次即可）
git submodule update --init vendor/pi
cd vendor/pi && npm ci --ignore-scripts --no-audit --no-fund && npm run build && cd ../..
python scripts/pi_doctor.py --json

# 显式真实 Pi + stub LLM 旅程（仍不是付费/真实模型）
PI_JOURNEY_E2E=1 python -m pytest tests/integration/test_pi_stub_llm_journeys.py --no-cov -q

# 本地浏览器路径（依赖可用时才运行；mock 与 real 结果分开记录）
cd frontend
pnpm exec playwright test --grep @smoke   # mock，无后端，不证明生产 API
# E2E_MODE=real pnpm exec playwright test  # 需本地真实后端/worker/账号

# 静态检查
python -m mypy --no-incremental
python -m ruff check app/lib/harness app/services/gis_harness app/services/gis_situation
```

裸 CLI 会在导入 app 前初始化测试离线环境；不要让 shell 中的生产 Redis/PostGIS 配置泄漏进本地黄金车道。大型构建最多 `-j2`，不得同时满负荷运行后端、前端和浏览器构建。

## 7. 保留的 baseline debt 与未验证项

#1999（Agent/浏览器 A4 未走约束）、#2001（跨城 overlay/范围错位）、#2003（旋转导出指北针）、#2005（网络秒/米）、#2006（summary 数值）、#2007（生成快照/文档漂移）不因本线通过而自动关闭。普通 guard/export/bearing 测试不能冒充这些问题的修复证据。#2004 仅补了截断披露与下游 stale，未接管前端选择交互。

真实浏览器、真实 PostGIS 故障、真实跨进程 RPC 重连、live provider、付费模型与生产部署均未验证。本地通过不等于线上 CI 通过；本线不等待或宣称 CI。
