# G02-GIS-EXECUTION-COMPILER PLAN

使命：在既有 RequirementIR / analysis_workflows / planner / goal_satisfaction 之上，补齐
**自然语言需求 → 受约束的能力/算法选择 → 可重算任务图 → 科学与地理验收** 的确定性执行
编译闭环。不新增第二 planner、不新建第二 orchestrator、不重做已合并 PR。

## 架构决策（M2 细化见 DECISIONS.md）

新增一个纯函数编译层 `app/services/gis_harness/execution_compiler/`（IR → 执行面投影），
三面输出、全部 additive：

1. **requirement→task 编译**（GC-01/02）：`RequirementDocument → TaskCompilation`
   （确定性族候选 + 数据角色绑定需求 + 表达候选与权衡理由 + closed-vocabulary verdict）。
   消费既有 families/plan_workflow 词表，LLM 不再自由选族（工具面默认消费编译器
   top 候选，显式 override 必须披露）。
2. **族声明 conformance**（GC-03）：`families ↔ execution_catalog/ToolRegistry`
   一致性审计（悬空工具/能力/单位词表外 → fail-closed 裁决投影 + registry 摘要）。
3. **验收桥**（GC-08）：`WorkflowRunState → chapter goal-evidence` 只读投影 +
   IR patch → 受影响 capability/step 失效集（GC-09）。
4. **单位对账**（GC-05）：IR measure/statistics 词表 × family unit_contract ×
   算法参数单位 的静态义务矩阵 + #2005/#2006 类合约回归语料。
5. **基准**（GC-10）：`execution_compiler` 端到端 golden 语料 ≥20 类 + runner。

## Oracle 表（完成判定）

| ID | Oracle | 验证方式 |
|---|---|---|
| O1 | IR→TaskCompilation 确定性：同输入逐字节同输出；零 LLM/IO | 性质测试（重复执行 diff=∅） |
| O2 | 零悬空引用：编译产物中 family/tool/capability/单位词全部可解析；不可用→fail-closed+理由 | 语料断言（含工具下线 fixture） |
| O3 | 表达候选推导：点/密度/分区组合从数据规模/几何/统计词表推导；用户显式要求 vs Agent 推荐分离 | golden 断言 + 反例（数据不足禁密度） |
| O4 | 验收桥：family 步骤 satisfied ≠ goal satisfied；关键义务（CRS/单位/来源/图层绑定）缺席 → partial/blocked | goal_satisfaction 语料扩展 |
| O5 | 单位义务：秒≠米、计数≠率、点≠密度 类错误被静态对账拒绝 | 合约回归（#2005/#2006 形态） |
| O6 | 选择性重算：改色→零数据步骤失效；换区→AOI 链失效、无关步骤复用 | 失效集断言（复用证据） |
| O7 | 跨城市/多图层范围不假匹配：AOI 切换 → 旧 ref 失效 | 反例语料 |
| O8 | golden 语料 ≥20 类：结构化 PlanGraph + dispatch trace + typed artifact 引用 + final verification | runner 报告 |
| O9 | 既有测试全绿：本方向改动不破坏 master 基线（改动范围 pytest/ruff/mypy） | 本地回归 |
| O10 | 独立 review 无未处理 P0/P1 | 子代理 B 冷读 |

## 里程碑

- M0 审计 ✅（BASELINE.md）
- M1 characterization：失败测试钉住 4 条断链（IR 不进编译/族自由选/回执不可见/无单位对账）
- M2 ADR + 接口契约（DECISIONS.md）
- M3 编译核心（requirement→task + conformance）
- M4 验收桥 + 重算失效集
- M5 单位对账 + 合约回归
- M6 golden 语料 + runner + 报告
- M7 压力/异常/取消/并发
- M8 独立 review
- M9 同步 master、回归、PR
