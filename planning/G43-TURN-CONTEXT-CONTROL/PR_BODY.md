# G43｜本轮实际上下文回执与可控项目资料工作台（ADR-0295）

## 解决的用户问题

用户无法知道「这一轮到底把哪些项目资料交给了模型、为什么少了某份」，也无法对下一轮的可选资料（项目知识条目 / 制图记忆事实 / GIS 空间记忆）表达优先或排除。F04 typed assembly 一直在铸造 `ContextAssemblyReceipt`，但 `bind_turn_prompt` 拿到后直接丢弃——没有持久事实面、没有策略入口、没有 UI。

## 行为增量

1. **持久 turn 回执**（`turn_context_receipts` 表）：每个 Pi turn 一行，bridge 在真实生命周期点写入——装配完成（assembled）→ 模型/预算门拒绝（**not_sent** + 闭集 reason）或 prompt RPC 返回（**submitted** + disposition started/queued/handled）→ 进程中断（**unknown**）。`turn_outcome`（completed/failed/cancelled）独立分列。状态机单调：迟到的弱观察永不改写已确证的强状态；`turn_id` 唯一保证重复事件幂等归属。legacy 路径也立行（coverage limited），**绝不二次装配补造回执**。
2. **源粒度**（receipt v2）：`SourceDecisionLine` 记录每条可选资料的真实去向（included/preferred/excluded_by_policy/omitted_card_cap/not_live）+ 真实权威版本 token（知识条目 liveness token / 事实 last_verified_at）——内容指纹绝不冒充数据库 revision；捕获发生在渲染时刻，非事后重读。
3. **会话可选资料策略**（`session_source_policies`，CAS）：源 id 由服务端铸造（`pk:<entry_id>` / `cm:<fact_id>` / `gm:block`）；目录经 owner + 项目级 `get_project_with_auth` 双重鉴权；越权/未知 id 统一拒绝（不泄漏存在性）。策略在 turn 装配时读一次并冻结（policy_version 记入回执）；活跃 turn 不受后续更新影响。**只有可选 priors 可控**——用户消息、verdict、SessionPlan、环境、工具面、GIS 状态重建等控制/权威域永不能经该面板关闭。
4. **单一渲染源内实施策略**：过滤发生在 memory/knowledge builder 与 GisMemoryProvider——typed 管线与 kill-switch 预拼路径共享同一实现。显式策略下不可拆来源的 opaque caller 预拼制图块 typed **明确拒绝**（不做字符串删段猜测；无显式策略的旧路径字节不变）。
5. **无副作用预览**：绝不调用完整装配（plan slot 创建/知识 demotion/情境推进都是副作用）；只读投影可选资料三域，plan/situation/工具面/gis_context 卡如实标 `not_previewed`；明确标注「预览非发送回执」。
6. **读面**：会话级 REST 六端点（sources/policy/preview/receipts×2 + 单 turn）+ 只读 `@tool webgis_turn_context_receipt`（dispatch 注入 session；tool_search 发现链）+ SSE 加性事件 `turn_context_receipt`（task_start 之后）+ `messages.context_receipt` 摘要列（G36 attachments 同款）+ 非流式响应 `turn_receipt` 字段。
7. **前端**：用户消息下可展开的「本轮提交资料」面板（状态徽标 + 源去向 + 版本 token + 诚实文案：not_sent/queued≠已消费/handled≠已运行/估算≠计费/排除≠遗忘）；会话资料工作台（三态优先/自动/排除 + 预览 + CAS 409 冲突合并提示）；SSE reducer 按 turn 幂等挂载 + 状态单调；会话恢复经 `messages[].context_receipt`。

## 复用与范围

复用既有 F04 providers/allocator/fence、G36 附件回执模式、G37 模型/预算门（not_sent 的来源）、记忆/知识管理面（不重做）。**不新建** memory/knowledge store、第二 agent loop、第二工具注册表；不改 Pi pin（b4afdddf，G22 对齐）；G36 图像/G37 用量权威只链接不重管。与 G42（goal repair，未合入）零依赖；与开放 PR #2125（G47）仅 `app/tools/__init__.py` 一行加性列表重叠。

## 实际验证（真实执行，非自述）

- **真实 Pi 旅程**（`PI_JOURNEY_E2E=1`，bundled vendor v1.5.2 子进程 + 生产扩展 + 生产 dispatch + stub LLM 记录完整供应商请求）——`tests/integration/test_pi_g43_turn_context_journey.py` **1 passed**：
  - J1：auto 策略 turn → SSE 回执事件 + 持久回执 submitted/started + 2024/2025 知识条目 included（真实 version_token）+ **供应商边界**：实收 prompt 含双条目渲染行；
  - J2：PUT 排除 2024（pk+cm）/优先 2025 → 下一 turn 回执 excluded_by_policy/preferred 如实分列 + policy_version 冻结 + **边界缺席证据**：实收 prompt 不含 2024 行、仍含 2025 行（共享事实行未删——排除≠删除）；
  - J3：tool_search 发现 → `webgis_turn_context_receipt` 直调 → step_result 含当前授权回执（真实 dispatch）；
  - J4：token cap=1 → 装配后被预算门拒绝 → 回执 **not_sent(model_budget_rejected)** + stub 对该会话**零请求**（零假发送）；
  - J5：非流式同语义（响应携带 turn_receipt + 同策略源去向一致）+ 历史消息 context_receipt 逐 turn 去重。
- **单元/DB/API/FE**：`tests/test_g43_turn_context_store.py`（15）+ `test_g43_turn_context_routes.py`（8）+ `test_g43_turn_context_tool.py`（7）+ `tests/unit/context_assembly/test_g43_{source_policy_receipt,policy_assembly}.py`（19）+ frontend vitest（9）全绿；context_assembly 既有套件 106/107（1 失败为基线既有，#2123，干净 base 复现）；G37 套件 42/42；ruff 本域 0 错误；`tsc --noEmit` 本域 0 错误（cartography-result-card 2 处为基线既有）。
- **迁移** `g43_turn_context_receipts`（down_revision=g40_cartographic_symbol_assets，单一 head）：空库 → head ✓；g40 head → g43 head ✓（两新表 + `messages.context_receipt` 可空列，存量行零改动）；OpenAPI 快照已按流程刷新。

## 诚实边界（未测/已知限制）

- 真实浏览器 Playwright 走查未运行（本机资源约束）；前端有 vitest 组件级证据，恢复数据源（`messages[].context_receipt`）由旅程 J5 在生产栈验证。
- handled/queued 的 bridge 分支在单元层由状态机测试覆盖（真实 handled 需 Pi input-handler 配置，旅程未构造）。
- 回执 tokens 为渲染估算（同一 estimator），不是供应商计费——UI/工具文案已固定披露。

## Review / Fix / Rereview

- 独立 review（未参与实现的 reviewer，只读 diff 审查）：见 planning/G43-TURN-CONTEXT-CONTROL/REVIEW.md。
- 阻塞项修复 + 定向复审记录于同文件。

## 迁移与回退

纯加性：两张新表 + 一个可空列；`downgrade` 逆序删除。无数据迁移；旧会话无回执行时 UI 显示 coverage limited（不补造）。

## 关联

ADR-0295；复用 ADR-0208（F04）/ ADR-0206（scope）/ ADR-0069（记忆先验）/ ADR-0291（G36 附件回执模式）/ G37 模型门。基线 master @ 655f769ca（含 G40 #2124 与 #2122 迁移合并）。
