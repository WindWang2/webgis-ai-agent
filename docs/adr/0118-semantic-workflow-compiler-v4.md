# ADR-0118: Semantic Workflow Compiler V4

- 状态：Proposed
- 日期：2026-09-09
- 关联：ADR-0101（GeoWorkflow Recipe & Conformance Foundation）、
  ADR-0104（GIS Harness Autonomous Runtime V4）、ADR-0099（空间科学平台）、
  ADR-0103（Cartographic Design System V4）

## 背景

V3 已建立 Recipe DSL V2（WorkflowProfile）、GIS 任务本体 V3（47 任务/9 域）、
15 阶段确定性编译器与四层回退。但 Phase-0 审计（`.agent-work/workflow-v4/00-baseline.md`）
证实三个核心缺口：

1. **编译器 evaluation-only**：`compile_workflow`（15 阶段）只被评估与测试
   消费；生产走 plan_orchestrator 两段式 planner，compiler 证据
   （ontology 匹配/资格裁决/候选 trace/回退层）生产不可见。
2. **DAG 无类型**：`capability_dag` 是字符串节点投影，无 typed ports、
   无 artifact/CRS/单位语义、无条件分支与并行安全标记。
3. **方法论无一等模型**：任务本体 ≠ 方法论；无「该方法族下有哪些候选
   专业方法、按什么资格裁决排序、无效方法如何拒绝」的契约；义务无
   组合/嵌套继承；无包版本、受影响子图重算与语义 diff。

## 决策

1. **新子包 `app/services/gis_harness/workflow_v4/`**（Epic 专属 ownership），
   10 个确定性模块，全部零 LLM / 零 I/O / 产物有界：
   - `methodology.py`：12 方法族（描述制图/分布密度/插值/分区统计/适宜性/
     网络/地形水文/遥感/变化检测/空间统计/多准则/组合制图）+ 44 审定
     候选方法 + 资格排序引擎（硬准则拒绝 + 确定性评分排序）；
   - `typed_dag.py`：TypedPort/TypedWorkflowNode/Edge/Graph；端口类型
     事实引用 ArtifactTypeRegistry（几何族）、算法层 crs_class（CRS 词表）、
     AlgorithmDescriptor（输入输出 artifact 类型/单位要求）；
   - `compiler_v4.py`：`compile_workflow_v4` = 既有 15 阶段（零改动）+
     8 个 V4 阶段（resolve_methodology → emit_workflow_package）；
   - `obligations.py`：义务继承（去重 + provenance 来源链 + 最强
     on_violation + 科学类 kind 优先；幂等）；
   - `package.py`：WorkflowPackage（canonical compiled form + sha256；
     semver；major 不兼容 → recompile migration）；
   - `parameters.py`：参数从算法层 parameter contracts 提取；user > hint >
     默认；非法值回落 + 披露（不阻塞人工）；
   - `recompute.py`：WorkflowChange → typed DAG 正向闭包 = 受影响子图；
     style 维零科学重算；recipe 维全图失效；
   - `diff.py`：包间语义 diff → WorkflowChange（直接喂 recompute）；
   - `acquisition.py`：有序获取备选声明（local→derived→catalog→STAC→
     provider→upload→synthetic_demo 仅显式 opt-in）；只声明不抓取；
   - `cartography.py`：表达-数据资格义务 5 规则（率需分母/定量密度禁
     原始计数填色/小样本禁分级色/近似不确定性展示/图例来源恒定）；
   - `evaluation.py`：五维编译器评估（族正确性/数据完备性/无效方法
     拒绝/义务完备性/确定性+可解释）。
2. **专业词方法族解析**：任务本体匹配是词汇模糊的；`resolve_methodology_family_for_query`
   用长短语加权专业词全族扫描（密度/自相关/坡度/各区…），允许与任务
   覆盖族分歧（FAMILY_TASK_DIVERGENCE 进 evidence，不静默）；零命中回退
   任务覆盖词表序。V3 ontology 词表零改动（3,240 一致性案例回归保护）。
3. **家族级数据需求派生**：`data_role_demands` 载入期从成员本体任务的
   required_data_roles 并集派生（ontology 单一事实源，不手写），入
   typed DAG 规划期输入（unknown 诚实入图）。
4. **生产接入 = 证据上行**（非替换 planner）：
   - 新 tier-2 确定性语义工具 `compile_workflow_semantics`（advisory 零
     执行）——LLM 面首次可见编译器产物；
   - `AgentPlanOrchestrator` 合成路径附加 `Plan.workflow_v4` 有界语义
     摘要（planner memo 复用近似零成本；失败/未映射 → None，绝不阻塞）。
   15 阶段生产行为零漂移（测试锁定）。
5. **中央校验收编**：methodology registry 并入 `validate_gis_library`
   （task/capability/algorithm/artifact 四谓词对账，悬空 fatal）。
6. **双语方法语料**：`app/evaluation/methodology_corpus.py` 12 族 ×
   中英审定案例（歧义变体 + hard negatives）；期望为语义计划
   （族/角色/拒绝集/义务提示），非工具序列；反泄漏（人工审定工件，
   编译器不可见）。
7. **不落库**：V4 是编译期契约层，packages/diff/recompute 为可重建纯
   函数产物；运行态仍归 workflow_instance（会话章），避免 Alembic head
   冲突（并行 Epic 隔离）。

## 后果

- 正向：「意图→专业方法→typed DAG」全链确定性可审计；无效方法按资格
  事实拒绝（LLM 不得绕过）；义务/制图约束机器化进完成契约；参数变化
  可精确计算受影响子图（runtime 只需消费集合）；同一方法族可选不同
  算法实现而保持方法论契约。
- 代价：编译产物 +22KB 有界摘要；V4 阶段 +8（全链 <2s 护栏，实测
  ~几十 ms 级）；语料/族关键词需随方法论演进而维护。
- 风险与边界：V4 编译器仍是「声明+证据」层，执行/恢复/验证归 Harness
  runtime（V5 Epic）；recompute 只产出集合，调度与产物复用校验由
  runtime 兑现；CORPUS 义务维度目前以恒定义务为主，composite 义务链
  进语料列为 follow-up。

## 验收（全部本 PR 内落地）

- 12 方法族 / 44 候选方法 / 0 悬空引用（validate_gis_library 0 issues）
- 编译器评估：37 表述五维全绿（确定性双编译同指纹）
- 15 阶段 COMPILER_STAGES 契约零改动（既有测试锁定通过）
- gis_harness 全量 916+ 绿；新增测试 ~80；V4 模块覆盖率 89-100%
- 两轮独立 review 无未修 BLOCKER/CRITICAL/MAJOR
