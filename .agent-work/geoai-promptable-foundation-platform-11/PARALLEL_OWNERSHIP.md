# PARALLEL_OWNERSHIP — open PR / branch / worktree 文件级所有权（2026-09-15 启动）

## 1. Open PR（唯一：#1335）

- **PR #1335** `fix/harness-claim-mission-failclosed`（head `92145507`，MERGEABLE）
- Changed files：11 个（清单见 BASELINE.md §2；`app/services/gis_harness/**` 8 + `app/services/mission_runtime/{service,store}.py` 2 + 对应 tests 2）
- 与本 track primary scope（`app/lib/modelops/**`、`app/services/modelops/**`、`app/agent/geoai/**`、新增 frontend geoai 组件、相关 tests/docs）**文件交集 = ∅**。
- 策略：上述 11 文件对本 track **read-only**。本 track 不依赖其新 API（其改动是 harness fail-closed 加固，与本 track 正交）。若 #1335 在本 track 进行中合并 → 直接 rebase，无业务冲突预期。

## 2. Remote branches（prune 后）

仅 `origin/fix/harness-claim-mission-failclosed`（= #1335）与 `origin/master`。无其他并发远端 track。

## 3. 本地他人 worktree

- `codex/stale-code-cleanup`（`.worktrees/stale-code-cleanup`，未推送，78 files 2587 deletions——stale code 清理）。
  - 与 modelops **无交集**；但改动了 `frontend/components/map/map-panel.tsx`、`map-action-handler.tsx`、`app/services/gis_harness/delegation.py` 等。
  - 策略：本 track **不修改** `frontend/components/map/**`（UI 走全新独立 geoai 组件目录）；不改 `gis_harness`（归 #1335 与 codex 分支共同领域）。

## 4. Open issues dedupe

#1330–#1334 全部由 PR #1335 修复（PR body `Fixes #1330..#1334`）→ 本 track 不实施。无其他 open issue。

## 5. Rescope 后的 primary write scope（映射自 GOAL 模板）

| GOAL 模板（C++ 形态） | 本 repo 实际（已采纳） |
|---|---|
| `src/operators/runtime/**` | `app/services/modelops/**`（engine/service 层扩展） |
| `src/operators/rs/*model*` | `app/lib/modelops/**` + `app/lib/gis/capabilities/modelops.py`（append-only） |
| `src/agent/**model**` | `app/agent/geoai/**`（新，agent tools 面） |
| `models/**` | 无本地权重目录（platform 不捆绑权重——descriptor/catalog 由 registry + package_store 承担） |
| `data/agent/capabilities/**` | 不存在；等价物 `app/lib/gis/capabilities/`（append-only 注册） |
| `new src/app/geoai/**` | `frontend/components/geoai/**` + `frontend/app/**/geoai*`（新独立面板） |
| `tests/*model*` | `tests/unit/modelops/**`、`tests/integration/modelops/**`、`tests/unit/agent/geoai/**`、`tests/e2e`（如需） |
| `docs/models/**` | `docs/adr/0197-*.md` 起 + `docs/geoai/**` |

Read-only / 避免冲突：PR #1335 的 11 文件；`frontend/components/map/**`；`app/services/gis_harness/**`、`app/services/mission_runtime/**`；主检出分支 `fix/storymap-review-hardening` 的一切文件。

## 6. 共享 integration 文件的接线策略

- `CHANGELOG.md`：独立 integration commit，append-only。
- capability 注册（`app/lib/gis/capabilities/modelops.py` 及其 index）：**append-only** 新增 `model_*` 词汇或 geoai 能力，不动既有条目语义。
- `.gitignore`：不改。
- 前端路由/导航（若需挂载 geoai 面板）：优先 self-contained 新 route + 现有扩展 seam；对共享文件只做最小 append。

## 7. 动态规则（执行中持续生效）

- 每次 Phase commit 前 `git fetch origin` 并复查 open PR 集合；新出现的并发 PR 立即做文件交集检查并更新本文件。
- 若并发 PR 触及 modelops 业务主体：优先消费其 master seam、转互补能力；禁止同功能重写。
