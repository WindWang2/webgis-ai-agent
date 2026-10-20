# H01 PLAN — 单一目标与实施波次

## 单一目标

让既有 GIS Harness 在真实生产调用路径上把「GIS 任务语义 → 数据/能力资格 →
工具计划 → 执行结果 → 完成判据」的约束闭环补严：资格/回退/语义裁决到达完成
判据与用户可见面，拒绝与降级带稳定机器码与有界证据，且全部改动 additive、
ToolRegistry/Pi/MapSpec 主权不变。

## 非目标（防漂移）

- 不重造 PlanGraph/ToolSurface/WorkflowCompiler/Skill/StateStore。
- 不修 #1705/#1707/#1708/#1712/#1713/#1718/#1719/#1720/#1735/#1753（他人线）。
- 不改 pytest.ini、不改 dispatch 对 EXTERNAL_UNAVAILABLE 的语义（登记 concern）。
- 不碰 H02 独占文件（见 CONTRACT_MATRIX）。
- 不跑全量测试（#1753 存量挂起），一律 targeted lane + timeout。

## 工作包（对应任务 §3.A–§3.E，映射 RECON 缺口 K1–K8）

### WP1 执行结果契约修复（K1/K7）——任务 §3.B「结果后置条件/错误对齐」

1. `pi_result_abi.py`：evidence 投影键对齐真实生产者键（capability_bind →
   `{action, capability, status, code, reason_codes[:4], alternatives[:2]}`；
   action_ir → `{plan_id, kind, status, finding_codes[:4]}`），全部有界、无
   payload 泄漏；预算收缩序不变。
2. `_error_class` 增加 `provider_unavailable`（PROVIDER_UNAVAILABLE code）；
   桥内 not-found / tier-3 拒绝带稳定机器码（`UNKNOWN_TOOL` /
   `TIER3_CONFIRMATION_REQUIRED`，与 registry 现有码同源对齐）→ envelope
   error.class 不再依赖文本匹配兜底。
3. TDD：先写失败测试（evidence 键契约、provider_unavailable 分类、桥码）。

### WP2 资格码单源对照（K8）——任务 §3.B「一致性验证」

4. 新增共享常量模块（H01 包内）：turn drop codes ↔ dispatch canonical reason
   codes 的单一对照表 + 一致性守卫测试（封闭词表快照 golden）。
5. 不改既有码值（向后兼容），只提供同源常量与对照；新码（如
   `permission_not_granted`）进对照表。

### WP3 权限先验披露（K5）——任务 §3.B additive

6. `PiToolSpecV1` additive 字段 `required_permission`（None = 无要求）；
   `compile_tool_spec` 从 descriptor 投影。
7. `qualify_surface` 消费 situation 的 `perm:<p>` 事实：阳性缺席
   （`perm:<p> == False`）→ drop code `permission_not_granted`（阳性缺席才
   逐出，unknown 不排除——与 ADR-0224 裁决 3 同纪律）。
8. schema golden 更新 + fingerprint 一致性测试（dump 侧与 receipt 侧同源）。

### WP4 turn 面降拒披露（K6）——任务 §3.B/D additive

9. `compute_turn_active_tools` 的 disclosure（qualification_dropped 等）有界
   投影进 turn 上下文（复用既有 marker/turn context 预算机制），模型可自纠；
   字符上界 + 杀开关（缺省 ON，`PI_SURFACE_DISCLOSURE=0` 关）。

### WP5 资格↔完成判据收敛（K2/K3/K4）——任务 §3.A/D 核心

10. finalize 热路径（`planner.finalize_with_profile` 的
    `_evaluate_workflow_contract`）additive 写入
    `workflow_contract["data_qualifications"]`（五态 + reason_codes 有界摘要）
    与 `workflow_contract["fallback_tier"]`；语义与编译器同源（复用
    `qualify_workflow_data_roles` / `resolve_fallback_tier`，不复制口头 if）。
11. `evaluate_completion_contract` additive 消费：data 维见到 blocked 资格 →
    BLOCKED 覆盖；degraded/minimal tier → verdict 至多 READY_WITH_WARNINGS
    且 required disclosure；完成契约评估读 fallback_tier（K3 补消费方）。
12. 任务语义存续校验（K4）：chapter intent.task ∈ 受保护分析集
    （analytical_density 等 hint 保护区）而实际主表达仅视觉 → 新封闭 finding
    code `F_TASK_SEMANTIC_DOWNGRADED` → verdict 压档 + 披露义务。
13. TDD：每条先失败测试（含金样例：成都每平方公里密度缺分母 → 不能 READY）。

### WP6 场景工厂（任务 §3.E）

14. `tests/unit/gis_harness/test_h01_capability_scenarios.py`：16+ 场景
    （成都小学展示/各区数量/定量密度/密度缺分母/热点/距离服务区/POI 丢失/
    字段缺失/空数据/少点降级/CRS 缺席/CRS 不同/单位误配/无凭据/离线网络工具/
    ref 失效/权限拒绝/schema 改版 stale/旧会话恢复/重复回执幂等/Pi-legacy
    parity），每场景固定 request → intent family → 资格/角色 → 选中或拒绝的
    capability → 计划边 → status/reason → 有界证据。
15. metamorphic：顺序扰动/重试/重复调用/大小写/中文同义词（至少 5 组）。

### WP7 金样例与文档

16. schema golden：PiToolSpecV1（含新 additive 字段）、WorkflowCompilation
    关键字段、CompletionContract 关键字段一致性测试。
17. docs：ADR-0224 增补 note（resultAbi evidence 修正 + permission 投影）、
    docs/gis-harness.md 增补「资格→完成判据收敛」段、CHANGELOG、质量清单
    按真实变化更新。

## 波次（对齐任务 §4）

- Wave 0 ✅：RECON/BASELINE/PLAN/CONTRACT_MATRIX/LOOP_STATE/WORKLOG（本批文件）。
- Wave 1：逐调用点实图已在 RECON §1–§2；先写 WP1 失败测试（红）。
- Wave 2：WP1 实现 + WP5 的 10/11（资格收敛主体）。
- Wave 3：WP2 + WP3 + WP4（Pi 面 additive）。
- Wave 4：WP5 的 12（语义存续）+ 恢复/重复回执场景验证。
- Wave 5：WP6 全量场景 + golden。
- Wave 6：受影响路径前后对照测量（envelope 微基准/marker 字符上界/lane 耗时）。
- Wave 7：文档/CHANGELOG/ADR + `git diff --check` + ruff 全清。
- Wave 8：独立 reviewer（子代理）对抗审查 → P0/P1/P2 修复 → 复审 → 本地复测
  两遍 → fetch 复核新 PR/issue → `gh pr create`。

## 停止条件

Oracle（任务 §5 12 条）全满足 → PR；真实阻塞（凭据/权限/资源）→ 如实汇报
保存进度。绝不为凑量迭代。
