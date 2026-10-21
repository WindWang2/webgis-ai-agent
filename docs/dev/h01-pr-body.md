# PR: feat(h01): GIS Harness 资格→完成判据收敛 + Pi 结果契约修复（ADR-0224 增补）

## 来源（issue / 文档债 / 任务书）

- 目标来源：H01 任务书「Pi × GIS Harness 语义能力与执行可靠性基础层 VNext」（planning/h01-harness-capability-foundation/ 全套规划文档随 PR 附带）。
- Baseline：`origin/master @ 0415ad8d3a03096ab405ece643753bc93974a760`（2026-10-20 执行时实测 fetch；与任务书快照一致，无漂移）。
- Dedup：与开放 PR（#1695–#1701，全为 dependabot 依赖升级）零文件交叠；与 #1708/#1712/#1713/#1718/#1735（他人认领线）零重叠——本 PR 的权限面是「turn 侧先验披露」，不动 #1708 的 dispatch 门禁本体；#1712 evidence_claim / #1713 visual_observation 域未触碰。H02 独占文件（template_selector / template_catalog / product_completeness / product_shapes / lib/cartography / frontend 制图域）零触碰。

## 根因与证据（Problem Evidence）

1. **resultAbi 证据键错配（K1）**：`pi_result_abi.py:119-128` 抽取键 `decision/code/capability_id`，而生产者 `capability_bind.py:427-458` 实际键为 `{tool,action,capability,status,code,reason_codes,alternatives}`、`gis_action/service.py:162-170` 为 `{policy_version,plan_id,kind,status,finding_codes}` → capability id 永远丢失、`evidence.action_ir` 恒空整键省略（plan_id 不可见）。
2. **两套资格词表无对照（K8）**：turn drop codes（`pi_surface_receipt.py:46-53`）与 dispatch 拒绝码（`capability_bind.py` / `registry.py` / `pi_input_gate.py`）同一事实不同名（credential_absent vs credentials_missing/CREDENTIALS_REQUIRED），无共享常量。
3. **权限先验断链（K5）**：`PiToolSpecV1` 不投影 `required_permission`、turn 资格层不查权限 → 模型可见「可调」但 dispatch 恒 `PERMISSION_DENIED`（`registry.py:1359-1388`）。
4. **逐出原因不披露（K6）**：`pi_native_surface.py:683-684` 的 `qualification_dropped` 在 `pi_turn_context.py:424-446` 只消费 abstained 即丢弃。
5. **资格裁决与完成判据脱钩（K2/K3）**：五态 data_qualification + fallback_tier 只在编译器证据侧（`workflow_compiler.py:277,528-538`）；finalize 热路径（`planner.py:1863-1935`）不跑 `qualify_workflow_data_roles`；`evaluate_completion_contract` 只读 obligations/method_blockers/data_blockers（`contracts.py:187-210`）；fallback_resolution 全仓唯一生产消费 = `evaluation/runner.py:508`（评测，非完成面）。
6. **任务语义存续无校验（K4）**：intent.task=analytical_density 被降级为视觉表达时完成判据不报（`contracts.py:174-310` 无 intent 维）。
7. **typed 错误分类有洞（K7）**：`PROVIDER_UNAVAILABLE` 不在 `_error_class` 词表 → 归 tool_error；桥内 not-found（`agent_pi_bridge.py:594-607`）/tier-3（609-620）拒绝纯文本无机器码。

## 改动清单（Architecture & Files）

- **修复（K1/K7）**：`pi_result_abi.py` evidence 投影生产者真实键（capability id / plan_id / alternatives[{kind,id}→id] 有界可见）+ 权威错误码确定性映射（新增 `provider_unavailable` class）+ 桥内拒绝机器码（`UNKNOWN_TOOL` / `TIER3_CONFIRMATION_REQUIRED`，与 registry 同码）。
- **新增（K8）**：`app/services/gis_harness/hotpath_convergence/dispatch_reason_codes.py` —— 跨层机器码单一事实源（常量与生产者字面量逐位一致由测试钉住）+ `ERROR_CLASS_BY_CODE` + `TURN_DROP_PARITY` 对照。
- **新增（K5）**：`PiToolSpecV1.required_permission`（条件序列化：未声明不出键，存量 spec 指纹零漂移）；`qualify_surface` 阳性缺席逐出（`permission_not_granted`；截断/未供给 = unknown 不排除）；`to_qualification_dict` 加性 `granted_permissions`（`perm:<p>` 纯派生键，等价 digest 剔除以保 planner/dispatch 四面等价；merge 仅在运行时补齐 creds 时刷新派生键）。
- **新增（K6）**：turn marker `[工具面资格]` 有界披露段（≤6 条/≤900 字符/`PI_SURFACE_DISCLOSURE=0` 杀开关）。
- **收敛（K2/K3）**：`planner._evaluate_workflow_contract` 以编译器同源函数（`qualify_workflow_data_roles` / `resolve_fallback_tier`，含 ontology 任务锚）写 `workflow_contract["data_qualifications"]`+`["fallback_tier"]`；`contracts.py` 消费：blocked 资格（未被声明允许降级覆盖）→ `BLOCKED_BY_DATA`；degraded/minimal 档 → READY 封顶 `READY_WITH_WARNINGS`。
- **新增（K4）**：`F_TASK_SEMANTIC_DOWNGRADED` + `TASK_SEMANTIC_FAMILY_GUARDS`（analytical_density ✕ visual_density）→ `BLOCKED_BY_METHOD`。
- **测试**：6 个新文件 —— 场景工厂（21 场景 × 展示/统计/密度/分母/热点/服务区/POI 丢失/字段缺失/空数据/少点/CRS×2/单位/凭据/网络/ref 失效/权限/schema 改版/恢复/重复/parity + 5 metamorphic，全经生产函数）、收敛测试、词表双向一致性守卫、result contract、permission qualification、surface disclosure。
- **文档**：ADR-0224 增补「后续修订（H01）」节、docs/gis-harness.md「资格→完成判据收敛」节、CHANGELOG。

## 验证命令与输出摘要（Test Evidence）

- 命令：`python -m pytest tests/unit/test_pi_h01_*.py tests/unit/gis_harness/test_h01_*.py tests/unit/test_pi_result_abi.py tests/unit/test_pi_surface_abi_route.py tests/unit/test_pi_tool_spec.py tests/unit/test_pi_dynamic_surface.py tests/unit/gis_harness/{test_runtime_situation,test_product_verdict,test_workflow_compiler,test_workflow_schema,test_map_completion,test_golden_cases_orchestration}.py tests/unit/gis/test_capability_binding_governance.py -q -p no:cacheprovider`（venv `/home/kevin/project/webgis-ai-agent/.venv`）
- 结果：**297 passed / 0 failed，退出码 0；连续两遍一致（RUN1 297 passed@121s / RUN2 297 passed@118s）**。
- TDD：全部工作包先红后绿（WP1 11 红 / WP3 6 红 / WP4 3 红 / WP5 7 红 → 修复转绿）。
- ruff：`ruff check app/ <H01 测试文件>` → All checks passed（退出码 0）。
- `git diff --check origin/master..HEAD` → 退出码 0。
- 性能：envelope 构建微基准 7.6 µs/次（10k 次实测），evidence 890B ≪ 4KB 预算；`qualify_surface` 权限检查 O(n·8)；marker 披露段 ≤900 字符硬截断。
- 基线既有失败（透明记录，非本 PR 引入）：全量套件受 #1753（串行 ~80% 永久挂起）与 #1707（p4_test_* 不被收集）影响 —— 本 PR 一律 targeted lane + timeout，未跑全量；#1702（同名 test_contracts 收集中断）在 HEAD 已修复（收集 26652 项 exit=0）。

## Review 意见与清偿（Independent Review）

- Reviewer：独立对抗 subagent（与实现者不同 agent；独立读全量 diff + 逐文件上下文 + 4 组运行时实验 + 独立跑 180 项 targeted 测试）；初审结论 **PASS-with-fixes**，复审结论 **PASS**。
- 计数：P0 ×0 / P1 ×1 / P2 ×3 / P3 ×3。
- 逐条清偿：
  - P1-1 alternatives 抽取对生产者形状是死分支 + 测试伪造形状锁死 + 文档失实 → 抽取键改 `id` 优先（{kind,id} 形状）+ 测试改真实形状正向断言 alternatives 在场（复审 FIXED，运行时实证）。
  - P2-1 merge 静默丢弃 caller 侧 granted_permissions → 仅在运行时补齐 credentials_present 时刷新派生键（caller-wins；kill-switch 路径保留；复审双实验复验 FIXED）。
  - P2-2 finalize tier 输入面窄于编译器 → 补 ontology_task_id 锚（只降不升方向核实）+ 输入面差异如实注释（uses_planned_capability/scenario minimal 缺席，finalize tier 是上界）。
  - P2-3 meta 场景测试 `_test_` 前缀不被收集 + parity 守卫方向反 → 改名收集（--collect-only 实证 29 项）+ 守卫双向化（事实类 drop code 未登记即红，预算类豁免）。
  - P3-1 冗余析取支 → 删除（复审语义等价证明成立）；P3-2 digest QualificationContext 支 offline 不对称 → 核实为 master 既有行为，备忘不修（登记 LOOP_STATE concerns）；P3-3 手工构造 >8 perm 键截断 → 真实构造器自洽，备忘。
  - P3 软断言/S17 弱断言 → 改确定性断言 + 真实 registry #1402 闸验证（asyncio.run dispatch → PERMISSION_DENIED）。
- 未清偿项：无（P3-2/P3-3 为 master 既有/理论路径，已登记不属本 PR）。

## 风险与回滚（Compatibility & Rollback）

- 兼容性：全部 additive——旧章节（无新键）逐位 master 行为（test_absent_keys_master_behavior 钉住）；`data_qualifications`/`fallback_tier` 键缺席走缺席分支；spec 条件序列化使存量 300+ 工具指纹零漂移（当前 registry 无生产工具声明 required_permission）；`granted_permissions` 为纯派生键且 digest 剔除，planner/dispatch 等价契约由既有 digest 测试钉住；`error.class` 新成员 `provider_unavailable` 加性（无封闭集校验消费方）。
- 回滚开关：`PI_SURFACE_DISCLOSURE=0`（披露段）；既有 `PI_RESULT_ABI=0` / `PI_TOOL_ABI=0` 家族不变；finalize 契约新键无开关但为纯附加证据（消费方缺键零漂移）——整 PR 可 revert 单 commit 回滚。
- 残余风险：finalize tier 相对编译器缺 uses_planned_capability 输入（方向只影响 planned 子场景的乐观上界，注释披露）；turn 资格快照与 dispatch 合并的时刻差（ADR-0224 既有边界，未改变）。

## Out of Scope

- #1705/#1719/#1720 GIS 科学前提与算法缺陷（他人线）；#1708 dispatch 安全门禁本体（本 PR 仅 turn 侧先验披露）；#1712 evidence_claim fail-open；#1713 visual_observation；#1718 durable caller；#1735 工具入参；#1753/#1707 测试基建修复。
- EXTERNAL_UNAVAILABLE 工具 registry dispatch 不拒（turn 面剔除 vs proxy 可达的面-执行真相差）——行为变更风险中等，登记 planning/…/RECON.md §3 不在本 PR 改 dispatch 语义。
- H02 制图产品组合域（template_selector 等）；recipe 路由选择序调整（analytical_density 当前路由到 administrative_choropleth 是既有选择层语义）。
