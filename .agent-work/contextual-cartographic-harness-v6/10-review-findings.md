# 10 — Review Findings（Review Round 1/2 记录）

> 开发主体完成后填写。Lens A：GIS Harness architecture / workflow correctness / state machine / partial recompute / artifact lineage。Lens B：cartography / render observation / visual quality / frontend-runtime / UX。
> 每条：severity（BLOCKER/CRITICAL/MAJOR/MINOR）+ 位置 + 描述 + 处置 + 验证。

## Round 1

（待填）

## Round 2（performance/concurrency/security/tenant isolation/memory/context explosion/visual privacy/retry loops/resume correctness/backward compatibility/跨系统 seam）

（待填）

## Claim Honesty（§54）

（待填：凡未完全实现的能力必须标 planned/degraded/limitation，不得宣传 native）

## 开发期已发现并已处理

| 日期 | 严重度 | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| 2026-09-09 | 环境 | `~/.kimi-code/config.toml`（仓库外） | subagent 通道 opencode-zen 缺 x-opencode-session 头被 400 拒；muse-spark 不支持 effort=max | 补 custom_headers + support_efforts/default_effort + secondary_model.default_effort=high（已验证可用） |
