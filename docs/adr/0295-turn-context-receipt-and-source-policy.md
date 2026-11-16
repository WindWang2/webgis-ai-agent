# ADR-0295: 本轮实际上下文回执与可控项目资料（G43）

## 状态

已接受（2026-11-15，G43-TURN-CONTEXT-CONTROL）

## 背景

F04（ADR-0208 D2）把 turn prompt 组装成 typed 管线：provider 投影 →
scope 门 → fence/scrub → 去重 → 预算分配 → 渲染，并在管线的末端铸造
`ContextAssemblyReceipt`（item 决策/指纹/预算/digest）。但直到本 ADR 之前，
`bind_turn_prompt` 拿到 `(assembled, receipt)` 后**丢弃 receipt 只返回字符串**
——用户无法知道「这一轮到底把哪些资料交给了模型、为什么少了某份」，也无
法对下一轮的可选资料（项目知识条目、制图记忆事实、GIS 空间记忆块）表达
优先或排除。仓内已有记忆/知识的管理面（retire/activate），但没有「本轮
用了什么」的持久事实面，也没有「下一轮用什么」的会话级策略。

上游 Pi v1.5.2 的 RPC 语义（prompt disposition ∈ handled/queued/started）
只描述 Pi 进程侧的接收结果：`handled` 不代表进入 provider，`queued` 不代表
已消费。任何「已发送 = 模型已读」的等式都是伪造。

## 决策

1. **持久回执（`turn_context_receipts` 表）**：每个 Pi turn 一行，由 bridge
   在真实生命周期点写入：装配完成（`assembled`）→ 模型/预算门拒绝
   （`not_sent` + 闭集 reason）或 prompt RPC 返回（`submitted` + disposition）
   → 进程中断/传输失败（`unknown`）。`turn_outcome`
   （completed/failed/cancelled）独立记录。状态机**单调**：迟到的弱观察
   永不改写已确证的强状态；`turn_id` 唯一保证重复事件幂等归属。
   legacy 装配路径也立行（`assembly_mode=legacy`、coverage limited），
   绝不二次装配补造回执。
2. **源粒度（receipt v2）**：`SourceDecisionLine` 记录每条可选资料的真实
   去向（included/preferred/excluded_by_policy/omitted_card_cap/not_live），
   `version_token` 只在有真实权威版本时填写（知识条目 liveness token、
   事实 last_verified_at）——内容指纹绝不冒充数据库 revision。捕获发生在
   渲染时刻（`entry_refs` 随 ScopedText/卡片返回），不是事后重读。
3. **会话可选资料策略（`session_source_policies` 表，CAS）**：源 id 由服务
   端铸造（`pk:<entry_id>` / `cm:<fact_id>` / `gm:block`），客户端只能提交
   目录内 id；越权/未知 id 统一拒绝（不泄漏存在性）。策略在 turn 装配时
   读一次并冻结（`policy_version` 记入回执）；活跃 turn 不受后续更新影响。
   只有可选 priors 可控——用户消息、verdict、SessionPlan、环境、工具面、
   GIS 状态重建等控制/权威域**永远不能**经该面板关闭。
4. **单一渲染源内实施策略**：过滤发生在 `_build_project_memory_block` /
   `_build_project_knowledge_block` / GisMemoryProvider——typed 管线与
   kill-switch 预拼路径（`legacy_compat`）共享同一实现。显式策略下，不可
   拆来源的 opaque caller 预拼制图块 typed **明确拒绝**
   （`ContextAssemblyRejected`），不做字符串删段猜测；无显式策略的旧路径
   字节不变。
5. **无副作用预览**：预览绝不调用 `assemble_turn_context`（plan slot 创建/
   知识 demotion commit/情境快照推进都是副作用）；只读投影可选资料三域，
   plan/situation/工具面/gis_context 卡如实标 `not_previewed`。预览明确
   标注「非发送回执」；真实装配重新读取权威源并复验。
6. **读面**：会话级 REST（sources/policy/preview/receipts，owner 守卫 +
   项目级 `get_project_with_auth`）+ 只读 `@tool webgis_turn_context_receipt`
   （dispatch 注入 session，绝不升级权限）+ SSE 加性事件
   `turn_context_receipt`（task_start 之后）+ `messages.context_receipt`
   摘要列（G36 attachments 同款）。
7. **用词契约**：UI/工具只说「本轮提交给 Pi 的资料」；queued ≠ 已消费、
   handled ≠ 已运行、估算 ≠ 计费、排除 ≠ 遗忘（仅影响新装配，历史消息
   中的内容不被声称删除）。

## 后果

- 回执/策略行只存有界投影与 server-minted id——不复制知识/记忆内容，
  不保存 prompt 全文、秘密或图片字节。
- `webgis_turn_context_receipts` 迁移纯加性（两新表 + 一可空列）。
- G37 模型门拒绝路径现在产生 `not_sent` 回执（此前用户只能看到错误事件）。
- 旧的 receipt v1 日志消费者不受影响（`as_bounded_dict` 加性字段）。

## 关联

- ADR-0208（F04 typed assembly）、ADR-0206（scope/sensitivity）、
  ADR-0069（项目记忆先验）、ADR-0291（G36 附件回执 —— 本 ADR 的姊妹面）、
  G37（模型/预算门 —— not_sent 的来源之一）。
