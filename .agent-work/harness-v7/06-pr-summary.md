# Harness V7 PR Summary — Long-Horizon Contextual GIS Agent Runtime

分支 `feat/harness-v7-agentic-runtime`：2aabdc43..HEAD（12 commits，含两轮 review 修复）。
本任务不等待/不依赖线上 CI/CD，全部本地验证（见「本地测试」）。

## Problem / Motivation

V4-V6 交付了 typed DAG、可恢复 runtime 与语义自治，但生产编排事实散落在
触发点：没有单一状态机回答「任务处于认知闭环哪一步」；replan 有裁决词
无生产驱动点与预算；finalizer 不直接消费 decide_continuation；
`intent_verified=(status=="complete")` 循环论证；harness 对 12 个已注册
子代理角色零程序化委派；检索缺结构化前置/后置条件与可靠性反馈；金标
语料 598 条硬编码行（V6 follow-up 已预告不可持续）。基线审计
（00-baseline.md，file:line 证据）确认上述缺口并给出 17 处附着接缝。

## Architecture（ADR-0130，docs/adr/0130-gis-harness-v7-agentic-runtime.md）

目标闭环：`Intent → Context Assembly → Planning → Capability Retrieval →
Execution → Observation → Critique → Repair/Replan → Partial Recompute →
Finalization → Context Commit`

- **D1** 任务级状态机：12 态封闭词表 + 合法转移表（文档/测试 oracle/
  命令式 fail-closed）；阶段 = 章节权威事实的确定性派生（非第二状态源）；
  additive 单键持久化 + gate 指纹幂等 + 锁内漂移守卫；suspended 旗标
  （turn 收尾未终态可恢复）。
- **D2** PlanRuntime：计划指纹（goal+rows+contract）→ 版本/历史/回滚点；
  replan 预算（LOOP_BUDGETS.replan=1）与生产驱动点（request_replan，
  finalizer 出口）同一 commit；失败种子最小重算清单（下游闭包 + reuse）。
- **D3** ContextLayers：九域确定性投影（全 rebuildable）；durable 侧预算
  （域上限 → 总预算裁剪序，违规留痕）；单键 checkpoint + revision +
  指纹；锚点只带域摘要（context_digest durable 键）。
- **D4** CapabilityDescriptors：跨 registry 只读统一描述符（前置/后置/
  cost/fallback 链）；select_capabilities（前置硬过滤 + CJK 二元组 +
  可靠性罚分）；tool_surface 6.7 门控信号（kill switch 逐位回退 V6）；
  生成式场景语料（域包×槽×确定性展开 2316 ≥2000 门 + registry 锚定
  校验 + 覆盖门 + stride 抽样 p@1）。
- **D5** MapCritique：blank_map / invalid_bounds / 出版件三件套（修复
  路由）/ label_collision（既有阈值）/ overlay 错位；finalizer 增值并轨。
- **D6** Finalization Hook：意图独立三面验收（verdict/desired/observed；
  observation 缺席诚实降级 intent_verified）；终验出口直连
  decide_continuation（V6 follow-up 兑现）+ replan 路由；READY → 上下文
  提交；最终显示确认钩子（auto 默认，required 模式 ack + 代次比对）。
- **D7** Delegation：handoff schema + 父侧台账（环形 ≤8）+ 确定性失败
  回收（预算内一 retry，再败诚实披露）；QA 驱动点默认关（env 显式开），
  同成品 revision 幂等。

生产链路闭环：
`intent → runtime phase 推进（三既有触发点）→ plan version → tool
dispatch（V6 hybrid + V7 描述符信号）→ failure → durable 记账 →
repair/replan/recompute（依赖感知最小重算）→ observation → critique →
finalization（意图验收 + continuation 裁决）→ READY → context commit →
下一 turn 增量继续（锚点 + 九域摘要）`

## Implementation waves

W0 审计 → W1 状态机 → W2 计划运行时 → W3 上下文分层 → W4 能力检索 +
语料结构 → W5 地图批评 → W6 终验钩子 → W7 委派 → W8 ADR/CHANGELOG →
W9 两轮 review（自审 3 项全修 + 独立评审）。

## Key code paths

- 新增 `app/services/gis_harness/{runtime_state_machine,plan_runtime,
  context_layers,capability_descriptors,map_critique,intent_acceptance,
  display_confirmation,delegation}.py`
- 新增 `app/evaluation/scenario_corpus.py`
- 触点：`completion/pipeline.py`（acceptance/continuation/commit 并轨）、
  `durable_context.py`（replan 预算 + context_digest 键）、
  `resume_anchor.py`（九域摘要入锚）、`tool_surface_v3.py`（6.7 信号）、
  `agent_pi_bridge.py`/`api/routes/chat.py`（三触发点 + turn checkpoint）、
  `session_plan.py`（投影行）
- 测试：`tests/unit/gis_harness/test_{runtime_state_machine,plan_runtime,
  context_layers,capability_descriptors,map_critique,intent_acceptance,
  delegation}_v7.py`（78 项新测试）

## Data / persistence changes

- **零新表、零 migration**。新增 session 面 additive 键：
  `gis_chapter["runtime_state"|"plan_runtime"|"delegations"]`、map_state
  `_context_layers`/`_final_display_ack`；既有块 additive JSON 键：
  锚点 `context_digest`、`map_product.intent_acceptance`/`continuation`。
  旧读者忽略；删块可由权威状态重建（测试钉死）。

## API / contract changes

- 无 breaking。task_complete/read_stored 载荷 additive 键
  `display_confirmed`；`map_product` additive 键如上。语义收紧一项（有意）：
  observation 缺席时 `intent_verified=False`（原自证 True）——
  semantically_correct 晋级需真实渲染证据。
- Kill switches：`GIS_RUNTIME_STATE_MACHINE=0`、
  `GIS_CAPABILITY_RETRIEVAL_V7=0`、`GIS_FINAL_DISPLAY_CONFIRM=required`
  （默认 auto）、`GIS_HARNESS_DELEGATION`（默认 0）。

## UI changes

无前端改动。新投影行（[GIS Runtime]/[GIS PlanRuntime]/[GIS Delegation]）
走 SessionPlan projection 既有通道；前端 observation telemetry 契约不变。

## Security implications

- 委派 role fail-closed（未知角色拒绝 spawn）；预算档/工具交集/递归深度
  全由 SubagentDispatcher 既有语义承载。
- 全部新载荷有界截断；无新外部输入面；ack 键仅显式 API 写入；
  LLM 依赖不进终验热路径（delegation 默认关）。

## Local test matrix（exact results）

- changed-scope 全量：**1254 passed / 10 failed / 4 skipped**（10 项与
  master 基线同因 = fcntl/h3 Windows 环境缺失，非本分支回归；
  基线 1103 passed → 1254 = +151 新测试全绿）
- V6 检索/面回归：retrieval_eval_v6 + semantic_retrieval_v6 +
  tool_surface_v3 + pi_native_surface（56 passed；1 项 fcntl 环境红在
  master 同样存在）
- finalizer/observation/verdict 回归 101 全绿；map_completion/
  map_verification 87 全绿
- ruff（app + tests）全绿
- 运行方式：`bash .agent-work/harness-v7/run-tests.sh <paths> -q`

## Performance

- 状态机/计划运行时/上下文 checkpoint 全部 gate 指纹幂等（无变化零写）；
  capability index 进程级缓存；corpus 全量展开仅构建期（2316 条 <0.1s），
  评测走 200 stride 抽样（<1s）；新增持久化为单键小 JSON（≤4KB）。

## Risks & rollback

- 三枚 env kill switch + display 模式 env 逐位回退；additive 键可被旧
  代码安全忽略；失败面全部「只少披露不阻断」。
- intent_verified 收紧为有意修正：无渲染证据的会话从（错误的）自证完成
  变为诚实未证实 —— 由 observation_health 的 unknown/blocked 投影承接。

## 与并行方向的边界

- 不碰：workflow compiler 本体、GeoCompute scheduler、渲染器、extensions、
  chat context 组装既有契约（budget/assembler 只读复用）。
- chat 侧唯一触点为 tool_surface_v3 的 gated additive 信号；共享 schema
  零变更；ADR 独立编号 0130；CHANGELOG 独立段。

## Follow-up candidates

- delegation QA 驱动点接入终验批评面的自动化开启评估（现 env 显式）。
- scenario corpus 域包扩充至全部 139 capability（覆盖门已会自动亮红）。
- display confirmation 的前端 ack 端点（后端 seam 已就绪）。
- [GIS Runtime] 行接入 v6_context_blocks 的结构化投影。
