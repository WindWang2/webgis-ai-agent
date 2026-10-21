# G05 TASKS

状态图例：☐ todo / ◐ in progress / ☑ done / ✗ existing（已存在于 master，复用）/ ⏸ deferred(reason)

## W0 侦察与地基
- [x] ✗ 侦察：RECON.md（subagent A，全部带 file:line 证据）
- [x] ☑ worktree feat/g05-harness-task-acceptance-vnext @ ee8c14f8
- [x] ☑ PLAN.md / TASKS.md / DECISIONS.md / TEST_MATRIX.md / PR_OVERLAP_MATRIX.md
- [ ] ☑ .goal-loop-ledger.md 记账启动

## W1 契约 + 最小消费（ScenarioContract）
- [ ] contracts.py：TaskScenario DSL v1（turns/agent_script/expectations/not_evaluated 声明/expect_fail）
- [ ] test_contracts.py：schema 契约 + 序列化 roundtrip + 负例校验红
- [ ] runner.py 最小闭环：单场景真实 dispatch_tool→MapSpec 产出
- [ ] fixtures/task_scenarios/ pin + pin 一致性测试

## W2 生产入口 + 20+ 场景
- [ ] corpus.py：12+ case family（点图/热力、行政区统计、密度、时间、栅格指数、空间连接、无坐标、样本不足、nodata/单位、错误 CRS、用户改图保护、导出一致性、取消/恢复、权限拒绝…）
- [ ] projections.py：requirement/工具 receipt/MapSpec/RenderWork/导出 manifest/最终答复 全链投影
- [ ] test_task_e2e.py：正例绿（真实链路证据断言）
- [ ] test_corpus.py：完整性/家族覆盖/去重

## W3 失败/恢复/并发
- [ ] attribution.py：task 级 taxonomy（intent_wrong/wrong_dataset/invalid_method/unmet_product/stale_render/lost_interaction/export_parity/unsupported/governance_violation）→ 归因到模块/阶段
- [ ] test_task_failures.py：负例红 + 行为反转翻红（expect-fail 真实性）+ 科学错误/地图错误两类注入
- [ ] test_multiturn.py：成都小学→区统计→隐藏锁定→换色带→导出 多轮链 + user-wins + revision 单调
- [ ] test_recovery.py：跨会话恢复续绘 + 长任务取消

## W4 判错 CLI 与质量门
- [ ] reporting.py：结果模型 + first_failing_stage + 覆盖矩阵 + not_evaluated 统计
- [ ] scripts/g05_task_acceptance.py：run/compare/report × smoke/focused/heavy
- [ ] 覆盖热区图（docs/quality/g05-task-acceptance.md）
- [ ] ruff/lint 过 + 本地命令表记录

## W5 独立 review（subagent C × 2 轮）
- [ ] Round 1 逐文件 diff review → 修复
- [ ] Round 2 复审变更与关键调用链 → 无 P0/P1

## W6 交付
- [ ] docs/quality + planning/g05 完整化（REVIEW.md / PR.md）
- [ ] commit 序列整理 + gh pr create（不合并）
- [ ] 终报（PR URL + 证据）

## 复用清单（existing，不重造）
- ✗ replay oracle T1–T4 / OfflineReplayer（app/lib/harness/replay/）——只读引用
- ✗ h02 33 导出 conformance 场景——导出环节期望源
- ✗ kernel e2e 7 场景模式（tests/test_harness_kernel_e2e_scenarios.py）——runner 蓝本
- ✗ ScriptedToolProvider/FaultInjector/LabClock（app/lib/harness/lab/fakes.py）
- ✗ gis_samples fixtures、pi_mocks
- ✗ evaluate_goal_satisfaction / build_product_manifest 等权威纯函数
