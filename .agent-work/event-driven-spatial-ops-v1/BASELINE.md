# BASELINE — 执行时点快照

- 执行日期：2026-09-16
- `origin/master`：`faa453a8935101378c23eb6694a42c3616d9c670`（#1329 Hot-path Convergence，Direction 04）
- 本地 `master` == `origin/master`（clean），worktree `../webgis-wt-event-spatial-ops-v1` 从该 SHA 建分支
  `harness/event-driven-spatial-ops-v1`。

## 网络状态（实测）

`git fetch` / `gh api` / `curl https://github.com` / `curl https://pypi.org` 全部
`TLS unexpected eof`（含非沙箱模式）→ **GitHub 外网在执行窗口内不可达**。

- 本地 refs 已包含两个 open PR 分支（fetch 于网络可用期），merge-base 均为 baseline SHA，
  故并行施工矩阵基于本地分支 diff 建立（可信）。
- PR 创建/推送需网络恢复；各阶段边界重试；若最终不可达，在 PR body 与 ledger 中如实记录。

## Open PR 矩阵（本地分支 diff 实测）

### #1335 `fix/harness-claim-mission-failclosed`（修 #1330–#1334，4 commits）

触及文件（全量）：
- `app/services/gis_harness/completion/pipeline.py`
- `app/services/gis_harness/evidence_claim/{census,grounding,verify}.py`
- `app/services/gis_harness/hotpath_convergence/{claim_ingest,pi_card,session_ctx}.py`
- `app/services/mission_runtime/{service,store}.py`
- `tests/unit/gis_harness/test_evidence_claim_graph_v1.py`、`test_hotpath_convergence_v1.py`

→ **本分支禁止修改以上文件**（只允许调用其公开 API）。

### #1336 `zcode/geoai-promptable-foundation-platform-11`（95 files，+7963）

热区：`app/lib/modelops/*`、`app/services/modelops/*`、`app/tools/geoai_tools.py`、
`frontend/{app/geoai,components/geoai}`、`frontend/lib/i18n/messages.ts`、
`frontend/messages/*`、`docs/geoai/*`、`app/main.py`（+2 行）、`tests/conftest.py`（+4 行）、
`app/services/gis_harness/capability_graph.py`（4 行）。

→ 本分支会**加法式**触及 `app/main.py`（router 注册 + lifespan worker，不同区域）与
`tests/conftest.py`（`_ENV_BASELINE` 追加键，不同区域）；语义无冲突，PR body 披露。

## master 关键近况（供设计对齐）

- #1320 Durable Mission Runtime（ADR-0197）；#1327 SkillPolicy；#1328 Evidence/Claim/Provenance Graph；
  #1329 Hot-path Convergence（Mission×SkillPolicy×Evidence 已入 Pi/SessionPlan 默认链路）。
- 迁移序号：最新 `0091_gis_mission_runtime` → 本分支新增 `0092_*`。
- 默认 flag 现状：`GIS_MISSION_RUNTIME` ON、`GIS_MISSION_HOTPATH` OFF、`GIS_SKILL_POLICY` ON、
  `GIS_CLAIM_INGEST` ON。
