# LOOP_LEDGER — G14 iteration log

## Round 1 (2026-11-10)
- Baseline research (2 Explore agents), planning docs, backend core (commits 42d830dbb / be83b589a / 425161301), frontend (commit 1fb16f376).

## Verified state after Round 1
- Backend: tests/unit/feature_edit 115 passed (core 66 + structural 25 + transactions 19 + flagship 2 + g14 tools 6 — 计数含修复的 SW-02 陈旧断言)；interaction_ir 邻域 126 passed。
- Frontend: vitest 5042 passed / 2 files pre-existing failures（pdf-dependency 15、changed-lanes j13 —— 均在干净 origin/master 临时 worktree 复现确认）；eslint --max-warnings 0 全绿；tsc（含 test tsconfig）全绿。
- api_contract test_field_contract：pre-existing master drift（/health +agent_runtime_*、/export/vector-pdf +constraint_layout 来自已合并 #2036/#2015，快照未刷新）——在纯净 origin/master worktree 复现；本分支未新增漂移（新端点返回 Dict[str,Any] 无字段签名）。

## Perf evidence (script /tmp/g14_perf.py, memory store, single worker)
- 200-target set_fields: preview 7.2ms / commit 273.7ms
- 50×64-vertex create: preview 20.7ms / commit 80.5ms / journal entry 92KB（<256KB entry 闸；>64KB 滚动预算 → 旧条目 FIFO 丢弃并披露 dropped=1）
- undo/redo 链（20 点 ref）: 22.6 / 24.0 / 79.9ms
- 50×1024-vertex create 超预算拒绝: 174ms，entry_too_large（诚实拒绝非截断）

## 事故记录
- 一次 `git stash` 在共享 stash 栈上弹出了其他分支的历史 stash 导致工作树污染 —— `git reset --hard HEAD` 已恢复（4 个提交无损，stash@{0} 完整保留）；此后基线验证一律使用临时 detached worktree。

## Remaining
- REVIEW.md（独立评审输入）、PR_HANDOFF.md、push + PR。
