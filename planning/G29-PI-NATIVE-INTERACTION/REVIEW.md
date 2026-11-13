# G29 REVIEW — 独立评审 / 修复 / 复审

## Round 1 — 独立评审（reviewer：未参与实现的 subagent；2026-11-13）

结论：**APPROVED-WITH-NITS**（无 P0/P1）。安全不变量（owner 门禁、HMAC 封套
防伪造、至多一次状态翻转、teardown/迟到回答拒绝、无误判 stall）经对抗性
阅读与单测通过确认成立。12 项发现：

| # | 级 | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| 1 | P2 | pi_runtime_interaction.py | `_awaiting_envelopes` 类级可变属性（跨实例共享）且永不修剪 → 无限增长 | ✅ 改实例级；`invalidate_turn` 清 turn 队列；`expire_due` 内 `_prune_expired_envelopes`（exp 过期修剪） |
| 2 | P2 | cartography_tools.py | 同名候选先截断到 8 再 mint —— mint 上限分支成死代码，标题计数与选项数不一致 | ✅ 全量候选过 mint（>8 由 `ENVELOPE_TOO_MANY_OPTIONS` 拒绝并引导缩小范围）；PLAN.md 措辞同步（12→8） |
| 3 | P2 | TEST_MATRIX/LOOP_LEDGER | 文档声称存在「跨语言 canonical JSON 单测」但仓库无此测试（只有 opt-in 旅程覆盖） | ✅ 新增 committed 单测 `test_envelope_signature_matches_node_verifier`（node 子进程加载生产 index.mjs 的 verifyEnvelope：正向验签 + 嵌套字段篡负例）；文档改为如实描述 |
| 4 | P3 | pi_runtime_interaction.py | docstring 声称 SessionStore 持久化未实现（STORE_KEY 死常量） | ✅ 删除声明与常量；改为「不跨进程复活（boot 代际只读终止）」如实描述 |
| 5 | P3 | expire_due | 无锁翻转状态与在飞 submit_answer 竞争（窗口窄，无 corruption 但终态可被覆写） | ✅ `record.lock.locked()` 跳过，下一心跳再处理 |
| 6 | P3 | _handle_dialog | 会话上限拒收先 pop 封套 → 扩展 resolve 只能得 ENVELOPE_NOT_PENDING（isError 而非干净取消分类） | ✅ 上限判定移到配对 pop 之前 |
| 7 | P3 | use-sse-stream.ts | 硬编码中文 steer 文案绕过 i18n | ✅ `steerQueued`/`steerSent` 双 locale 键 + 命令式 `t()` |
| 8 | P3 | pi_interactions.py | `_TURN_MESSAGES` 会话键永不回收 | ✅ 全局 LRU 上限 512 会话（OrderedDict move_to_end + popitem） |
| 9 | P3 | use-sse-stream.ts | 跨会话守卫只覆盖 pi_interaction 分支 | ✅ update/turn_message 分支补同款守卫（steer 载荷补 session_id 字段） |
| 10 | P3 | worktree | 空杂散文件 `9` | ✅ 删除 |
| 11 | P3 | journeys | `pi_interaction` 子串断言会匹配 `_update` | ✅ 精确 `event: <name>\n` 匹配 |
| 12 | P3 | runtime-interaction-card | confirm 的 message 渲染两次 | ✅ 移除方法块内重复渲染 |

评审同时确认无问题的关键面（原文照录要点）：加密管线（canonical JSON 等价性
/常量时间比较/exp 双侧）、状态机（双提交幂等/迟到/跨会话/worker-gone/超时不写
stdin）、bridge 集成（记账数学/排除合成事件的续接观察/全部 stdin 写经锁）、
reader 路由、路由门禁（steer gate 纪律/resolve 三重校验）、工具层零漂移、
SSE 词表/纯翻译、前端 fail-closed 与 IME 安全、旅程真实性（真子进程/真扩展/
真 dispatch，MapSpec 权威断言）。

## Round 1 修复后复验（同 worktree）

- 单元 29 passed（新增跨语言签名测试含篡改负例）。
- 真实 pin 旅程复跑 1 passed（104s）—— P2-1/P3-5/P3-6 涉及运行时路径，必须复验。
- 前端 vitest 56 passed + `tsc` 0 + eslint 0；ruff touched-set 全过。
- `git diff --check` 干净。

## Round 2 — 复审（独立 reviewer 复核；2026-11-13）

结论：**APPROVED**。12 项逐条复核：#1-9、#12 FIXED（附 file:line 证据）；
#10（杂散文件）复检时仍在（作者的 shell 管道形态反复产生）→ 已删并改用
`flock "$LOCK"` 正确形态；#11 PARTIAL（:671 残留子串）→ 已改精确匹配。
复审新发现 4×P3（confirm 显示硬编码中文 / :671 残留 / 死代码
envelope_by_native_id+pop_envelope_by_id / P3-6 注释理由不准）+ 1 项记账
粒度备注（按迭代而非墙钟，有界、不影响 stall 语义）——前四项已修（含删除
死代码），记账备注保留现状（900s 帽内有界近似）。

复审明确核清的修复交互面：配对 pop 不会双配对（出队即移除；consume 读
record.envelope）；LRU 逐出仅在 512 交错会话后才影响只读台账；命令式 t()
SSR 安全（client-only 文件、回调内调用）；双 locale 键集对称。

nit 清偿后复验：真实 pin 旅程 1 passed（101s）；前端 tsc/eslint 0 +
vitest 51（受影响面）；ruff 全过。

**最终裁决：APPROVED（P0/P1/P2 = 0）。**
