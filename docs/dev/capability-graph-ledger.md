# Capability Graph V1 — Ledger

每个 milestone 一节：目标 / 改动 / 契约 / 测试 / 证据 / 兼容 / 回滚 / 未解。

## M0 — Phase 0 勘察（2026-09-13）

- 目标：执行时基线核验 + 防重复对账。
- 改动：docs/dev/capability-graph-{recon,decisions,ledger}.md（本文件）。
- 关键事实：基线 580b33e9；V8 图存在但 qualification/candidates 生产零调用；
  四段（recipe/template/component/adapter）未投影；六关系未发射；四类图验证缺失；
  ADR 最高 0179，tts 线拟占 0180 → 本任务 0181。
- 测试：无（勘察阶段）。
- 回滚：纯文档。
- 未解：planner.py / candidate_planner_v8.py 精读在 M1 完成后补充。

（后续 milestone 追加）
