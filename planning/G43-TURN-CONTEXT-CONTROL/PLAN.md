# G43 PLAN

## 交付物（映射工作包）

1. **WP01/02 源矩阵 + 回执模型**：`source_policy.py`（源 ID 命名空间/可选域闭包/策略对象）；
   `receipt.py` 加性扩展 `SourceDecisionLine`（source_id/kind/decision/version_token）；schema `ctx.receipt.v2`。
2. **WP03 持久化**：`app/services/turn_context_store.py`（TurnContextReceiptRow/SessionSourcePolicyRow 模型入
   db_model.py + 迁移 `g43_turn_context_receipts`）；状态机 assembled/not_sent/submitted/unknown + disposition
   + turn_outcome；幂等单调更新；bridge 双路径（stream/nonstream）窄接线；`messages.context_receipt` JSON 列
   关联用户消息（G36 attachments 同款）。
3. **WP04/05 策略 + provider 落实**：policy 表（session 级，policy_version CAS）；源目录服务（授权校验 pk/cm/gm）；
   `SharedTurnFacts.source_policy`（fetch_shared_facts 与 legacy_compat 两编排点统一加载，单次 I/O）；
   memory/knowledge/gis_memory provider 过滤；card.py 返回 included_ids + read_only 变体；显式策略 +
   caller 预拼块 → typed 拒绝（ContextAssemblyRejected），默认路径字节不变。
4. **WP06 预览**：`preview` 服务复用 provider + 只读 facts（不 ensure slot、不 demote、不 situation advance、
   不 surface marker）；honest not_previewed 列表。
5. **WP07 权限**：route 层 conversation/project 授权（404 不泄漏存在性）；源 ID 服务端解析，无任意正文/路径；
   回执/日志有界（指纹/计数，无全文/秘密）。
6. **WP08 冻结/并发**：策略在 turn 装配时读一次并冻结（policy_version 记入 receipt）；活跃 turn 不受后续策略
   变更影响；CAS 冲突 409 + 当前值返回。
7. **WP09 UI**：`turn-context-panel.tsx`（消息内可展开回执 + 源清单 + 状态徽标）+ `turn-context-sources.tsx`
   （可选资料优先/排除工作台 + CAS 冲突处理 + 预览）；SSE `turn_context_receipt` 事件；message-reducer 扩展。
8. **WP10 @tool**：`webgis_turn_context_receipt`（只读，当前 turn 默认/历史 turn 可查，授权校验，有界回执）。
9. **WP11 长会话**：策略沿 session 生效；新原生会话重取当前权限；历史 receipt 缺失 → coverage limited/unknown。
10. **WP12 证明**：pytest 单元/集成（状态机、CAS、policy 过滤、预览无副作用、权限）、vitest 组件、
    真实 Pi 子进程旅程（本地 stub LLM）+ provider 边界断言 + Playwright 浏览器链。

## 实施顺序

A. 源策略契约 + receipt 扩展（纯加性，先行可测）
B. store + 迁移 + 模型
C. provider 过滤 + 编排点加载 + 预拼块拒绝
D. bridge 接线（receipt 生命周期 + SSE + on_turn_result）+ chat route 消息关联
E. API route + 预览 + @tool
F. 前端（API client → SSE → 面板 → 工作台）
G. 测试矩阵逐场景补齐 → 真实旅程 → review → PR

## 关键决策

- 不新建 store：pk/cm/gm 复用既有查询；receipt/policy 表只存策略与装配事实（bounded JSON，无全文）。
- 预览与真实发送分离：预览绝不调用 `assemble_turn_context`（有副作用），走只读投影子集 + honest 披露。
- 排除 ≠ 遗忘：UI 文案固定披露「仅影响本轮之后的新装配」。
- `handled` ≠ 已发送成功；`queued` 保持未确证；进程死亡 → unknown（不复制上一 turn）。
- G40 已合并：迁移 down_revision 取运行时实际 head（g40_cartographic_symbol_assets）。
