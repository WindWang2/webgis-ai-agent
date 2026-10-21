# G01 TASKS

状态图例：☐ 待办 / ◐ 进行中 / ☑ 完成 / ✗ 放弃(原因见 DECISIONS)

## W0
- [x] 基线 SHA 确认（b1b2ec23，gh API 双确认）
- [x] worktree/分支创建（零占用校验通过）
- [x] RECON.md（调用链+缺口+交叠）
- [x] 基线测试：dataset_semantics 3 文件 42 passed
- [x] PLAN/TEST_MATRIX/DECISIONS/PR_OVERLAP_MATRIX 建立

## W1 — WP-A 角色绑定
- [ ] role_binding.py：SessionSemanticEvidence（ref 排序、有界、fail-closed 读）
- [ ] role_binding.py：bind_role_datasets（bound/not_found/ambiguous/stale/partial + reason codes）
- [ ] qualification_bridge：session_role_evidence()；primary_session_descriptor 兼容改写
- [ ] 单测 test_role_binding_v1.py（含：同名 ambiguous、乱序确定性、损坏 descriptor、expected 指纹 stale）

## W2 — WP-B 生产接线
- [ ] qualify_workflow_data_roles 增 role_descriptors（additive）
- [ ] compiler_v4/workflow_compiler/plan_candidates 透传
- [ ] semantic_tools 注入改角色级 + expected 指纹
- [ ] planner finalize per-role descriptor
- [ ] descriptor_evidence 接线（消零调用）
- [ ] 集成测试：真实编译入口拿到角色级事实

## W3 — 恢复判据
- [ ] project_runtime resume/rebind descriptor 对账（evaluate_reuse verdict 落 evidence）
- [ ] 失败注入：损坏 descriptor/版本前进/ref 淘汰/跨项目拒绝/并发
- [ ] MapSpec expected 指纹回喂资格（重编译路径）

## W4 — preflight + 场景
- [ ] preflight.py metadata-only 兼容判据 + typed gaps
- [ ] 六场景端到端（成都小学 ×6）
- [ ] 性能对比 vs master（p50/p95/内存/查询数）

## W5 — review
- [ ] 独立 reviewer 逐文件（未参与实现者）
- [ ] 修复 → 复审无 P0/P1

## W6 — 文档+PR
- [ ] 用户数据问题诊断矩阵一页
- [ ] ADR-0215 增补
- [ ] gh pr create（不合并）

## 检查点
- W0 @ b1b2ec23：42 基线测试绿，无未提交代码变更
