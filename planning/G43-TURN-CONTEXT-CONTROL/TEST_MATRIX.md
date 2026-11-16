# G43 TEST_MATRIX — 场景 → 证据层级

层级：U=单元（pytest）/ DB=DB 集成 / API=route 合同（真实 app 栈）/ FE=组件（vitest）/
**PI=真实 Pi 旅程**（bundled v1.5.2 子进程 + 生产扩展 + 生产 dispatch + stub LLM 供应商边界 + 真实 HTTP/SSE）。

| 场景 | 层级 | 证据 |
|---|---|---|
| C01 双成果条目included+真实版本 | **PI-J1** | journey: pk2024/2025_included（version_token=fp-*） |
| C02 优先选入下一轮生效 | **PI-J2** | journey: policy_saved + pk2025_preferred + 边界含 2025 |
| C03 排除旧资料 | **PI-J2** | journey: pk2024/cm2024 excluded + 边界不含 2024 + 共享事实未删（seed 行仍在） |
| C04 历史消息回执 | **PI-J5** | journey: history_message_receipt（messages.context_receipt） |
| C05 预算截断省略 | U | card omitted_ids/omitted 计数 + receipt as_bounded_dict |
| C06 预览 vs 实际差异 | API/PI | preview is_preview + disclaimer；J2 实际与预览口径同链 |
| C07 预览无副作用 | U+API | ensure_session_plan_slot 断言不被调用；not_previewed 域 |
| C08 控制域不可关 | U | 可选域闭集（source_policy.optional_domains）+ auto 不改默认 |
| C09 伪/越权源 ID | U+API | source_id_malformed / source_not_authorized 统一 400 |
| C10 控制标记注入 | 既有 | fence.py 契约（未改动，复用）；数据不可信纪律在 receipt/文档声明 |
| C11 优先不破预算 | U | preferred 只影响排序，allocator 上限不变（test_g43_policy_assembly auto 等价 + card cap 测试） |
| C12 CAS 冲突 | **API+FE** | 409+current；PolicyConflictError→conflict UI（turn-context-sources.test） |
| C13 活跃 turn 冻结 | U+**PI-J2** | facts 冻结 + policy_version_frozen（PUT 后下一 turn 才变） |
| C14 版本变化复验 | U | 渲染时刻 live_versions 捕获；not_live 如实（liveness 现取） |
| C15 权限撤销/stale | U | not_live_ids 披露 + 目录按 get_project_with_auth 过滤 |
| C16 typed 失败不 fallback 重注入 | U | 显式策略+预拼块 ContextAssemblyRejected（不落 legacy） |
| C17 索引≠全文 | FE | panel estimateNote/historyNote + sourcesTitle 文案（i18n） |
| C18 装配后预算拒 | **PI-J4** | receipt not_sent + model_budget_rejected + stub 计数 0 |
| C19 handled | U（状态机）+SSE payload | disposition 独立列；sse_payload 不覆盖 |
| C20 queued 后取消 | U+DB | submitted+queued + turn_outcome=cancelled 分列 |
| C21 socket 失败/进程死 | U+DB | record_unknown 单调（不降级 submitted）；bridge PiRpcError→mark_unknown |
| C22 回执落盘失败 | U+DB | record_assembly 返回 None；SSE persisted=false |
| C23 重复/迟到事件 | U+DB+FE+**PI-J5** | turn_id 幂等；reducer 单调；history_receipts_deduped |
| C24 模型切换预算差异 | 复用 G37 | not_sent_reason=model_budget_rejected 联动（PI-J4） |
| C25 跨租户 | API | 404 不泄漏存在性（含他人 turn_id） |
| C26 秘密/大载荷 | U+设计 | payload=as_bounded_dict（指纹/计数）；secret scrub 既有 |
| C27 同文本双源 | U | 源身份=命名空间 id（pk:/cm:），决策按 id 分列 |
| C28 空项目 | FE+API | noProject/empty 文案；AUTO_POLICY |
| C29 legacy 无回执 | U+API+tool | coverage limited；RECEIPT_NOT_FOUND 说明 |
| C30 排除≠遗忘 | FE+tool | UI 固定披露「不删除/不移除历史」；工具 semantics 字段 |
| C31 UI→Pi→工具→dispatcher | **PI-J3** | tool_search 发现→直调→step_result 含回执 |
| C32 选择→领域产物 | **PI-J2** | 边界证明：所选资料版本进入真实请求 |
| C33 provider 边界对账 | **PI-J1/J2** | stub 完整 prompt 断言（含/缺席） |
| C34 流式/非流式一致 | **PI-J5** | 非流式 turn_receipt + 同策略源去向一致 |
| C35 compaction 后重建 | 既有 G22 | 未触碰；policy 沿会话（load_policy per turn） |
| C36 换代/重启 | DB | receipts/policies 持久；J4 新会话按当前权限装配 |
| C37 独立于 G42 | 全部 | 本 PR 无 G42 依赖（grep 无 repair 模块引用） |
| C38 源已删/撤权 | U | not_live / 目录空 → 拒绝 |
| C39 预拼块含被排除资料 | **PI-J2 语义**+U | typed 拒绝路径单测；旅程走 kill-switch ON 主路径 |
| C40 kill-switch 兼容 | U+**PI-J1** | 无显式策略字节等价；legacy_compat 同策略加载 |

## 未测/环境限制（如实）

- 真实浏览器 Playwright 走查未运行（frontend 有 vitest 组件级 + i18n；浏览器链由
  会话恢复/消息流的生产 API 面覆盖——journey J5 的 messages.context_receipt 即恢复数据源）。
  本地未启动浏览器栈（资源约束），不宣称浏览器级证据。
- C19/C20 的 handled/queued 分支在 bridge 单元层由状态机测试覆盖（真实 handled 需要
  Pi input-handler 配置，本旅程未构造）。
