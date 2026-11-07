# PR 证据对账与交付

状态：独立二次复审通过；尚未 push，尚未创建 PR。

## 当前 Git 事实

- 最新 `origin/master`：`585812ee4e2d75612f2b124e6fe6587af3deca5c`（再次 fetch，未变化）。
- 当前 HEAD：`1960d1f61`（`docs(planning): record independent cold review fixes`）；代码修复 HEAD：`ce3706e65`。
- worktree：`/home/kevin/projects/webgis/webgis-wt-harness-reliability-lab-20261107`
- branch：`zcode/goal-harness-reliability-lab-20261107`
- PR URL：待真实创建后填写，不虚构。

## Commits（master 起 13 个）

1. `e3895ece4` feat(harness): add bounded end-to-end journey diagnostics
2. `8a9742eac` fix(recovery): preserve dispatch attribution in diagnostics
3. `6ff022f70` docs(planning): establish G07 reliability evidence ledger
4. `45394480f` fix(selection): disclose truncated feature inputs
5. `0e7dbae79` test(harness): add twelve real-dispatch golden task groups
6. `5f9698dbd` docs(planning): record G07 golden task evidence
7. `fa6e3def5` test(harness): harden journey fault terminal contracts
8. `bb86d5d08` docs(planning): record G07 fault matrix evidence
9. `85674cba6` test(recovery): add four controlled recovery experiments
10. `f13d1fa27` docs(planning): record G07 recovery experiments
11. `0aaca4811` perf(replay): bound resume rewrite cost and UTF-8 metrics
12. `6691e3fee` feat(harness): integrate journey metrics and reliability docs
13. `51eb10c68` docs(planning): record G07 performance and Pi evidence
14. `ce3706e65` fix(harness): close independent review evidence gaps
15. `1960d1f61` docs(planning): record independent cold review fixes

## Oracle 证据对账

| Oracle | 当前证据 | 状态 |
|---|---|---|
| O01 完成声明阶梯 | `journey.py` 八阶段与 8 声明；缺 render/artifact/context 均不可完成 | 本地通过 |
| O02 12 组双 Oracle | manifest + 30 真实 dispatch 场景；10 组双侧 + 2 focused | 本地通过，模式明确 |
| O03 关联无跨会话 | session/turn/goal/operation/correlation 检查与零回显 | 本地通过 |
| O04 故障终态 | 20 项跨边界 + 89 项既有 fault/lock/SSE | 本地通过 |
| O05 只读诊断 | owner scope、secret 负例、UTF-8 上限、CLI 子进程 | 本地通过 |
| O06 模式不混淆 | offline/live dispatch/Pi stub 有证据；browser mock/flaky；live not_run | 诚实披露 |
| O07 4 组恢复 | 缺观察、瞬态、跨城、持久编辑；恢复域 199 项 | 本地通过 |
| O08 压力资源有界 | 140 场景 wall/CPU/RSS/payload；events/artifact bytes/frames 未测项明示 | 本地通过（未测项不冒充） |
| O09 两个热点结论 | resume 1.77×；UTF-8 未提速/deepcopy/sha 不优化 | 原始 JSON |
| O10 科学不降级 | focused 255、科学/单位/范围既有回归；未修开放 issue | 本地通过 |
| O11 原 ratchet 不降级 | record-only，active baseline 不动，not_evaluated 非 pass | 本地通过 |
| O12 双跑+独立 Review | 全量 270 passed；B 冷审无 P0/P1、P2 全关闭；二次复审通过 | 已满足，最终双跑待完成 |
| O13 干净 worktree 复现 | 质量文档 §6 命令、分层车道、资源上限 | 已交付 |

## 本地验证统计

- 全量 Replay/Lab：`270 passed, 4 warnings, 107.24s`。
- 真实 dispatch：30/30 场景；CLI 报告 `reports/g07/golden.json`。
- focused 科学/scope：255 passed；恢复：199 passed；selection 全链：11 passed。
- journey 修复后相关：52 passed；perf 契约：7 passed。
- Pi doctor：全 PASS；显式真实 Pi + stub 旅程：1 passed, 32.17s。
- Mypy：Success, 38 files；Ruff/diff check 通过。
- 浏览器：首跑 7 passed/2 skipped；第二跑 J9 failed（既有 flaky，非稳定门）。

## 独立 Review

- B 冷审：P0/P1=0；P2 四项（inline artifact、float 参照、512B 边界、未测维度）。
- 修复 commit：`ce3706e65`；复审正在执行，结果待写 `REVIEW.md`。

## 在途接触点

- #2013 TDS 不同代码块，语义兼容；#2012 仅影响未来 Pi 验证环境；master 未变化，无需 rebase。
- 不等待或宣称线上 CI；不合并 PR。

## 未验证/风险

真实后端浏览器、真实 PostGIS 故障、跨进程 RPC 重连、live provider、真实模型、生产部署未验证。J9 浏览器 flaky 明示。#1999/#2001/#2003/#2005/#2006/#2007 保持 baseline debt，不宣称修复。
