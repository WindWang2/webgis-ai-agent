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

## M1 — per-turn 面字节预算 + 披露面（T2 残留）

（待交付后回填）

## M2 — pre-dispatch strict validation + typed error（T4/T7）

（待交付后回填）

## M3 — surface metrics + 回归门（T9）

（待交付后回填）

## M4 — parity 测试 + ADR-0180 + 文档（T8）

（待交付后回填）

## 验收对照（DoD）

- [ ] 从执行时最新 master 建立独立 worktree/branch（580b33e9 ✅）
- [ ] 最新 PR/review/issues/ADR/code 勘察完成并落 recon ✅
- [ ] 没有重复实现最近已合并/在途 PR 已覆盖的功能 ✅（D1）
- [ ] 生产调用链真实接入（进行中）
- [ ] 关键契约可序列化、可测试、可观测（进行中）
- [ ] fail-closed / fallback / rollback 行为明确（D2/D3：kill-switch + registry 权威兜底）
- [ ] scoped tests 全绿
- [ ] 跨模块回归已跑
- [ ] master 预存失败基线归因
- [ ] 资源使用受控
- [ ] 独立 review 完成并修复 P0/P1
- [ ] 文档/ADR/ledger/生成物一致
- [ ] 独立 PR 已创建（未 merge、未 auto-merge）

## 与并行线的兼容声明

- #1270（open）：文件面零交集；若合入先 rebase。`tests/conftest.py` 本线不改（其 CARTO_METRICS 环境钉是对方线）。
- 无其他在途分支触碰 Pi tools / tool surface（Subagent A 对账）。
