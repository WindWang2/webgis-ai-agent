# Workflow V4 — Progress Log

## Waves 完成（全部本地验证）

| Wave | Commit | 内容 | 测试 |
|---|---|---|---|
| 1+2 | 1be24fac | methodology：12 方法族 + 44 候选方法 + 资格排序引擎；DATA_FIT_SCORE 公共别名 | 13 |
| 3 | 166656b6 | typed DAG：typed ports（artifact/几何/crs_class/单位）+ 校验 + 确定性构建 | 6 |
| 4 | 1e77a0cc | compiler_v4：wrap 15 阶段 + resolve_methodology/select_method/compile_typed_dag | 6 |
| 5+6 | 45eda3d2 | obligations 继承链（provenance/幂等/最强语义）+ WorkflowPackage（semver/指纹/兼容性） | 10 |
| 7-10 | 6cabf10d | parameters/recompute/diff/acquisition/cartography + compiler 阶段 19-23（共 23 阶段） | 16 |
| 11 | f5e4a825 | 双语方法语料（12 族 × zh/en × 歧义/负例）+ 五维编译器评估 + 专业词族解析 + data_role_demands 派生 | 9 |
| 12 | 82525791 | 生产接入：compile_workflow_semantics 语义工具 + AgentPlanOrchestrator.workflow_v4 证据 | 6 |

累计新测试 66+；gis_harness 全量 904→931 绿。

## 关键设计实现记录

- 资格排序权重：role_fit 0.40（复用 DATA_FIT_SCORE）/ method_quality 0.30 /
  priority 0.20 / precondition 0.10；rejected 完整保留 + 稳定 reason codes。
- typed DAG：结构依赖 = 顺序约束元数据（不生成数据流边）；端口类型兼容只在
  真实数据流边裁决；产出者 = 解析算法 output_artifact_type 匹配者。
- 方法族解析：专业词长短语加权全族扫描（允许与任务匹配分歧 →
  FAMILY_TASK_DIVERGENCE evidence）→ 零命中回退任务覆盖词表序。
- recompute：WorkflowChange → 正向闭包（数据流边 ∪ depends_on）；style 维度
  零科学重算；recipe 维度全图失效。
- 生产接入取「证据上行」：零行为漂移（compilation/plan 语义不变，仅 additive）。

## 已知风险记录（review 前自查）

- plan_orchestrator 证据经 try/except 全包裹（失败 → None），但 V4 编译在
  大 query 下成本未测 —— Phase D 需基准。
- corpus 义务维度只覆盖 legend/source 恒定义务（7 例）；更深义务链组合
  （composite 义务）待 Wave 5 语义进语料 —— 记为 follow-up 候选。
- evaluation.py 消费 base.data_roles bounded dict 的 role 键 —— 若 V3 改
  bounded 形状需同步（低风险：有测试锁定）。
