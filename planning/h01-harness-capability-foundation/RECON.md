# H01 RECON — Pi × GIS Harness 语义能力与执行可靠性基础层 VNext

- 基线：origin/master `0415ad8d3a03096ab405ece643753bc93974a760`（2026-10-20 fetch 复核）
- worktree：`../webgis-ai-agent-h01-harness`，分支 `feat/h01-gis-harness-capability-foundation-vnext`
- 调查方式：3 个并行只读 subagent（Pi 工具面链 / Harness 领域链 / 测试基础设施）+ orchestrator 对承重事实的逐条亲自复核

## 1. 已实现（不需要重造）

| 能力 | 位置 | 状态 |
|---|---|---|
| Turn-scoped qualified tool surface（7 个封闭 drop codes） | `app/services/chat/pi_surface_receipt.py:46-53`、`pi_native_surface.py:552-828` | 已实现，生产接线 |
| PiToolSpecV1 + fail-honest schema 投影 + canonical-JSON 指纹 | `app/services/chat/pi_tool_spec.py` | 已实现 |
| RuntimeSituation（credentials/offline/durable_worker/perm 投影） | `app/services/gis_harness/hotpath_convergence/runtime_situation.py:174-214` | 已实现 |
| 15 阶段 WorkflowCompiler（COMPILER_STAGES） | `app/services/gis_harness/workflow_compiler.py:35-51,133` | 已实现（证据面） |
| 五态数据资格（eligible/transform_required/degraded/blocked/unknown）+ 8 remediation ops | `app/services/gis_harness/data_qualification.py:30-78` | 已实现（编译链） |
| V8 资格（QualificationContext 数据面 20+ checks） | `app/services/gis_harness/qualification_v8.py:81-129`、`capability_reasons.py` | 已实现（dispatch 期） |
| 四层 fallback tier（preferred/degraded/minimal/blocked） | `app/services/gis_harness/fallback_v3.py:28` | 已实现（仅编译+评测消费） |
| 七维完成契约 + F_* 封闭码 + verdict 5 档 | `app/services/gis_harness/completion/contracts.py:43-99,131-135` | 已实现（settle 热路径） |
| 11 类失败分类 + 8 remediation 动作（dispatch seam 单点） | `app/services/gis_harness/failure_taxonomy.py:32-73`、`tool_dispatch_service.py:990-994` | 已实现 |
| 断点恢复凭证（RecoveryLedger/ResumeAnchor/ref 快照/旧代拒收） | `recovery_ledger.py`、`resume_anchor.py:46-52,296-314,415-450` | 已实现 |
| 分布展示 vs 定量密度隔离（hint 护栏 + recipe 五路分立 + 义务） | `intent.py:44-46,322-324,445-456`、`recipe_packs/density.py` | 已实现（到 verdict 降档） |
| resultAbi envelope（pi_result.v1，≤4KB） | `app/services/chat/pi_result_abi.py` | 已实现（有缺陷，见下） |

## 2. 部分实现 / 有生产缺陷（H01 目标缺口）

| # | 缺口 | 位置 | 影响 | 亲自复核 |
|---|---|---|---|---|
| K1 | **resultAbi.evidence 抽取键与生产者键错配**：capability_bind 实际键 `{tool,action,capability,status,code,reason_codes,alternatives}`，envelope 只抽 `decision/code/capability_id` → capability id 永远丢失；action_ir 实际键 `{policy_version,plan_id,compile_id,kind,status,finding_codes}`，envelope 只抽 `decision/code` → **恒空 → 整键省略**，plan_id/compile 状态永不可见 | `pi_result_abi.py:116-131` vs `capability_bind.py:427-458`、`gis_action/service.py:162-170` | 执行结果契约的证据面失效；无法从结果回溯 capability/plan | ✅ 读双方源码 |
| K2 | **完成判据与资格裁决脱钩**：五态 data_qualification + fallback_tier 只在编译器证据侧；finalize 热路径（`planner.finalize_with_profile → _evaluate_workflow_contract`）不跑 `qualify_workflow_data_roles`；`evaluate_completion_contract` 只读 `workflow_contract` 的 obligations/method_blockers/data_blockers，读不到 tier/五态 | `contracts.py:174-210`、`planner.py:1336,1863-1935` vs `workflow_compiler.py:277,528-538` | 「拒绝/降级」裁决不影响实际完成判据；degraded/minimal 产品可能判 READY | ✅ grep+读码 |
| K3 | **fallback_resolution 无生产消费方**（全仓唯一消费 = `app/evaluation/runner.py:508`） | `fallback_v3.py`、`workflow_compiler.py:528-538` | 四层回退裁决在用户可见完成面缺席 | ✅ grep |
| K4 | **完成判据不校验任务语义存续**：intent.task=analytical_density 被回退成视觉热力时，completion 无 intent/measure 维校验，只靠披露链 | `contracts.py:174-310`、`goal_satisfaction/requirements.py:9-25` | 「工具真能完成用户任务」语义层断点 | subagent 复核 |
| K5 | **required_permission 不进 PiToolSpecV1**，turn 资格层不查权限；situation 的 `perm:<p>` 事实 turn 面从不消费 → 模型可见「可调」但 dispatch 恒 PERMISSION_DENIED | `pi_tool_spec.py:270-296`、`pi_surface_receipt.py:123-176` vs `registry.py:1359-1388` | 权限维约束传递断链（先验披露缺失） | subagent 复核 |
| K6 | **turn 面 drop 原因不披露**：`disclosure`（含 qualification_dropped）在 `_active_tools_block_for` 只消费 abstained 即丢弃 | `pi_native_surface.py:683-684`、`pi_turn_context.py:424-446` | 模型只看到工具变少，无法自纠 | subagent 复核 |
| K7 | **桥内 typed 拒绝缺机器码**：tool-not-found（594-607）与 tier-3（609-620）只有文本；`PROVIDER_UNAVAILABLE` 不在 `_error_class` 词表 → 归 tool_error | `agent_pi_bridge.py:594-620`、`pi_result_abi.py:152-169` | 错误分类靠文本匹配，typed 契约有洞 | subagent 复核 |
| K8 | **turn 侧 drop codes 与 dispatch 侧拒绝码两套词表、无共享常量**（credential_absent vs credentials_missing/CREDENTIALS_REQUIRED 等） | `pi_surface_receipt.py:46-53` vs `capability_bind.py:20-31`、`registry.py` | 同一事实两套码，遥测/完成判据无法对齐归因 | subagent 复核 |

## 3. 无生产消费方（登记，默认不在本 PR 抢修）

- `registry_validation.validate_gis_library`：启动校验已改走 runtime_manifest 双轨（`app/main.py:291-300`）——两套校验面漂移风险，登记 concern。
- `PI_SURFACE_SCOPE_ENFORCE` 默认 OFF（设计内 pilot 开关，不动）。
- `GIS_SITUATION_PROBE_WORKERS` 默认 OFF → durable_worker 资格事实默认 unknown（ADR 已知边界，不动默认值）。
- `EXTERNAL_UNAVAILABLE` 工具 turn 面剔除但 registry dispatch 不拒（proxy 可达）——行为变更风险中等，登记 INTERFACE/concern 不在本 PR 改 dispatch 语义。

## 4. 测试缺失

- 16+ 场景任务矩阵（成都小学展示/各区数量/定量密度/热点/服务区/POI 丢失/字段缺失/空数据/少点降级/CRS 不明/CRS 不同/单位误配/无凭据/无网络/ref 失效/权限拒绝/schema 改版/旧会话恢复/Pi-legacy parity）无统一可重放工厂——现有 golden/scenario/corpus 分散在 tests/cartography、tests/fixtures、app/evaluation。
- resultAbi evidence 键与生产者的契约一致性无测试（所以 K1 存活至今）。
- turn 面 drop 码 ↔ dispatch 拒绝码一致性无对照测试。

## 5. 已在其他 PR/Issue 内（不抢修）

| Issue | 内容 | 与 H01 关系 |
|---|---|---|
| #1708 | modelops_evaluate_model 越权栅格 / execute_execution_plan 写他人会话 | 安全门禁，其他工作线；H01 的 K5 是权限「先验披露」不是门禁本体 |
| #1712 | evidence_claim 验证器 fail-open 两处 | evidence_claim 域，其他线 |
| #1713 | 视觉观测非默认模式跳过 C13 | visual_observation 域 |
| #1718 | geocompute durable 丢弃 caller 身份 | durable 域 |
| #1735 | spawn_subagent parallel_tasks 丢参 / distance_band 绕过 apply_contract | 工具入参域 |
| #1702 | test_contracts 同名收集中断 | **HEAD 已修复**（亲测 26652 collected / 129s / exit=0） |
| #1707 | 39 个 p4_test_*.py 不被收集 | 仍存在；H01 不改 pytest.ini（属 #1707 修复线），新测试一律 `test_*.py` 命名 |
| #1753 | 全量串行 ~80% 永久挂起 | H01 一律 targeted lane + 外套 timeout，不跑全量 |
| #1705/#1719/#1720 | GIS 科学前提/算法缺陷 | 其他线；H01 只加跨层契约断言，不重复修 |

## 6. PR 冲突表

- 开放 PR #1695–#1701 全为 dependabot 依赖升级（pandas/pypdf/fakeredis/sqlalchemy/pydantic/docker/node/npm）——零文件交叠。
- H02（并发）独占：`template_selector.py`、`template_catalog.py`、`product_completeness.py`、`product_shapes.py`、`app/lib/cartography/**`、frontend 制图域——H01 零触碰（本计划不涉及上述文件）。
- H01 触碰面：`pi_result_abi.py`、`pi_tool_spec.py`、`pi_native_surface.py`、`pi_turn_context.py`、`pi_surface_receipt.py`、`agent_pi_bridge.py`（拒绝码 additive）、`completion/contracts.py`（additive 读取）、`planner.py`（additive 写入 workflow_contract 键）、`workflow_compiler.py`（additive 透传）、`data_qualification.py`（只读复用）+ 新测试文件 + 新 golden。
- 共享边界遵守：MapRequestIntent → WorkflowCompilation/MapProductPlan/CompletionContract 均为 additive 字段；MapSpec/RenderWork/ToolRegistry 事实源不动结构。

## 7. 测试环境事实（Agent C 实测）

- venv：`/home/kevin/project/webgis-ai-agent/.venv/bin/python`（pytest 9.1.1 + timeout 2.4.0 + xdist 3.8.0）；在 worktree 目录内跑（`pytest.ini pythonpath=.`）。
- 无外部服务：conftest 强制 sqlite 临时库 + memory celery + USE_REDIS=false。
- 安全 lane：`tests/unit/gis_harness/`（2502 collected / 65s 收集）、`tests/unit/test_pi_*.py`（451 项）、单文件实跑 56–90s（约 50s 固定启动开销，`--cov=app` 插桩）。
- 全量收集 129s 成功（#1702 已修复）；全量执行受 #1753 影响禁止；大 lane 外套 `timeout`。
- ruff 0.16.8：`app/services/gis_harness/` 当前 0 违规。
