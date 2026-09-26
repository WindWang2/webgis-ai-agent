# F12 Map Plan Compiler — 独立 Review 报告（Subagent C）

- Reviewer：独立深审 agent（与实现者分离，只读）
- 范围：`origin/master...HEAD`（基线 9e1ad229），全量 diff + 实证复现
- 结论：**READY-AFTER-FIXES → 已全部清偿（2×P1 + 7×P2 修复，见文末）**

## 逐项审查结论（A–I）

| 项 | 结论 | 关键证据 |
|---|---|---|
| A 单一真相/映射一致性 | PASS | `apply.py::mutation_to_engine_intent` 与 `intent_codec.body_to_intent` + `mutate_component` 语义一致；全链唯一提交经 `apply_mutation`，无第二写路径 |
| B 正确性 | PASS（修复后） | paint delta 只带变化键；visibility 双形态与引擎契约一致；`_merged_layer_dict` 不丢 current 独有键；CAS 链在成功/duplicate/error 分支均正确 |
| C 并发/幂等/迟到 | PASS | session 锁 + 锁内 CAS + dedup 先于 CAS（响应丢失重试安全）；receipt 存储失败只告警，重试走幂等去重 |
| D 有界性/资源 | PASS | 全部字段上限逐项核对命中；receipt 环 ≤8 FIFO；compiler 线性无 N+1；fingerprint 有界投影 |
| E 数据隔离 | PASS | receipt 只存 session 维度 map_state；日志无 payload |
| F 工具面 | PASS（修复后） | 声明面与实际突变面相符；amendments ≤16 真实强制；`layer_bindings` 补齐数据绑定选择通道 |
| G 测试质量 | PASS | 负例走真引擎（锁阻塞/stale CAS/blocked/非法 amendment）；mutant 推演锁定（paint 整包→minimal 测试红；CAS 恒值→corpus 红） |
| H 兼容性 | PASS | decision_record additive kind 无消费方破坏；manifest/binding conformance 19 passed；lifecycle 回归 29 passed |
| I 文档一致性 | PASS（修复后） | ADR-0214 四处漂移已按实现修正 |

## 发现与清偿

### P1（阻断级，已修复 + 回归测试锁定）

1. **生产路径锁快照缺失 → obligations 前置闸空转 + 部分提交风险**
   - 实证：锁 `pl-li-02-secondary` 后 2 层计划 obligations 报 ok，apply 提交 step1 后 step2 被引擎锁守卫拒 → `failed`，spec 遗留第 1 层。
   - 修复：`MapPlanCompilerService.lock_snapshot_for()`（读引擎 `locked_layer_ids_of`/`locked_component_ids_of`）+ 工具在 `project()` 前传入 `user_locks`；回归测试 `test_workbench_lock_blocks_plan_before_any_commit`（锁 ⇒ blocked、零层提交）。
2. **空 `bound_ref` 编译出无数据层并报成功**
   - 实证：工具全链 `applied`，committed layer `source=''`、sources 出现 `""` 键。
   - 修复：obligations 闸 2 收紧（新建层且目标不在场必须 ref 可解析 → `DATA_REF_UNRESOLVED`）；工具新增 `layer_bindings` 参数（LLM 做绑定选择、编译器验活性）；测试断言无空 source 层落盘。

### P2（已修复 7 项）

- `bound_layer_id` → `options.layerId` 映射（ensure/patch 双分支）+ 测试。
- superseded/error 回执改采信引擎锁内一致读的当前 spec/revision。
- `map_mutations` 声明补 `remove_layer`/`component`/`theme`。
- ADR-0214 文实漂移 ×4 修正。
- determinism 补 current dict 键插入序不变性测试。
- obligations 组件词表剔除引擎工厂不支持的 `basemap`。
- >64 步计划幂等重放受引擎 dedup FIFO 约束 → 已在 ADR D5 披露（安全 superseded 中止）。

### 记录在案（后续方向，不阻断）

- `compile_plan` 纯 CPU 在事件循环上执行；超大 spec（100k 层）建议后续 `asyncio.to_thread`。

## 测试证据（review 修复后全量重跑）

- F12 专属 9 个测试文件：**78 passed**（`-n 0`，约 11s）。
- 兼容性邻域：manifest/binding conformance 19 passed；decision_provenance/replay roundtrip 46 passed；lifecycle 引擎回归 29 passed。
- cartography release-gate 套件：1359 passed / 4 failed —— 4 项失败在干净基线（stash 后）逐项复现，均为 master 存量红（`test_vector_pdf_route` ×3、`test_v4_cartography_libs::test_blend_and_normalize`），与本分支无关。
