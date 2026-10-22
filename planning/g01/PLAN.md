# G01 PLAN — 数据集语义身份、可用性与恢复链路

基线：origin/master b1b2ec23 · 分支：feat/g01-dataset-semantic-readiness-vnext

## 波次计划

### W0（本文件 + RECON + TASKS + TEST_MATRIX）✅
- 基线测试 42 passed（11.64s），命令：
  `timeout 300 .venv主仓/bin/python -m pytest tests/unit/test_dataset_semantics_store_v1.py tests/unit/test_dataset_semantics_wiring_v1.py tests/unit/test_qualification_descriptor_wiring_v1.py -q --no-cov --disable-warnings -p no:cacheprovider`

### W1 — WP-A 角色绑定契约（纯增量，主责区）
新模块 `app/services/dataset_semantics/role_binding.py`：
1. `SessionSemanticEvidence`：session refs（≤16，按 ref_id 排序——确定性，不用 dict 迭代序）→ 每 ref 语义记录（descriptor、指纹、read_status、alias）。含 expected（记录面）指纹的 per-ref 对账。
2. `bind_role_datasets(role_reqs, evidence, ...)`：role → 绑定结果 `RoleBinding`，status ∈ bound/not_found/ambiguous/stale/partial（+ not_authorized 经 ref 解析失败区分），证据 = descriptor 事实（geometry_kinds/fields/unit/temporal）+ capability/alias 线索。**同名/同线索多候选 → ambiguous（带候选清单），绝不取首个。**
3. 词表复用 DATA_ROLES；reason codes 稳定机读（ROLE_BINDING_AMBIGUOUS 等）。
4. `qualification_bridge` 增 `session_role_evidence()`（async，纯读）；`primary_session_descriptor` 保持向后兼容（内部改走同一证据读取）。
测试：`tests/unit/dataset_semantics/test_role_binding_v1.py`。

### W2 — WP-B 生产接线（小缝，实证必要）
1. `qualify_workflow_data_roles` 增可选 `role_descriptors: Dict[role, descriptor]`（additive；每角色用自己的数据集事实）。
2. `compiler_v4` / `workflow_compiler` / `plan_candidates` 透传面接 `role_descriptors`。
3. `semantic_tools.compile_workflow_semantics`：primary_session_descriptor → session_role_evidence + role 绑定注入（同时喂 expected 指纹——MapSpec/durable 记录面在场时）。
4. `planner.py` finalize：bound_refs 已有 role→ref；补 per-role descriptor 解析（经 store，零扫描）。
5. `descriptor_evidence` 接线为 qualification_bridge 内部工具（供 expected 指纹形状）——消除零调用 API。
验收：chat 主链经 plan_orchestrator 的编译路径可获得角色级 descriptor 事实（集成测试穿透真实入口）。

### W3 — WP-B6/恢复判据（project_runtime 缝）
1. `project_runtime/rebind.py` 或 `resume.py`：重水合后对每个带 descriptor_fingerprint 的 source 走「零扫描补铸或 store 迁移 + evaluate_reuse 对账」，verdict 落 resume evidence（`descriptor_reuse` 字段）；不通过 → 诚实降级不静默续渲染。
2. staleness 来源归因：用 compare_descriptors 字段级 delta（复用既有），不新造。
3. 失败注入测试：损坏 descriptor、版本前进、ref 淘汰、跨项目引用拒绝、并发写入。
验收：恢复链 fingerprint 一致性可复现（旧版本漂移 → 旧计划失效判据可达）。

### W4 — WP-C preflight + 6 场景贯通
1. `app/services/dataset_semantics/preflight.py`：metadata-only 多数据兼容 preflight（CRS 可比性/单位冲突/时间覆盖重叠/空间 bbox 相交/密度vs数量字段语义），verdict + typed gaps（Agent 可消费的最小澄清：需要什么数据、typed alternatives）。
2. 六场景端到端：成都小学分布/各区数量/每平方公里密度（需分母）/2020vs2025 变化（需双期）/缺行政边界（typed gap）/两个同名数据集（ambiguous）。链路：role binding → preflight → 资格 → 计划证据 → lineage。
3. 性能：preflight 只读 descriptor（0 数据扫描），对 master 的 p50/p95 对比（pytest -m perf 或手动基准脚本，实测预算披露）。

### W5 — 独立 review（subagent C，未参与实现）
逐文件审 `git diff origin/master...HEAD`：fake green/错误吞噬/身份泄漏/版本并发/数据误判/影子注册表/性能退化/断言强度。修复 → 复审至无 P0/P1。

### W6 — 文档 + PR
- `docs/` 用户数据问题诊断矩阵一页 + ADR-0215 增补（G01 接线记录）+ planning/g01 全套。
- `gh pr create --base master --head feat/g01-dataset-semantic-readiness-vnext`，标题 `feat(dataset-readiness): ...`。不自动合并。

## 非目标
不改 data_fabric/contracts 冻结面；不统一旧六套指纹；不做导入 UI；不抢 #1758 issue；不跑全量套件（#1753 挂起）；不建第二数据目录/catalog。

## 停机条件（DoD 对应）
见任务提示 §5。每 wave 一个或多个自洽 commit；planning/g01/* 随进度更新。
