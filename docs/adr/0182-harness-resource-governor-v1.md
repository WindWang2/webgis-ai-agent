# ADR-0182: Harness 资源治理 Governor V1 —— 跨域准入、预算、背压与资源感知执行

- 状态: Accepted
- 日期: 2026-09-14
- 线: harness/resource-cost-governor-v1（worktree `webgis-wt-resource-governor-v1`）
- 关联: ADR-0096（geocompute 层级资源治理——ResourceGovernor 本体）、ADR-0100（会话公平 wave gate）、ADR-0101/0103/0104（context 预算面）、ADR-0131（SLO 预算 manifest）、ADR-0158（visual judge 限流纪律）、ADR-0159（ratchet/provisional 纪律）、ADR-0166（export 串行纪律）、ADR-0173（DF 成本模型）、ADR-0174（DF 降级链）

## 1. 背景

P0 勘察（`docs/dev/harness-resource-v1-recon.md`，全部断言带 file:line）确认：本仓已有
**30+ 个 isolated 的资源机制**（geocompute 预算树、DF 结果界与熔断、context 预算、org
配额、wave/session 闸、export 串行、SLO manifest……），但 Harness 仍无法回答
"当前目标在当前负载下怎样执行"：

- **G1** 四套预算互不知道彼此，无组件能回答"这个 turn 总共花了多少"；
- **G2/G3** 无 plan 级聚合预算、无跨子系统全局准入（wave 5 / workflow 4 / executor 2
  是孤立旋钮）；
- **G4/G5** 重试级联无成本聚合、取消后废功不入账；
- **G6-G9** 无全局公平/内存压力信号/统一降级叙述/deadline 传播；
- **G12** SLO 观测（observe-only）与准入决策断裂。

## 2. 决策一：身份——跨域协调者，不是第二棵预算树

- 新包 `app/services/governor/`（全新文件；唯一强集成点 `ToolDispatchService.dispatch`
  的执行块内 ~15 行，`_get_governor_adapter` 懒构造 + kill-switch
  `GOVERNOR_TOOL_SURFACE=0` 整体直通）。该文件不在任何并行 PR 热区
  （#1274 动 `agent_pi_bridge.py`/`tools/registry.py`；#1273+capability-graph 动
  `gis_harness/planner.py`/`recipes.py`/`tools.py`——governor 完全不触碰）。
- geocompute `ResourceGovernor`（rows/bytes/nodes L1 权威）**不复制不替换**：governor
  通过 `live_memory_total` 等只读视图消费其语义空间里 harness 特有的维度
  （token/browser/export/wall-time/render-work/external-calls）。
- token 估算单一语义仍在 `context_budget.py`；governor 只做记账
  （`context_assembler.py` 的 8 行只读 hook）与优先级提示。

## 3. 决策二：契约（rg.v1）——未知绝不等于 0

`contract.py`：六类核心类型（Estimate/Demand/Budget/Reservation/Usage/Decision）+
`Certainty ∈ {known, estimated, unknown, unavailable}` × range（min/expected/max）+
confidence + source + reason。**unknown 维按保守地板计费**（`CONSERVATIVE_FLOORS`，
与 DF 结果界/TOOL_TIMEOUT 同量级），unavailable 维不参与判定但留痕。全部封闭词表
（Subsystem/ResourceClass/AdmissionDecision/RetryClass/CancelReason/DegradationSemantics）。

## 4. 决策三：准入——确定性策略链 + 双模式 + fail-open

`admission.py`（策略）+ `governor.py`（facade）：硬预算违规→reject；全局内存压力→
light defer / heavy degrade / remote-only reject；provisional 违规→留痕+降级建议
（**校准前不硬拒**，ADR-0159 同款 provisional 纪律）；provider 熔断 open→degrade
（local_fallback 建议）；SLO breach 积压→accept_with_limits（G12 的升级通道）；通道
积压→预测性 defer。`GOVERNOR_MODE=observe` 为回滚 kill-switch（决策照算、放行执行、
留痕 observe_pass）；governor 内部任何异常 → fail-open 放行（`governor_internal_errors`
计数）。defer 语义 = 进程内排队（`backpressure.py` 的 max_wait 上界；超时升格
degrade——**拒绝无界等待**）。

## 5. 决策四：背压与公平——三层 + 加权 + aging + small bypass

- 三层：session（并发 + heavy 上限，全部等待有 max_wait 上界）/ subsystem 通道
  （raster/browser/export/external/llm/heavy 六通道）/ global（通道容量本身）。
- 每通道内嵌 `FairScheduler`：虚拟起始时间加权公平 + aging（等待超阈值线性升权）+
  **small bypass 预留槽**（估时 < 5s 的任务永不排在 giant raster 队头后）。
- 与 ADR-0100 `_SessionWaveGate` 的关系：外层协调，不替换。
- 取消：`CancellationCoordinator` 挂靠 `app/lib/cancellation.py` 主干；cancel 后
  （a）pending 不启动（入场检查 + 排队候补唤醒）（b）reservation/背压槽位立即归还
  （c）RetryBudget token 清零——**用户取消后重试必须停止**。
- RetryBudget（R10）：进程 + 会话双层令牌；retry 与 fallback 互斥
  （`DENY_DEGRADE_TAKEN`）；对既有 ≥10 处重试点 V1 不逐一改造（观测先行，避免热区
  扩散），第一消费方是 governor 自己的 dispatch 面。

## 6. 决策五：降级与计划提示——建议不落刀

- `degradation.py`：8 动作封闭阶梯（sample/coarsen/simplify/interactive-first/
  deterministic-judge/local-fallback/essential-views/reduce-DPI），每步强制携带
  `semantics ∈ {comparable, approximate, non_comparable}` + reason codes +
  节省倍率。执行权在调用方——**绝不偷偷改变科学语义**。
- `context_link.py`：R8 `ResourceAwarePlanHint` 窄协议（capability-graph owner 合并后
  适配即获预算感知，V1 零侵入其双热区文件）；KDE 例（exact 4GB vs native 300MB、
  预算 2GiB）有单测锁定。

## 7. 决策六：观测、校准与测试

- `metrics.py`：封闭标签词表（decision/subsystem 通道/retry_class；**无 session-id
  标签**——高基数走 `[resource-governor]` 结构化日志）。estimate-vs-actual 误差比、
  取消延迟、重试/降级计数、通道水位 gauge、内部错误计数。
- `scripts/perf/calibrate_governor.py` + `tests/governor/synthetic.py`：scale 矩阵
  （small/medium/large/extreme）× 六域 corpus，零真实大数据；证据落
  `docs/harness-resource-v1-calibration.json`；预算变更必须经校准证据 + 显式 PR。
- 测试：`tests/governor/` 132 项（契约/估算/背压公平/准入降级/门面适配/接线集成/
  chaos 9 项/多 session 1-4-8-16 压测）。受影响域回归 292 项全绿
  （dispatch/chat/pi-bridge/chaos-engine 面）。

## 8. 明确不做（V1 边界）

- 不做跨进程/多副本聚合（geocompute advisory 层先例：默认关闭）；
- 不做 GPU 维的执行调度（ModelOps 自持语义不变，契约已留 `gpu_*` 维）；
- 不改造既有 10 处重试点为 RetryBudget 消费方（观测先行）；
- 不新增 DB 迁移；不触碰 `gis_harness/planner.py`、`recipes.py`、`tools.py`、
  `agent_pi_bridge.py`、`tools/registry.py`（并行线热区）。

## 9. 回滚

- `GOVERNOR_TOOL_SURFACE=0` → dispatch 面完全直通（代码路径退化为接线前的
  if/else 两行）；
- `GOVERNOR_MODE=observe` → 决策照算不拦截；
- 提交粒度回滚：governor 包为纯新增，接线 diff < 25 行。
