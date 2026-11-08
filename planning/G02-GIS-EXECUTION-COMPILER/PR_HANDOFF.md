# G02-GIS-EXECUTION-COMPILER PR HANDOFF

- worktree：`../webgis-wt-gis-execution-compiler-20261107`
- branch：`zcode/goal-gis-execution-compiler-20261107`（base `origin/master` `585812ee4`）
- commits：审计→实现→测试/文档 三段语义化提交
- PR base：master

## PR 摘要（body 底稿）

**问题**：自然语言→执行的真实链路上有 4 条断链（BASELINE.md）——
RequirementIR 不驱动族选择（LLM 聊天自由选族）、analysis workflow 运行
回执对 goal 验收不可见（节点成功≠目标达成不可验）、族声明无生产
conformance、IR patch 无失效面推导（改色可能重拉数据）。

**实现**：新增纯函数编译层 `execution_compiler`（零 LLM/IO、bounded、
同输入逐字节同输出）：
- `compile_requirement_to_tasks`：族候选（信号溯源）/表达候选
  （user-vs-recommended）/单位义务（missing_denominator fail-closed）
- 验收桥：WorkflowRunState → chapter 行投影（命名空间化）→
  goal_satisfaction 零改动消费；completion/pipeline 评估视图合并
  （不写回持久 chapter）
- `audit_family_conformance`：七族×步骤×注册表 fail-closed 对账
- `patch_invalidation_scope`：style-only 零科学失效/换城全量/统计最小集
- GC-01 genesis/edit 收割（geometry_kind/normalization/near 封闭词表）
- `webgis_requirement_compile` 只读工具
- golden 语料 23 任务类 + Oracle 49 测试

**兼容性**：全部 additive——旧文档/旧计划/旧章节零迁移；diff 路径不传
query 的调用面零漂移；桥零运行时按原 chapter 评估（零漂移回退）。

**证据**：49（新）+3377（gis_harness 全量）+335（completion/goal）+
2779（registry/tools）+2591（evaluation 语料）本地全绿；ruff 0；mypy
=master 基线；语料报告 `reports/execution_compiler_benchmark.md`
（本地工件，23/23）。

**风险与未验证**：线上 CI 不等待不宣称；Pi 端到端联调未做（工具注册
面已验）；binding_unbacked 棘轮 63→64（同型披露，见测试注释）。

## 检查表
- [x] BASELINE（实时 master/PR/分支/Issues 审计 + 冲突矩阵）
- [x] characterization 失败测试（断链实证）→ 全绿
- [x] Oracle 表 O1–O8 达成（TEST_MATRIX.md）
- [x] 独立评审（REVIEW.md）→ P0/P1 清偿
- [x] 最终回归 + 推送 + 真实 PR
