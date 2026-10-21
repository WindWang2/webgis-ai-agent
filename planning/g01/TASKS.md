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

## 终态盘点（W6 · done / existing / deferred）

### done（本 PR）
- WP-A #1 调用链：RECON.md 全量证据（文件:行）
- WP-A #2 六态角色绑定 role_binding.py（不新增第二数据目录/不复制数据）
- WP-A #3 字段事实来源：复用 descriptor 既有字段级 schema/unit/时间/CRS 证据（零新推导）
- WP-B #4 role_evidence 经真实编译链（compile_workflow/v4 → qualify）+ semantic_tools 生产入口；stale 精确归因（复用 compare_descriptors/evaluate_reuse）；未知指纹不伪造新鲜
- WP-B #5 typed gaps（preflight 6 类判据 + 角色所需数据速查）
- WP-B #6 恢复链：迁移 + 记录面对账（ResumeReceipt.descriptor_reconciliation）+ MapSpec 冻结指纹回喂（stale 绑定 → 资格 DESCRIPTOR_STALE_*）；严禁仅局部 hash 判全局复用（verdict 单一来源 evaluate_reuse）
- WP-C #7 metadata-only preflight（零扫描，mock 计数钉扎）
- WP-C #8 同一语义证据读法（collect_session_evidence 单一入口；向后兼容：primary shim 行为不变）
- WP-C #9 ref 淘汰/跨会话（not_authorized fail-closed + 测试）、损坏 descriptor（fail-closed 可见 + 测试）、数据 revision 前进（恢复对账 recompute + 测试）
- WP-C #10 六场景贯通测试
- WP-D #11 确定性（乱序 ref 稳定）、负例拒绝假通过（ambiguous 不供事实、stale 降级、blocked）
- WP-D #12 性能：零数据扫描断言 + O(refs) 语义读 + p95<50ms 宽松退化上界（实测披露，非拍脑袋绝对目标）
- WP-D #13 诊断矩阵一页 + ADR-0215 增补 + 本目录全套

### deferred（原因）
- chat 主链 plan_orchestrator / workflow_runtime 注册 / runtime_bridge 复编译注入 descriptor：planner 无 session 锚（DECISIONS D11）；tier-2 advisory 工具已闭环生产可达，主链注入为后续小缝（不阻塞本 PR 价值）
- 并发写入失败注入测试：resume lease 串行化 + store 内容寻址幂等已覆盖（复审确认无发现），专项测试留后续
- preflight 强阻断模式（当前 advisory）：等消费面反馈再收紧
