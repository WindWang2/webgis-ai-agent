# F04 — Independent Review Gate 记录

- Reviewer：Subagent C（独立，只读），基线 `9e1ad229`，review 对象 `origin/master...HEAD`。
- 首轮结论：**NO-GO**（1×P0、4×P1、10×P2）。以下为逐项处置。

## P0

| # | 发现 | 处置 |
| --- | --- | --- |
| P0-1 | `ProjectKnowledgeProvider`/`GisMemoryProvider` 未标注 sensitivity（默认 SESSION_LOCAL、scope_id=project/org id）→ scope gate 按 session 比对必然失败，项目会话默认路径静默丢失 `<project_knowledge>` 与 `[GIS_MEMORY]` 两块 | knowledge → `PROJECT_SCOPED`；gis_memory → `ORG_SCOPED`（ADR-0183 语义）；新增 provider 全链路 gate 测试（`test_review_gate_v1.py::test_project_knowledge_item_survives_scope_gate` / `test_gis_memory_item_survives_scope_gate` / `test_cross_tenant_items_still_denied`——修复不打开跨租户门） |

## P1

| # | 发现 | 处置 |
| --- | --- | --- |
| P1-1 | typed 路径对 user message 做了 secret 消毒（违背「identity 不消毒」契约）；且 `auth`/`token` 子串误报 `author=`/`tokens=` | `scrub_item` 跳过 USER_MESSAGE；key 段加 `(?<![A-Za-z0-9])…(?![A-Za-z0-9])` 独立段守卫（`api_key=`/`api-key:` 仍命中）；负例测试钉住 |
| P1-2 | kill-switch/typed 异常回退时 cartography 五块整体丢失（chat.py 已不预拼） | chat.py 两调用点：`typed_context_assembly_enabled()` 为 False 时恢复 `_build_cartography_turn_context` 预拼并按旧签名传入 bridge——回退路径保真 |
| P1-3 | reservation 在 plan→settle 间被取消/释放时 `complete()` 不记账，但 receipt 仍报 `ledger_recorded=True` | 以 `reservation.released` 为「已记账」信号；未记账时回落显式 `record_context_tokens`——actual 不丢也不双计 |
| P1-4 | wave-B 卡片 `budget_used` 误计整个 prompt（pre-F04 只计姊妹制图四块）→ 忙会话卡片被 `budget_skipped` | `prior_chars` 只累计 `INLINE_JOIN_DOMAINS`；测试钉住（env/plan 9000 字符不计入） |

## P2（处置）

- P2-1 docstring 与行为不符（legacy 块「被忽略」实为原样注入+抑制派生）→ 修正（注入一次，receipt 记 `cartography_derived=caller_injected_block`）。
- P2-2 USER_MESSAGE 32k cap 截断长消息 → factory 豁免 USER_MESSAGE（identity 不是 context），测试钉住。
- P2-3 ENVIRONMENT cap 4000 < situation 4096B + canvas 1600 → 提到 6000。
- P2-4 仅 3 池有 plan cap（PROJECT_CONTEXT/HARNESS_EVIDENCE 无）→ **接受现状**（closed-word budget 表的诚实语义；cap 是 `context_budget` 的单一真相，F04 不新增 fraction）；文档已注明。
- P2-5 phase-3 后 `pool_usage` 未回调、omission reason 时序 → 已修（决策时快照 + 池用量同步扣减）。
- P2-6 `max_total_context_chars` 死代码 → 已接线（`_apply_char_ceiling`：yield 序省略、untouchable 豁免、reason `omitted:total_char_cap`），测试钉住。
- P2-7 缺组装级负例 → 补：走私 marker 中和（message 经 typed 全链路）、provider-through-pipeline scope 测试（正是能拦住 P0-1 的那类）。
- P2-8 legacy_compat 多取 plan + 副作用 → 改轻量 fetch（map_state+mapspec，无 plan/slot 副作用）。
- P2-9 `caller:` 豁免无卫兵 → 加 `provider_id == "caller"` 双条件；EnvironmentProvider 以 `caller` 身份发射。
- P2-10 "byte-for-byte" 措辞 → 收敛为「良性内容、空会话边界字节等价；敌意 marker 形态双路径都中和，wrap-fence 是 typed 路径声明的加固」（flags docstring + design doc）。

## 复核结果

- Reviewer 独立复核（commit 8d73d9b6）：P0-1/P1-1..4/P2-9 逐项 runtime 验证通过（含 gate 级跨租户复现、released-flag 竞态分析、kill-switch 双路径接线核查）；**终审 GO**。
- 测试：新套件 65/65；复核方独立重跑 454 项全绿；邻域回归 341/341（pi_turn_context、cartography_injection、gis_situation、governor 全套、harness_kernel lifecycle、context assembler/budget/policy、gis_context、chat_api）。ruff 全绿。
- 残留 P2（不阻塞）：ceiling 省略不回调 receipt 的 planned/pool 计数（默认关）；`complete()` 内部 ledger.release 异常的自吞微边（需 governor 内部错误才触发）；env-flag 路由/桥读 TOCTOU（理论性）。`test_cross_tenant_items_still_denied` 已去空洞化（预构建外租户 item 直测 gate）；`token2=` 取舍已在 fence.py 注释。
