# G02-GIS-EXECUTION-COMPILER REVIEW（M8 独立评审）

评审人：子代理 B（独立冷读，不复用主实现结论；探针脚本实证 + 本地实跑）
范围：`git diff origin/master...HEAD` 全部变更文件逐文件 + 20 文件冷读
基线复核：requirement_ir 218P/1S、finalization 13P、pipeline 邻接 50P 绿

## 裁决总览

| 级别 | 数量 | 处置 |
|---|---|---|
| P0 | 0 | — |
| P1 | 3 | **全部修复**（commit db5303b17）+ 8 个回归钉测试 |
| P2 | 7 | 6 项修复/文档化，1 项记录为已知边界（见下） |

## P1 清偿明细

- **P1-1 陈旧运行遮蔽 + 跨目标污染**（acceptance_bridge）：load_workflow_runs
  改为每族最新运行获胜（updated_at 降序 + workflow_id tie-break）；枚举硬
  上限 16。修复前探针实证：同族旧失败/新成功并存 → goal 得 FAILED +
  每轮 finalization 反复驱动 replan。**跨 goal 边界诚实披露**：运行态无
  goal 身份，按每族最新收敛污染面（≤7 运行）；goal 级关联需 plan 工具面
  写 goal 键（非本层单方面可修）——已记录为已知限制。
- **P1-2 「最近」时间词假阳性**（build.py）：`_NEAR_TIME_RE`/`_NEAR_ADMIN_RE`
  负向先行排除——「成都最近三年的小学数量变化」不再误置 near（探针曾复现
  network_proximity 6.0 登顶）；「最近医院」仍正确命中；「邻近城市」排除。
- **P1-3 换城失效面不达 GC-06**（invalidation）：回退集改 DATA_ROLES 词表
  派生（16）；subject→{subject,facility,hazard,receptor}；scene_data→
  elevation。**ref/图层 id 粒度**：InvalidationScope 保持角色粒度，ref 级
  由 recompute 内容指纹承接——文档明示该边界（不虚报覆盖面）。

## P2 处置

| # | 处置 |
|---|---|
| 1 blocked 自然路径不可达 | 修复：none 默认值不再计分；`no_family_signal_match` 自然可达（回归钉） |
| 2 violated 死词表 | 修复：词表收口三态；SCIENTIFIC_ORACLES 超卖段改写（运行期矛盾归 run_final_gate） |
| 3 heatmap 披露串反义 | 修复：`feature_count=5 < MIN_DENSITY_POINTS=30`（回归钉） |
| 4 锁内 store IO | 修复：锁外预取 awf_runs，锁内纯内存合并 |
| 5 收割半径披露 | 机制有披露；半径（分级/人均等词触发 origin=user → agent 改字段 PatchConflict）已写入 PR 风险节 |
| 6 C3 无生产接线 + 文档残段 | 修复：audit_family_conformance 接入 validate_gis_library（fail → issues 行，doctor/启动面可见）；残段删除 |
| 7 杂项 | measures.* 聚合命中面/isoline+flow 映射移除/文档权重 6.0/conformance 死代码/side_effect/ int 守卫/测试恒真断言 全部修复 |

## 已核查无问题面（评审人结论摘录）

状态映射与 goal 消费面一致；合并不写回持久 chapter、行指纹守卫未被绕过；
fail-closed/三态（suitable=None 无下游当 True）；additive 回归全绿；
测试真实性抽查（goal 桥三测/city-switch 精确步骤集/密度三态门槛为真锁定）。

## 结论

**无未处理 P0/P1**。P2 全部修复或文档化。合并条件满足。
