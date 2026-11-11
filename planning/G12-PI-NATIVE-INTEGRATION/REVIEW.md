# REVIEW — G12-PI-NATIVE-INTEGRATION

## 第一轮独立评审（BLOCK → 已全部修复）

- Reviewer：独立 general-purpose subagent（`agent_d0367ff2`，未参与实现；给定
  base `816705218a` / head `b7cba0d4` + 完整 diff + 用户场景，未预设通过）。
- 裁决：**BLOCK — P1×4 + P2×4 + P3×1**。
- 修复 commit：`见 git log`（"fix(pi): 独立评审修复……"）。

### P1 逐条清偿

| # | 发现（reviewer 证据） | 修复 | 覆盖 |
|---|---|---|---|
| P1-1 | grant 自授权：任何注册面直调可自铸 grant（repro：`granted_without_tool_search ('nearest_facility',)`）；旧负面测试被反转为执行成功 | **语义澄清 + 收紧**：vendor 源码核实（`_getCallableTools`：codemode/deferred 无论 active 均可调；模型直调在 active 集内）→ deferred 直调/沙箱嵌套天生合法，grant 服务这两个场景而非「先搜索才能调」；grant 只接受 **deferred（非 native 前门）**；恢复 tier-3/destructive/hidden 拒绝 + 计数器零执行 + 未注册名 resolve 拒绝 | `test_scope_enforcement_negative_types_rejected`、`test_scope_enforcement_unregistered_name_rejected`、attack-matrix 语义文档 |
| P1-2 | 配置换代只在校验 sweep 触发；在飞 turn deferred 后下一 turn 不恢复（旧配置无限期沿用） | `chat.py` turn 准入处（surface recycle 旁）增加 `recycle_if_config_stale()` | 测试补「turn 结束后下一 turn 边界必须完成换代」断言 |
| P1-3 | 继承的 OPENAI_API_KEY 压过解析新 key，而 generation 哈希新 key（期望/生效对账失真） | `_spawn_process` 解析 key **覆盖** env（单一权威优先级） | A10 测试补继承 env key 场景（真实请求用新 key） |
| P1-4 | rebuild 读错形状：`layer.visible` 而非 canonical `layer.layout.visibility`；`plan.capabilities`/`recent_evidence_refs` 在真实 SessionPlan 不存在 → 隐藏层误投可见、待办/证据永不投影 | `_layer_is_hidden`（layout.visibility 优先 + 旧字段 fallback）；`open_capabilities(plan)`（progress 语义）；`PlanStep.evidence[].ref`；测试改用**真实** SessionPlan/PlanStep/StepEvidence + canonical 隐藏层回归 | `test_rebuild_block_hidden_layer_not_misreported_visible` 等 5 例 |

### P2/P3 清偿

- **P2-1** clear_queue TOCTOU：发出前重校验在飞 turn 身份，翻转则跳过 clear+abort（不清错队列/误杀新 turn）。
- **P2-2** J6b 掩盖选择器回归：恢复原始两段式裸名流程；`declared_nearest` 降为证据字段。**存量抖动证据**：master@v1.5.0 干净基线（G21 worktree）10 跑 4 失败，同签名（`nearest_ok:False` + step_error）——非本 PR 引入，如实披露；确定性发现覆盖由新 J8 承担（driver 3/3：search→声明面 merge→buffer 真实执行）。
- **P2-3** fresh-spec fail-open：非空 echo + fresh spec 不可编译 → fail-closed（echo 缺席才放行）。
- **P2-4** 层截断披露：>12 每类遗漏计数披露（含隐藏类）。
- **P3-1** ADR compaction 钩子名（`session_after_compact` 不存在于 v1.5.1 → 改为实际存在的 before/failed）；回退文档补 `PI_MIN_VENDOR_TAG` 回退要求。

### 评审已验证项（reviewer 结论，复述）

- vendor checkout = tag v1.5.1 原样；C11–C16 契约描述与 vendor 源码一致
  （tool_search merge / nested parentToolCallId / clear_queue / EOF shutdown）。
- `parent_tool_call_id` 加性且顶层缺席；OpenAPI 无破坏性变更。
- J6b 存量抖动干净基线复现（同上）。

## 复审（第二轮）

- 复审范围：上述 9 项修复的 diff + 全量回归证据。
- 复审结论：见下方「复审记录」。

## 复审记录

（待第二轮 reviewer 返回后填写）
