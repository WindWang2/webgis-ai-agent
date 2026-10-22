# G01 REVIEW — 独立审查记录（review → fix → re-review）

审查员：独立 subagent（未参与实现，只读）。轮次 2。

## 第一轮（@ e761a9bb）

发现：0 P0 / 3 P1 / 6 P2 / 6 P3。

| 级别 | 位置 | 问题 | 处置 |
|---|---|---|---|
| P1 | preflight.py verdict | unknown 判据被读成 compatible（违反 unknown ≠ pass 契约） | ✅ 修复（verdict 收敛 gaps>unknown>compatible；静态无时间证据 not_applicable 不误伤；单侧时间证据按任务角色披露） |
| P1 | preflight.py 时间 | coverage_end 不可解析塌缩成起始年 → 假 TEMPORAL_DISJOINT | ✅ 修复（不外推端点；月精度比较；年粒度降 partial） |
| P1 | wiring 测试 | `assert X or True` 恒真 + 编译链 threading 无断言力 | ✅ 修复（facility_coverage_ratio 真实角色 + 破坏性验证敏感） |
| P2 | role_binding | 证据推导路径不做 stale 对账（生产入口 stale 不可达） | ✅ 修复（绑定命中即按记录面锚对账；e2e 测试钉扎） |
| P2 | semantic_tools | 重编译同时传 primary descriptor 中和「不顶替」 | ✅ 修复（重编译 descriptor=None，角色证据为权威） |
| P2 | preflight | 零生产调用方 | ✅ 修复（semantic_tools 输出 data_preflight 字段） |
| P2 | role_binding/bridge | 基础设施故障静默降级无日志 | ✅ 修复（warning + exc_info） |
| P2 | preflight | 同年不同月假 overlap（年粒度过粗） | ✅ 修复（月精度值域比较） |
| P2 | PLAN | 承诺 vs 交付不符（见下「诚实披露」） | ✅ 文档修正（W6） |
| P3×6 | 各处 | 几何归类复制/排序确定性/死码/归一/缺口上限/日志级别 | ✅ 全部修复 |

明确无发现：身份/权限泄漏、并发 resume、确定性、向后兼容（2543 既有测试全绿）、影子注册表、性能（零扫描有 mock 计数钉扎）。

## 第二轮（@ 12cfa11b 修复后）

- 3 P1 全部确认正确修复（含破坏性验证：模拟 role_evidence 丢弃 → 新断言失败）；
- 6 P2 全部确认修复；P3-4 部分修复（残留 {"descriptor": None} 形状）；
- 新发现 3 P3：N1 倒挂区间不设防、N2 None-descriptor 归一漏、N3 可选角色进 expected_roles；
- 扩展回归 2549 passed / 3 skipped；ruff 通过。

**结论：不再存在阻塞合并的 P0/P1。**

## 第二轮 P3 残留处置（终轮）

- N1 倒挂区间 → ✅ `_interval` start>end 返回 None（不可判）+ 测试；
- N2 None-descriptor 形状 → ✅ 归一化跳过 + 测试；
- N3 可选角色 → ✅ semantic_tools expected_roles 只取 required。

最终验证：dataset_semantics 80 + qualification/workflow/project_runtime 回归 136 → 全绿；ruff 通过。

## 诚实披露（复审指出，PR 中明示）

- planner finalize / chat plan_orchestrator / workflow_runtime 注册链的 descriptor 注入**未做**（planner 无 session 锚，见 DECISIONS D-补充；chat 主链接线为后续工作）；
- role-level 资格证据目前经 semantic_tools（tier-2 advisory 编译工具）生产可达；expected 指纹生产供值闭环 = durable 记录面（ingest/mapspec 写入）→ 绑定对账（本 PR）；
- preflight 为 advisory 审计面（data_preflight），不阻断编译主链。
