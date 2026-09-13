# ADR-0183: Harness Replay + Benchmark + Explainability——轨迹级回归体系 v1

- 状态: Accepted
- 日期: 2026-09-14
- 线: harness/replay-benchmark-explainability-v1（方向 10）
- 关联: ADR-0159（质量事实库/ratchet/golden 基座，本线全部复用）、ADR-0168（wave ratchet）、ADR-0174（fault 矩阵先例）、ADR-0178（facts/ratchet 零基线语义）、pi-host-seams.md（Pi 为 host 的缝清单）

## 1. 背景

仓库已有丰富的单点回归设施：18 阶段 GisTraceChain（trace_v6 JSONL 持久化，64 条/session 窗口）、TurnEvidence turn 包络、HarnessEvaluator 质量闸、质量事实库 + ratchet、像素/JSON golden、ads fault 矩阵与 offline guard。但它们没有串成**完整 Harness 行为的可重放单元**：无法离线重放一次"任务→规划→工具→收据→变异→地图产品→判定→交付"，无法在单条轨迹级做回归 ratchet，失败时无法定位到 phase，也没有给开发者的因果链解释产物。

## 2. 决策

### 决策一：ReplayTrace 是打包层，不是第二套 trace
以 `Stage` 1-18 为骨架，settle 时刻把 chain JSONL + TurnEvidence summary + verdict 块 + cost/timing + artifact digests 原子打包为单个 versioned JSON artifact（`schema_version=1`，additive-only）。为并行线预留 versioned optional 字段：`situation_revision`（#1275）、`governor`（#1279）、`skill_id`（#1278）、PlanStep 引用（#1277）。未知字段保留。

### 决策二：Recorder 骑 S13 settle 缝，env-gated，fire-and-forget
`HARNESS_REPLAY_RECORD` 总闸（默认关）。recorder 只读收集、白名单 sanitize（剥离 secrets/CoT/prompt 全文/原始 geojson，字段级字节上限，超限降级 digest-only）、自吞异常、不阻塞主链路。生产改动 = agent_pi_bridge settle 块单个 additive 调用。

### 决策三：离线重放 = 决策重放 + 确定性下游重执行（T1/T2/T3）
LLM/Pi 决策作为录制输入不重生成（vendor/pi 子进程与 LLM 均不可离线）。T1 证据级（harness→gate→goal satisfaction 重放，全场景）；T2 变异级（录制 map_actions 重放至全新 MapSpec kernel，比对 fingerprint/revision/findings）；T3 dispatch 级（fake 服务重发，仅声明场景）。输出比对三分类：exact / tolerant / nondeterministic_text。注入时钟 + 种子化 id。

### 决策四：场景语料 = 紧凑矩阵 + 确定性展开器
≥120 core 场景（17 类，含 36 个 3–8 轮 multi-turn），fault 场景组覆盖 10 类故障；每故障断言 fail-closed/fallback/resume。multi-turn 在同一 SessionPlan/MapSpec kernel 上顺序执行，断言跨轮情境持续性。

### 决策五：轨迹指标接 #1269 ratchet，provisional-first
`record_quality_run(lane="replay")` 入既有四表（无新迁移）；bench 产出 ratchet 行 JSON 走既有 CLI；基线默认 provisional 不拦截；仓库内置 intentional-degradation 测试证明 gate 会红；不自动更新 baseline。

### 决策六：Explainability + bench CLI + triage
因果链 bundle（JSON + Markdown）：task→plan→tool→evidence→product→verdict，含 rejected options/fallback/user overrides/invalidation/missing evidence。`scripts/replay_bench.py`：suite 选择、seed、offline 强制、bounded 并发（默认 1）、JSON/CSV/MD 输出、baseline 比对、only-failed、resume。失败分类六分：semantic_regression / cartography_visual_regression / data_fixture_drift / generated_artifact_drift / platform_limitation / nondeterministic_text_only——禁止裸 "snapshot changed"。

## 3. 后果

- 正面：轨迹级可重复回归；失败可定位 phase；生产行为可离线复现研究；为 #1274/#1275/#1277/#1279 提供评测对接位（optional 字段）。
- 代价/风险：agent_pi_bridge 单点接线与并行线小冲突面（1-3 行）；trace 打包体积需上限纪律（默认 512KB 降级）；ratchet 基线激活需人工（有意为之）。
- 回滚：删去 settle 单调用 + env 总闸关闭即完全回到 master 行为；新代码全部在 `app/lib/harness/replay/**` 与 `scripts/replay_bench.py`，无 schema/DB 影响。
