# PR_OVERLAP_MATRIX — GOAL 05

基线 fetch 时间：2026-11-02；origin/master = 6c5e71c2c（PR #1875 合并后）。

## 相关 merged PR（能力可复用，不重建）

| PR | 主题 | 与本任务关系 | 裁决 |
|---|---|---|---|
| #1762 | G05 任务验收 lab | 复用 ScenarioContract/CheckVerdict/failure categories 九类 | reuse（语料期望值口径） |
| #1868 | 多源数据准备 sdo | eligibility/prepare 链上游 | reuse 接口（data_qualifications 投影） |
| #1869 | 分析工作流合同 + 选择性重算 | M5 只重试失败子图的执行底座 | reuse（`webgis_analysis_workflow_recompute` + recompute 指纹） |
| #1870 | 制图候选/设计决策 | 信号 repair_cartography 的既有降级通道 | reuse |
| #1871 | 图册 durable job | 定向重试先例 | avoid（不建新 durable 引擎） |
| #1873 | 要素编辑预览/确认/撤销 | M2 写操作守卫的确认入口语义 | reuse（tier3/确认门复用） |
| #1874 | map operation 事务 + FinalDisplayConfirmation | 恢复动作走既有 mutation 通道的依据 | reuse |
| #1875 | linked views/label/export | export receipt 证据源 | reuse |

## Open PR（无文件冲突，已核对 changed files）

| PR | 主题 | 文件交集 | 裁决 |
|---|---|---|---|
| #1876 | GOAL 01 spatial-semantics（open，无 review） | 无（其改 spatial_semantics/ + tools；本任务不触碰） | avoid |
| #1877 | GOAL 03 temporal（open，无 review） | 潜在轻微：其改 mapspec_schema/mutation_handlers/intents；本任务只在 completion/pipeline 加 additive 块，不与时间轴耦合 | avoid（不改 temporal 契约） |
| #1878 | GOAL 02 OGC/COG 数据底座（open；2026-11-02 复查新增） | 无（data_fabric/ogc 工具/sidebar 前端；本任务不触碰） | avoid |

master 复查（2026-11-02）：origin/master 仍为 6c5e71c2c（PR #1875 合并后），无前进，无需 rebase。

## 本任务新增 vs 既有系统

| 能力 | 状态 | 说明 |
|---|---|---|
| GISRecoveryDiagnostic.v1（6 处置 + 封闭 reason codes） | new | 既有 HarnessFailureClass 11 类保持不变，诊断层引用映射 |
| RecoveryPolicy（风险×处置×预算纯决策 + 副作用守卫） | new | 不执行；执行归 LLM 自愈/runtime_repair/finalizer/recompute/用户确认 |
| goal_verification（L1-L5↔goal 对账） | new | 消费 ADR-0183 报告 + observation_states + roles/data_qualifications |
| 恢复解释（what_failed/safe_to_try/needs_you/already_done） | new | 从诊断+goal 报告派生，进既有 SSE/投影通道 |
| recovery_corpus 25+（输入状态/允许/禁止动作） | new | 复用 ScriptedCall/simulate_agent_loop 机制 |
| TDS `recovery` additive 键 | extend | 挂在既有 harness_failure 之后 |
| [GIS Recovery] 投影行 / step_error 扩展字段 | extend | additive 单行/字段模式 |
| trace 阶段 14/15/16 发射 | extend | 补齐既有 Stage 词表的缺发射点 |
| 第二 trace store / 第二 runtime / 新调度引擎 | **不做** | 红线 |
