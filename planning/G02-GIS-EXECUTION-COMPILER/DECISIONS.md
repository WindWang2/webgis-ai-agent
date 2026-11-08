# G02-GIS-EXECUTION-COMPILER DECISIONS

## D1 编译层定位：投影而非裁决者
`execution_compiler` 是 RequirementIR → 执行面的**纯函数投影层**。族候选
排序是证据投影（信号带 IR 路径溯源），显式 override 由工具面披露；不
新建第二 planner/orchestrator/注册中心/验收器。`plan_workflow`/goal_
satisfaction 的裁决权威不变。

## D2 权重分级：载荷类型 > 语义任务 > 泛化聚合
`_TASK_TYPE_FAMILY` 权重三级：5.0（raster/spatial_equity/risk_exposure
——科学适用性硬约束，矢量聚合无法消费栅格/率语义）、3.0（领域任务）、
关系信号 6.0（spatial_relation.kind 是 IR 权威关系裁决）+ raster 主题
对矢量族 role_kind 硬排除（编译期 fail-closed，与 ADR-0284 D3 同判）。

## D3 GC-01 genesis/edit 收割的边界
只收割**既有权威词表命中**（harvest_geometry_kind/normalize_normalization
/`_NEAR_CUE_RE` 封闭词面）；词表外 = 未表达零推断。diff 路径不传 query
的调用面零漂移；edit 轮口径/表达 cue 走 patch journal（白名单既有路径
`statistics.normalization`/`representation.geometry_kind`）。

## D4 验收桥：评估视图合并，不改持久 chapter
`evaluation_view_with_workflow_rows` 在 completion/pipeline 的持久化点把
族运行行合并进**仅供评估**的 chapter 视图；行指纹守卫消费 fresh.
gis_chapter 原面——合并零写回、零漂移。行 capability 命名空间化
`awf.<family>.<step_id>` 与 plan 行零冲突；goal_satisfaction 零改动
（derive_goal_contract/_row_status_evidence 既有合同消费投影行）。

## D5 单位义务分层：声明层静态 vs 运行期权威
units.py 只做 IR 统计语义 × 量纲表的**声明性对账**（missing_denominator
四态封闭）；五值判定/终局门/envelope 实测口径留在既有层。#2005/#2006
形态做合约回归语料（秒≠米/计数≠率），不做独立 issue 抢修。

## D6 失效面与内容指纹互补
patch_invalidation_scope 是**需求语义差分**（角色/步骤语义身份），与
recompute.py 的**内容身份**差分互补；本层输出是执行计划失效种子的上限，
两层都过才是最小重算。换城市=全量角色失效（GC-06 跨城不假匹配）。

## D7 语料 Oracle 的期望来源
23 任务类期望值 = 科学正确的编译结果；与实际不符时修**编译器**（权重/
映射/收割），不改期望迁就现状（如 risk_exposure 映射、raster 排除、
near 权重、edit 口径 patch 均为修复产物）。

## D8 文档由独立子代理起草并回灌一致性
GIS_COMPILER_PIPELINE.md / SCIENTIFIC_ORACLES.md 由子代理冷读代码起草；
其报告的一致性风险（`__init__` 未 re-export 桥接函数致生产静默降级、
FC 死常量）均已修复并回归钉死（oracles 测试 test_evaluation_view_symbol_
importable_from_package）。
