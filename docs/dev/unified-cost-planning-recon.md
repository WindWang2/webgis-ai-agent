# Unified Cost / Resource / Planning Model — Recon（direction 7）

日期：2026-09-20 · 分支：`harness/unified-cost-planning-v1` · 基线：origin/master `5a4d4632`（#1478）

## 1. 估算口径现状（三套同名异构 + 一套空挂）

| 面 | 位置 | 口径 | 消费方 |
| --- | --- | --- | --- |
| governor rg.v1 | `app/services/governor/contract.py`（ResourceEstimate/DimValue/CONSERVATIVE_FLOORS） | 逐维 range + confidence + source + reason；unknown≠0（保守地板） | admission/backpressure/ledger（dispatch_adapter 唯一喂点） |
| harness 分类档位 | `app/services/gis_harness/qualification_v8.py:397-481`（ExecutionEstimate + estimate_for_node） | latency/memory 档位词表 + basis 披露；confidence=有据维占比 | candidate_planner_v8 / capability_resolution |
| modelops | `app/lib/modelops/resources.py:14`（同名 ResourceEstimate dataclass） | vram/host_ram/batch；明言不对接 governor | modelops 引擎（未接线） |
| 空挂 | `app/services/gis_harness/candidate_resolution.py:292` `_COST_RANK` | 定义后从未使用 | 无 |

已知残余（ADR-0204 review P2-5，有意识接受）：planner 取 graph extras 的
`cost` 声明、dispatch 取 registry metadata `cost` —— graph extras 过期时两源
会静默分叉（先验表已单源，cost 来源未单源）；与 classify_tool「名字模式优先」
的已文档化权衡同源，由校准脚本的通道水位证据发现后再收敛。

**核心断点**：`estimate_for_node`（规划面）与 `estimate_for_tool`（派发面）零互相引用 ——
planner 排序看不到 dispatch 实际计费的数值估算；dispatch 计费也拿不到 planner 的档位证据。
两套先验（`_TOOL_CLASS_PRIOR` vs graph extras 声明）无一致性约束。

## 2. 计划级聚合现状

`governor/estimation.py:214 sum_estimates` 是**全维朴素求和**：wall_time 也求和（无
critical path / 并行 peak 区分）、内存也求和（sequential 复用被重复计费）、无 retry
乘数、无 cache 折扣、无 optional/fallback 分支语义。方向 7 明确点名此反模式。

## 3. planner 现状

`candidate_planner_v8.plan_candidates_v8`：资格过滤 → `score = latency_rank + degraded
0.5 + reliability_penalty`，截断 8。cost 维**未接入**（文件头宣称 V8.4 latency/cost，
实际只有 latency）。V3 `plan_candidates.py` 九维评分含 cost/latency 维，但其
`_estimate_cost`（L229）是 recipe 启发式（调用数 + heavy 集合），与两套 estimate 均无关。

真实多 provider 能力（live registry 实测 21 个）：`admin_boundary_query`（7 providers，
slow/heavy ↔ fast/medium）、`dataset_ingest`（medium ↔ heavy memory）、
`crs_transformation`、`image_segmentation`（fast/light ↔ heavy/slow）—— resource-aware
选择有真实试验床，无需造数据。

## 4. render / export

`governor/render_budget.py` 估工公式完整（DPI 平方律、provisional 系数），但
`RenderWorkInput` 在生产代码**零构造**——render 估算从未被真实制图路径喂数。
`publication_export.py` 自有 `_WEASYPRINT_LOCK` + SVG 字节预算，与 governor EXPORT
通道 `max_exports` 双轨（本 PR 不收敛执行面，见 out-of-scope）。

## 5. actual / 校准

- dispatch_adapter complete 只回填 `wall_time_s`；governor.complete 算
  estimate-vs-actual → Prometheus 直方图（聚合，无 per-tool 持久化，不回填先验）。
- `scripts/perf/calibrate_governor.py` 只跑 synthetic corpus（FakeExecutor），"actual"
  是合成乘数。
- `qualification_v8` 的 `measured` basis 预留未实现。
- 独立账本：GeoComputeResourceUsage（DB CAS）、MissionResourceLedger（durable）与
  governor metrics 互不回灌。

## 6. retry / replan 预算

- `governor/retry_budget.py` 消费方仅 governor 包内（dispatch attempt>1 路径；
  dispatch_adapter 目前不传 attempt/retry_class）。
- harness 侧 `durable_context.LOOP_BUDGETS`（deepen/requalify/repair/replan）纯次数；
  `plan_runtime.request_replan` 只查次数。RetryBudget 与循环预算互不感知。

## 7. 热区（最近 14 天 #1450-#1478）

`workflow_runtime/dispatch.py`、`agent_pi_bridge.py`、`gis_harness/plan_runtime.py`、
`capability_graph.py` 高频；#1477 新增 `hotpath_convergence/capability_bind.py`（
plan_candidates_v8 第一个生产调用方）；#1470 改过 `qualification_v8.py`。本分支从
最新 master 切出，全部包含；本方向修改 `qualification_v8.py`/`candidate_planner_v8.py`
时保持函数签名与既有字段向后兼容，避免与后续并行分支冲突。

## 8. 绝对禁止重复实现

- 不重写 Governor / admission / backpressure（ADR-0182 刚落地，#1472/#1388 刚加固）；
- 不新增第二套先验表或第二套 ResourceEstimate；
- 不做商业计费、GPU 压测、全量数据扫描式估算；
- 不动 geocompute `ResourceBudget`（rows/bytes/nodes L1 权威在其树）；
- 不改 V3 recipe 评分语义（compiler 零漂移纪律）。
