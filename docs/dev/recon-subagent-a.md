# Recon Subagent A — 报告存档（PR/ADR/audit 对账）

> 由侦察 subagent 产出、主 agent 存档（subagent 为只读）。日期 2026-09-13。

## 1. PR review 意见（5 个 PR，inline review 评论为零——反馈在 issue comments + body）

- **#1272（ads-v1，ADR-0170-0179）**：owner 巡检发现 3 项可行动：(a) `tests/unit/test_data_fabric_fallback.py` ruff F401×2；(b) `app/services/data_fabric/adapters/local_file_adapter.py` bandit B608×3——`# noqa: S608` 无效，bandit 只认 `# nosec B608`；(c) DB Migration Gate 模型/迁移漂移需 Alembic。另 4 项 Backend 失败为 master 预存 artifact-staleness（见 #1270）。
- **#1271（ac-v11，ADR-0160/0161）**：2 个 blocker：(a) `frontend/lib/map-kit/pdf-vector.ts` TS7016（svg2pdf ESM 子路径声明，已修）；(b) PostGIS 迁移门方言混用（sqlite_master 跑在 Postgres 引擎上→按 `engine.dialect.name` 分支已修）。另有 workflow 容器 `POSTGRES_USER` flake。
- **#1269/#1266**：无 review 意见，仅 owner 本地门证据评论。
- **#1150（ADR-0103 本体）**：不变式"ToolRegistry 是唯一执行真相；每个新面都是投影"。231 工具 descriptor 富化 + 覆盖门 `scripts/check_tool_descriptor_coverage.py --gate`。spawn dump v2 + per-turn WEBGIS_ACTIVE_TOOLS → setActiveTools（下回合生效）、marker 伪造中和、48 上限、kill switch、裸名直派 ToolDispatchService。自审 3 个 major（marker 伪造/router 覆写 legacy 采样/state_epoch 未接线）已在 `6fa7902` 修复。

## 2. ADR 清单

- master 最高：**0179**；无在途占号；**下一空闲：0180**。历史有重号（0088×3、0101×4、0104×4、0118×5）。
- 工具/Pi 相关：0005（tiered ToolCatalog）、0006（统一 dispatch，Pi 路径曾丢 ref_id）、0043（执行策略 seam）、0045（singleflight 缓存）、0068（一切程序化执行必须过 ToolDispatchService；入口差异=显式参数，绝无短路径）、0077（wrap 官方 Pi，不 fork——被 0103 实践超越）、0100（取消 seam）、0101（seam 成文：registry 单一真相 + tier-3 confirm ContextVar chokepoint）、**0103（最近先例）**、0104/0105/0128（扩展平台 V1-V3）、0134（V7 状态机）、0137（V8 capability_graph 只读派生图，"no N+1th source of truth"）。
- 最新 pi-host-seams 文档：`docs/research/pi-host-seams.md`（注意：其"Pi 只见一个无类型 proxy"的表述**早于 #1150**，以实装 index.mjs 为准——全超集已注册、schema 真实下发）。

## 3. 审计发现（.audit/，2026-08-27，早于 #1150）

- AH-P1-1 cartography runtime 驻留 bridge → 已解决（cartography_runtime.py）。
- AH-P1-2 services→routes import 边（agent_pi_bridge 引 pi_tools.get_bridge_secret）→ 仍开放，非本线范围。
- AH-P2-5 finalize_display 契约只在 legacy SYSTEM_PROMPT → 部分解决（扩展 promptGuidelines 有 cartography_status 与 anti-wrap 规则，无 finalize_display 专项）。
- AH-P2-8 registry 3 工具名硬编码 ref_cursor 特例 → 未验证，与 typed parity 相关。
- AH-P3-1 index.ts 死副本仍在（#694 已知）。
- 工具参数注入/schema 漂移方向无其他未决 finding。

## 4. docs/dev 约定

行内模式：`<line>-recon.md`（现状规则矩阵 + 基线指标表）→ `-decisions.md` → `-plan.md` → 分波 `-<wave>-ledger.md`（任务→文件→测试→证据四列 + 验收 checklist + §8.1 式并行线兼容声明）→ `-review-notes.md` / pr-body。

## 5. 在途冲突

- 唯一 open PR #1270：mapspec coordinator / quality manifests / conftest env pin / compiler.ts——与本线零交集（conftest.py 本线不碰即可）。
- 近期分支均为 ac/ads/fix 线；**无并行 Pi tools 车道**。
