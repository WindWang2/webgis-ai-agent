# ADR-0101: GeoWorkflow Recipe & Conformance Foundation

- 状态：Proposed
- 日期：2026-09-06
- 关联：ADR-0092（可复现专业 GIS 运行时）、ADR-0098（语义产品族 V2）、
  ADR-0099（空间科学平台 VNext）、ADR-0081/0086/0091（完成管线 / 渲染观察）

## 背景

Pi + SessionPlan + GIS Harness + CartographyRecipe + Algorithm/Capability
Registry + Map Product 体系已建立「LLM 理解语言、代码保证 GIS 语义」的
运行时。但方法知识面仍窄：17 个 recipe seed、18 个任务族、306 个离线
评估案例，专业领域（地形/水文/SAR/空间统计/时序/自然资源…）没有一等
工作流；数据需求没有角色语义；完成判断停留在「工具跑过 + 渲染有效」，
缺少科学维与披露维；workflow 语义变化对旧计划不可感知。

## 决策

1. **Workflow Recipe DSL V2（additive）**：`CartographyRecipe` 挂载可选
   `WorkflowProfile` —— 数据角色（15 词）、科学义务（联动算法层
   preconditions，不重复实现）、完成契约维度（7）、语义回退策略（5 级
   降级分类 + 强制披露）、专业路由关键词、内容指纹。`workflow=None`
   的 V1 recipe 行为不变。
2. **大规模领域包**：24 个领域模块、147 个专业 recipe（总量 164）。
   所有引用（capability / map model / artifact / precondition）经
   registry 校验，capability 悬空 fatal。
3. **数据角色系统（C3）**：角色 → 获取通道 → 缺失策略（block / degrade）
   → 机器可读 reason code 与披露；`must_not_guess` 默认真。
4. **科学义务联动（C4）**：`kind=precondition` 委托
   `scientific_preconditions.evaluate_precondition`（五值裁决）；
   unknown ≠ unsatisfied；transformation/disclosure 类义务规划期即披露。
5. **确定性工作流编译器（C5）**：12 阶段（normalize_intent → … →
   completion_contract），每阶段 reason codes + 有界 evidence；
   编排既有组件，零 LLM/零 I/O，不执行。
6. **回退语义（C6）**：三层回退统一产出 `FallbackDecision`（新增
   downgrade_class / disclosure）；`not_allowed` 降级必须阻断完成。
7. **完成/Verdict V2（C7）**：七维完成契约；workflow 硬违反在合成完美
   渲染面上仍裁决 BLOCKED_BY_METHOD / BLOCKED_BY_DATA（压档不升档）；
   无 workflow 的会话行为不变。
8. **Analysis Graph 可解释性（C8）**：goal 节点携带有界 workflow 块
   （角色/义务/阻断），纯 projection。
9. **Manifest 指纹修复（C12）**：recipe 投影从空壳（历史读不存在的
   `r.capabilities/r.task`）修正为真实编排面 + 内容指纹；recipe→capability
   悬空 fatal 自此真正可达；MANIFEST_VERSION 保持 3（ADR-0099 锁定），
   指纹内容自然变化即触发 plan stale。
10. **一致性语料库（C9）+ 反声明（C10）+ 场景（C11）**：47 个人工审定
    语义族 × 确定性表述扩展 = 3,540 plan-tier 案例；反声明契约
    （分母/准则/受体/显著性/代理语义）；11 个 workflow 契约案例；147
    recipe 编译覆盖 sweep；7 个端到端确定性场景。
11. **路由守卫**：seed 资历层 + V2 通用罚 + 专业关键词倒排索引 ——
    通用短语的产品族契约零漂移（306 案例回归锁定），新任务族与专业词
    命中时专业工作流直接路由。

## 后果

- 正向：专业 GIS 方法面扩容 ~10×；科学红线机器可断言；完成语义可审计；
  workflow 语义演进对旧计划可感知（stale）。
- 代价：manifest 指纹变化（旧持久化计划一次性转 stale —— 即设计目的）；
  语料库与 family 表需要随产品语义演进而维护。
- 风险与边界：V2 recipe 在 V1 seed 服务的任务族内只能经关键词/hint
  路由（保守策略，避免通用表述漂移）；预期随项目记忆（ADR-0069）与
  后续任务族细化逐步放开。

## 验收（全部在本 PR 内落地）

- recipe/workflow library：17 seeds → 164（24 领域包，147 V2）
- 全部测试绿灯：既有 816 + 新增 ~60（schema/compiler/corpus/场景）
- 离线语料：3,540 一致性案例 + 7 反声明 + 11 契约 + 7 场景，零 LLM
- 两轮独立 review 后无未修 BLOCKER / CRITICAL / MAJOR
