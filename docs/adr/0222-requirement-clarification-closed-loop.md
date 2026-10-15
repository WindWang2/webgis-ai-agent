# ADR-0222: Requirement Clarification Closed Loop（blocking 歧义 → 用户 → 应答 → 恢复）

- 状态：Proposed（随 `zcode/requirement-clarification-closed-loop` 分支评审）
- 日期：2026-10-16
- 关联：ADR-0215（F02 Requirement IR）、ADR-0208（refused turn 语义）、
  ADR-0180（SessionPlan kernel envelope）、ADR-0183（goal satisfaction
  投影）、SEC-08（匿名会话 owner_token）

## 背景

F02 交付了 Requirement IR 的歧义账本（`Ambiguity`，context_key 去重、
blocking 策略表、answer/waive patch 协议、provenance、CAS、journal
replay）。但"识别出需要澄清"与"真正让用户回答并恢复执行"之间存在断链：

1. `open_clarifications` 只活在 `webgis_map_intent` 工具载荷里（Pi 可见、
   用户不可见）——无 SSE 事件、无交互卡片；
2. `gis_chapter.intent.clarification.status == "resolved"` 被 kernel
   refusal 谓词（`runtime.py::turn_refusal_candidate`）与 goal evaluator
   （`_derive_signal` → `REQUEST_CLARIFICATION`）两处读取，但**没有任何
   代码写入**——闭环的下游死了；
3. 生产工具缝 `tools.py` 把 session_id 字符串误传为 `session_store`、
   query 误传为 `session_id`——`load_state` 对字符串调 `get_map_state`
   必然抛错被吞，requirement IR 状态**每次意图解析都重建、永不持久**
   （澄清去重/答案 journal 全部失效）；
4. `waive_ambiguity` 只记 `answer=default` 不回写目标字段（与 F02 review
   P1-3 修 answer 回写时的同类缺口）。

## 决策

### D-1 envelope 是派生视图，不是第二真相源

`ClarificationEnvelope`（`requirement_ir/envelope.py`，
`clarification_envelope.v1`）全部字段从 `RequirementDocument` 的 open
blocking 歧义确定性派生；`request_id = "clar-" + document_id + "-" +
fingerprint(revision, sorted(context_keys))[:12]`。同输入必同输出——
GET 恢复面、SSE 发射、答案 CAS 校验共用同一构造函数。歧义账本仍是
"什么未决"的唯一真相；suppression/telemetry 记账
（`requirement_clarification_ledger`，session map_state 独立键）只记
"哪些 context_key 已在 SSE 上问过"，丢失不影响需求真相（fail-open）。

### D-2 答案的 authority 全部走既有 patch 协议

`submit_answers` 不另起状态机：幂等检查（重复同答案 → 同结果，先于
revision CAS）→ request_id 与文档派生值比对（防跨会话/伪造 id）→
`expected_revision` CAS（stale → typed 409）→ 答案经 option value/label
或词表归一（formats/statistics）+ patch 目标 path 既有 validator 预检
（fail-closed，无部分应用）→ 转确定性 `op_id` 的 `answer_ambiguity` /
`waive_ambiguity` patch（provenance origin=user；agent actor 在服务层与
patch 层双重拒绝代答 blocking）。waive 显式豁免时把安全默认写回目标
字段并以 `origin=default + rationale` 披露（默认值不是用户选择，不冒充
user-wins）。

### D-3 传输：additive SSE 事件 + 章节 pending/resolved 单写者

- `clarification` 事件注册进 `CHAT_EVENT_TYPES`（additive 词表）；Pi
  事件泵在 `agent_settled` 分支**先**计算 envelope + 章节 pending 章
  （`HarnessKernelRuntime.update_chapter_clarification`，事件
  `clarification_updated` 入 EVENT_KINDS）再跑 settle 投影——goal
  evaluator 的 `REQUEST_CLARIFICATION` 与 refusal 降级谓词读到同一份
  事实；循环 break 后在 `map_finalization` 与 `done` 之间发射事件
  （TurnResumeRegistry ring 自动覆盖断线重连重放）。never-raise：澄清
  面故障绝不阻断收口。
- suppression：只发"本 turn 新出现"的未决问题（context 未变不重复问，
  telemetry 记 `suppressed`）。
- 恢复：`GET /chat/sessions/{sid}/clarifications/pending`（204/200）；
  前端 `selectSession` 历史载入后重挂卡片（历史 transcript 只有纯文本，
  卡片不落历史）。

### D-4 应答端点镜像 destructive-confirm 先例

`POST /chat/sessions/{sid}/clarifications/answer`
（`require_owned_session`；typed 状态码映射：409 stale/already_answered、
404 unknown/absent/所有权、400 invalid）。全部答完 → 章节
`status="resolved"` + answers（kernel/goal 两读取点即闭环）；仍有
pending → 章节问题滚动到下一个。恢复执行由前端按 PlanProposalCard
先例发送续行消息（后端不自动起 turn——尊重单 POST 单 turn + turn lease
模型，destructive 工具绝不自动重放；下一 turn 的 `ensure_document` 幂等
收敛，已完成 evidence 不失效）。

### D-5 并发：进程内 per-session 写锁

answer 端点在 turn 之外执行，与 Pi turn 内 `ensure_document` 存在并发
窗口。`service.py` 增加 bounded per-session `asyncio.Lock`
（`session_writer_lock`），`ensure_document` / `apply_user_patches` /
`submit_answers` 全程持锁（无锁内层供组合调用，避免重入死锁）。跨进程
双写仍是文档化的 best-effort（与 F02 既有并发披露一致）。

## 已否决

- ✗ 后端 answer 后内联起 Pi turn（破坏 turn lease/流模型；答案落地与
  恢复执行解耦才是可审计的）；
- ✗ 前端把答案当自由文本发给 Pi 再 NLP 解析（丢结构、不可审计、agent
  可能代答）；
- ✗ 新建 clarification registry/store（第二真相源）；
- ✗ 复制 `ClarificationRequest` 成新协议（IR Ambiguity + 兼容出口
  `to_legacy_request` 已够）。

## 已知限制 / 后续

- `measure_multiple_candidates`（字段二义）协议面已可传输/可答，但
  field_resolver `needs_clarification` 回流到 IR 歧义账本的生产接线是
  后续工作（执行期提升通道）；
- 用户纯文本聊天回答（"用 pdf 吧"）由 agent 经工具面消化；结构化
  REST 是唯一权威应答通道；
- 跨进程双写（多副本部署）为 seq fencing + 进程内锁：栅栏输家的写入
  被检测并转 typed `requirement_persist_fenced`（409，客户端重读重试
  收敛），不再静默丢失；未引入分布式锁（与 F02 披露一致）。

## 独立 review 清偿记录

P0：超替文档 revision 跨代单调（生产 store 的 `set_map_state(seq)`
单调栅栏会静默拒绝 revision 回退到 1 的超替写入——多任务会话的 IR
状态此前永远无法前进）；P1：部分作答后兄弟未决问题解除发射抑制 +
前端仅在 `resolved_all` 时锁定/续行；`save_state` 检查栅栏拒绝并转
typed 错误。P2：revision CAS 先于 request_id 匹配（语义 409 而非 404）、
waive 面 open-blocking 成员检查 + patch 层 owner 门、answer+waive 同
key 预检、bridge collect 失败指标、章节 pending 章 raised_turn_id 覆盖
+ resolved 空块守卫 + "clear" 对账路径、前端跨会话守卫与单 live 卡
不变式、replay 不变式钉住 answer/waive 分支。

二轮复核（全部 VERIFIED-FIXED 后的新发现，已清偿）：超替后 suppression
记账按 request_id 的 document lineage 前缀剔除（两任务撞同一
context_key 时新任务问题不再被旧任务记账永久抑制）；无变化重复 edit
跳过写库（同 seq 重写曾被栅栏误报为并发输家）；前端部分作答后 pending
刷新失败 → 卡片转 error 可重试（不再卡 submitting）。
