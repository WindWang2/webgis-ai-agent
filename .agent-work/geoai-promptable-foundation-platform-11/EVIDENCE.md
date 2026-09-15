# EVIDENCE — 验证与审计证据账本（倒序追加）

## 2026-09-15 · Phase 0 启动审计

- `git fetch origin --prune` → OK；`origin/master` = `faa453a8935101378c23eb6694a42c3616d9c670`（GOAL 快照 `ebcafb4d` 过时）。
- `gh pr list --state open` → 仅 #1335（清单见 BASELINE §2）。#991/#992 不存在。
- `gh issue list` → #1330–#1334，全部归 #1335（dedupe 记录在 PARALLEL_OWNERSHIP §4）。
- `gh pr view 1335` / `gh pr diff 1335 --name-only` → 11 文件 read-only 集。
- ISSUES.md / docs/agents/goal-template.md → master 上不存在（absent，记录）。
- `git check-ignore -v .agent-work/geoai-promptable-foundation-platform-11/GOAL.md` → exit 1（可跟踪）。
- `git worktree add ../exp-rs-geoai-promptable-foundation-platform-11 -b zcode/geoai-promptable-foundation-platform-11 origin/master` → HEAD=faa453a8。
- codex/stale-code-cleanup（本地）与 modelops 交集 = ∅；触碰 `frontend/components/map/**` → 本 track UI 避开。
- 环境自检：Python 3.13.9 / pytest 8.4.2 / numpy 2.4.4 / pydantic 2.12.4（主 repo .venv）。
- Skills：goal-loop 已加载（启动时）。其余按相位加载（DECISIONS D-006）。

## OUT_OF_SCOPE 登记

-（暂无；执行中发现即登记于此并在 PR_BODY 顶部标注 P0 项。）
