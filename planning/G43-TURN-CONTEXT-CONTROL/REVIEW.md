# G43 REVIEW — 独立评审记录

## Round 1（reviewer：未参与实现的独立 agent；只读审查 base..7f5b24db8）

结论：**BLOCKING (P0/P1) = 1**；两个 P2 建议修复；安全姿态/状态机诚实性/策略
单一渲染源/迁移卫生/复用纪律被评为 solid；旅程声明的事实核验为准确
（真实 Pi 子进程 + stub 记录完整 prompt + 非空守卫断言）。

| # | 级别 | 发现 | 修复（c05ae0cb9） |
|---|---|---|---|
| 1 | **P1** | 预览并非无副作用：`read_only` 参数存在但零调用方；preview → `_build_project_knowledge_block` 仍执行 demotion + db.commit()，与 ADR-0295 决策 5 矛盾；C07 测试刻意无真实项目未验证 | `SharedTurnFacts.preview_mode`（仅 preview 置 True）→ provider → builder → `load_project_knowledge_card(read_only=True)` 跳过 demotion+commit；新增行为测试（真装配降级漂移条目 / 预览后保持 active）+ flag 转发测试 |
| 2 | P2 | 流式失败路径（模型门/RPC 异常）持久化的消息摘要停留在 `assembled`（durable 行正确，SSE 事件正确，但刷新后气泡摘要陈旧） | `mark_not_sent`/`mark_unknown` 返回刷新读模型；bridge 四处失败路径同步刷新 `_turn_receipt_read`/`_ns_receipt_read`；移除 model-gate 的 persist-if-None 补丁 |
| 3 | P2 | 策略 CAS 非原子（read-compare-write 无锁）：并发同版本 PUT 可双胜（丢失更新） | 条件 `UPDATE ... WHERE policy_version=:expected` + rowcount 裁决；首插竞态 IntegrityError → 冲突；失败路径 rollback 后取当前值 |
| 4 | P3 | `ProjectKnowledgeCard.chars` 注解错改 `str`（返回 int） | 回 `int` |
| 5 | P3 | reducer 同 rank 迟到事件可替换已确证态；fallback 挂载启发式在 abort/重试竞态下可能误挂 | 严格更强才替换（与后端状态机一致）；仅挂最后一条无回执 user 消息（迟到宁可丢弃不误归因）；新增 vitest 用例 |
| 6 | P3 | `record_submitted` 未知 disposition 丢弃整个转移 | 收敛为 `started` |
| 7 | P3 | `not_sent_reason` 未进 SSE payload（面板死代码） | 两分支均携带 |
| 8 | P3 | 死代码 `message_receipt_summary` | 移除 |
| 9 | P3 | typed 意外失败 → legacy 回退可注入 opaque 预拼块（显式策略下与 typed 拒绝路径不一致） | 回退前显式复核策略（读失败保持旧行为）；explicit + 块在场 → re-raise ContextAssemblyRejected |
| 10 | P3 | preview 草稿策略无结构校验（mode 任意） | 与 PUT 同规则 `validate_policy_payload` + auto_with_selection 拒绝 |
| 11 | P3 | i18n JSON 丢尾行 | 恢复 |

分类结论（Round 1 原文摘要）：信息泄漏/授权 clean；假发送仅 #2；策略绕过实践
clean（#9 为纵深缺口）；控制面完整性 clean；并发仅 #3；前端基本正确（#5 nit）；
迁移 clean（单一 head 验证）；复用纪律 clean；测试诚实（#1 的声明未行使是唯一例外）。

## Round 2（rereview：只读审查 7f5b24db8..c05ae0cb9）

**结论：ACCEPTABLE。** 11/11 findings 全部验证 FIXED（#5 实质半修复 +
另一半为行为等价的 no-op）；无 P1/P2/P3 回归；新增观察仅 P4 级：

- N1（P4）sse_payload both-None 分支缺 not_sent_reason 键（TS 可选，无 UI 影响）
- N2（P4）双失败边缘（装配落盘失败 + 模型门拒绝）：on_turn_result 摘要
  fallback assembled + persisted:false —— 诚实且严格优于旧代码
- N3（P4）reducer 挂载 break→return 行为等价（信息性）
- N4（P4）preview 草稿未用去重后的 preferred/excluded（集合语义，无害）
- N5（info）preview.py:122 的中间链路由两端测试间接钉住

reviewer 特别核验：
- P1 残余写面清扫 clean（memory builder consume=False/无 commit；knowledge
  唯一 commit 已被 read_only 守卫；gis_memory queries/retrieval 零写；
  _readonly_facts 无 ensure slot/情境推进；预览 provider 列表排除写面 owner）
- CAS 方言正确性：sqlite/aiosqlite 与 postgres/asyncpg 单行 UPDATE rowcount
  均可靠；READ COMMITTED 下并发败者 rowcount=0；rollback 前置安全（自有会话）
- 严格更强 reducer 不丢必要更新（SSE 每 turn 恰一事件；turn_outcome 走服务端
  持久摘要）
- 新测试非空转（真引擎/真模型/真 liveness；引用恒等断言钉零状态变化）
- 移除的 persist-if-None 补丁实际有害（旧序可插入卡在 assembled 的新行），
  移除无损（无行时 persisted:false 诚实回退）

最终判定：可合并（无需第三轮）。
