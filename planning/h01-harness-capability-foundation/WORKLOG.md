# H01 WORKLOG — 每轮一行：假设→改动→测试→review→继续/重规划

| # | 假设 | 改动 | 测试 | 结果 | 下一步 |
|---|---|---|---|---|---|
| 1 | master 现场与任务书描述一致，相关 PR 已合并 | 无（只读侦察：3 subagent + 亲核 K1/K2/K3） | 基线 lane 3+3 项实跑 | 全 exit=0；K1–K8 缺口确认 | 冻结计划，进 Wave 1 |
| 2 | resultAbi 证据键错配可修（K1/K7） | `dispatch_reason_codes.py` 新模块 + `pi_result_abi.py` evidence 投影/错误码映射 + 桥 not-found/tier-3 机器码 + `pi_surface_receipt` DROP 集合 | test_pi_h01_result_contract / test_pi_h01_reason_code_parity（先红后绿） | 11 红 → 全绿；邻接 test_pi_result_abi/test_pi_surface_abi_route 无回归（35 passed, exit=0） | WP3 权限先验 |
| 3 | required_permission 可进 turn 面（K5） | `pi_tool_spec` 条件序列化字段 + `runtime_situation.to_qualification_dict` granted_permissions（纯派生键，digest 剔除保等价）+ `qualify_surface` DROP_PERMISSION_NOT_GRANTED | test_pi_h01_permission_qualification（先红后绿） | 6 红 → 全绿；test_pi_tool_spec 无回归 | WP4 披露 |
| 4 | 逐出原因可进 marker（K6） | `pi_turn_context._qualification_drop_disclosure`（有界 ≤6 条/900 字符 + PI_SURFACE_DISCLOSURE 杀开关） | test_pi_h01_surface_disclosure（先红后绿） | 3 红 → 全绿；test_pi_dynamic_surface 无回归（19 passed） | WP5 收敛 |
| 5 | 资格/回退档可进 finalize 契约并被完成判据消费（K2/K3/K4） | `planner._evaluate_workflow_contract` +data_qualifications/fallback_tier（同源 qualify_workflow_data_roles/resolve_fallback_tier）；`contracts.py` 消费（blocked→BLOCKED_BY_DATA、degraded/minimal→压档、K4 语义存续守卫 F_TASK_SEMANTIC_DOWNGRADED） | test_h01_qualification_completion_convergence（先红后绿） | 7 红 → 全绿；verdict/compiler/schema/completion/binding 回归分片 143+56 passed（修 digest 等价回归：派生键剔除） | WP6 场景工厂 |
| 6 | 21 场景 + metamorphic 全经生产函数可重放 | tests/unit/gis_harness/test_h01_capability_scenarios.py + 全 lane | 全 lane（6 新文件 + 11 邻接回归） | 295 passed / 1 skipped / exit=0；envelope 微基准 7.6µs/890B；ruff 全清；diff --check=0 | 独立 review |
| 7 | 独立对抗 review 能抓真实缺陷（reviewer 独立读 diff+代码+4 组运行时实验） | 修复：P1-1 alternatives 生产者形状 {kind,id}（死分支+测试伪造形状+文档失实）；P2-1 merge 只在补齐 creds 时刷新派生键（caller-wins）；P2-2 finalize tier 补 ontology_task_id 锚+输入面差异如实注释；P2-3 meta 测试改名收集+parity 守卫双向化；P3-1 删冗余析取支；P3 软断言改确定性、S17 断言真实 registry 闸 | 修复批（126 passed / exit=0）+ ruff 全清（app/ 全树） | review 结论 PASS-with-fixes → 修复全绿 | 复审 + 双跑 + PR |
