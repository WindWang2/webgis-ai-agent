# Typed Tool Surface V1 — Ledger（交付台账）

约定：每个 milestone 一节，四列（任务 → 文件 → 测试 → 证据），尾部验收对照与兼容声明。

## M0 — Phase 0 勘察与基线

| 任务 | 文件 | 测试 | 证据 |
| --- | --- | --- | --- |
| git fetch + 基线 | — | — | origin/master `580b33e9`；open PR 仅 #1270 |
| T0 Pi boundary facts | docs/dev/typed-tool-surface-recon.md §4 | probe 脚本输出 | 327/316/191KB/17,277B 实测 |
| 重叠矩阵 + before 链 | docs/dev/typed-tool-surface-recon.md §2/§3 | — | — |
| Subagent A 勘察 | docs/dev/recon-subagent-a.md | — | PR reviews/ADR/audit/in-flight 对账 |
| 决策日志 | docs/dev/typed-tool-surface-decisions.md | — | D1-D8 |
| 台账 | docs/dev/typed-tool-surface-ledger.md | — | 本文件 |

## M1 — per-turn 面字节预算 + 披露面（T2 残留 / ADR-0180 D2）

| 任务 | 文件 | 测试 | 证据 |
| --- | --- | --- | --- |
| apply_surface_byte_budget（贪心 + 前门恒保留 + 记因） | app/services/chat/pi_native_surface.py | tests/unit/test_pi_surface_budget.py（8） | 8 passed；预算裁剪仅尾部、0=off 恒等 |
| compute_turn_active_tools 集成 + 链发射披露字段 | 同上 | test_pi_surface_budget.py::integration | disclosure 带 surface_budget/budget_dropped |
| 默认 32KB 依据 | decisions D2 | probe | native7=17,277B、median=528B → 典型 turn 不变 |

## M2 — pre-dispatch strict validation + typed error（T4/T7 / ADR-0180 D3/D4）

| 任务 | 文件 | 测试 | 证据 |
| --- | --- | --- | --- |
| registry.args_model() 公开访问器 | app/tools/registry.py | parity 共享模型断言 | 闸与 dispatch 用同一 Pydantic 对象 |
| pi_input_gate 分层校验（归一化/unknown/required/结构错位/TypeAdapter 探针/无串升级档/oversized 旁路/fail-open） | app/services/chat/pi_input_gate.py | tests/unit/test_pi_input_gate.py（12） | 12 passed；ref 游标字符串零误拒 |
| bridge 接线（dedup/wave 之前）+ SCHEMA_VALIDATION_REJECTED details | app/agent_pi_bridge.py | test_pi_input_gate.py::dispatch_reject_path | issues+retryable 机器可读；不进 tool_failed/harness_failure |
| pi_surface_metrics 计数接线 | app/services/chat/pi_surface_metrics.py + bridge | test_pi_surface_metrics.py | reject/proxy/direct 计数面 |

## M3 — surface metrics + 回归门（T9 / ADR-0180 D5）

| 任务 | 文件 | 测试 | 证据 |
| --- | --- | --- | --- |
| /metrics/digest additive pi_surface 段 | app/api/routes/metrics.py | tests/unit/test_pi_surface_metrics.py + test_metrics_api.py（既有 2 passed） | admin 门不变；缺段不阻断 |
| golden 面质量门（必达/tier-3 零泄漏/预算内） | — | test_pi_surface_metrics.py::golden | 真实 registry 3 查询 |
| gate 有界时延（快速拒绝 ≠ wave 排队） | — | 同上 latency 2 例 | 2000 要素 args < 250ms；50×双路径 < 2s |

## M4 — parity 测试 + ADR + 文档（T8）

| 任务 | 文件 | 测试 | 证据 |
| --- | --- | --- | --- |
| 校验双向 parity（gate 拒⇒registry 拒；gate 漏 ⇒ registry 权威） | — | tests/unit/test_pi_surface_parity.py（6） | 真实 dispatch（session_id=""）对照 |
| 入口等价（裸名 vs proxy 内名同 (tool,args)）+ tier 双路不可达 + 单一 schema 真相 | — | 同上 | resolve 相等断言 + dump⊆args_model 键面 |
| ADR-0180 | docs/adr/0180-pi-typed-tool-surface-hardening.md | — | 编号对账：master 最高 0179、无在途占号 |
| tool-surface.md V1.5 小节 | docs/agent-runtime/tool-surface.md | — | 与实装一致 |

## 验收对照（DoD）

- [x] 从执行时最新 master 建立独立 worktree/branch（580b33e9 ✅；分支后 master 未动，merge 验证 up-to-date）
- [x] 最新 PR/review/issues/ADR/code 勘察完成并落 recon ✅
- [x] 没有重复实现最近已合并/在途 PR 已覆盖的功能 ✅（D1；重叠矩阵 recon §3）
- [x] 生产调用链真实接入（bridge `_dispatch_tool_bound` + per-turn `compute_turn_active_tools` 生产路径直改）
- [x] 关键契约可序列化、可测试、可观测（gate report JSON-safe；metrics snapshot；链发射披露字段）
- [x] fail-closed / fallback / rollback 行为明确（预算 0=off；闸 fail-open；registry 权威兜底；5-commit revert 面）
- [x] scoped tests 全绿（32 新 + 345 受影响面）
- [x] 跨模块回归已跑（dispatch/normalization/session-plan/bridge/auth/contract）
- [x] master 预存失败基线归因（#1270 记录的 8 failures 为 quality artifact staleness 域，零交集；唯一中途失败为本线契约冲突已修复）
- [x] 资源使用受控（无全量 build；focused tests 串行；无前端改动故跳过 next build）
- [ ] 独立 review 完成并修复 P0/P1（Subagent B 进行中）
- [x] 文档/ADR/ledger/生成物一致（ADR-0180 + tool-surface.md V1.5；无生成物触碰）
- [ ] 独立 PR 已创建（未 merge、未 auto-merge）

## 与并行线的兼容声明

- #1270（open）：文件面零交集；若合入先 rebase。`tests/conftest.py` 本线不改（其 CARTO_METRICS 环境钉是对方线）。
- 无其他在途分支触碰 Pi tools / tool surface（Subagent A 对账）。
