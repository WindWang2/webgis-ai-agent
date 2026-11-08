# PR 证据对账与交付

状态：**完成**。REL-O01–O13 本地证据完成，独立二次复审通过，关键验收连续两次通过；目标 branch 已推送，真实 PR 已创建，未合并。

## Git 与发布边界

- 实时 `origin/master`：`585812ee4e2d75612f2b124e6fe6587af3deca5c`（2026-11-07T17:54:14+08:00；2026-11-08 最终重取，merge 已最新，无 rebase）。
- 独立二次复审 HEAD：`8f2f1c873a020ef68e245e111ee527ec0cdd1245`；最终代码修复：`ce3706e65`。
- 最终代码及验收账本 HEAD、PR 创建时 head：`3958aec5d6323778300f84efc043d09086bb1ea6`。
- worktree：`/home/kevin/projects/webgis/webgis-wt-harness-reliability-lab-20261107`
- branch：`zcode/goal-harness-reliability-lab-20261107`
- **PR URL：https://github.com/WindWang2/webgis-ai-agent/pull/2017**（#2017，base master，非 draft）。本文件提交是最后的交付回写；其对象 ID 由提交后的 `git rev-parse HEAD` 和 PR 页面确认，不能在文件内自引用。
- 只 push 本 branch，不改 master、不合并、不等待或宣称 CI。
- #2013 接触 TDS 不同代码块，语义兼容；#2012 未改本分支 Pi doctor/旅程文件；#2011/#2015 无本分支文件重叠。#2014 的选择范围语义接触点见下。

## Commits（创建 PR 时 17 个）

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
16. `8f2f1c873` docs(planning): close independent second review
17. `3958aec5d` docs(planning): record final G07 acceptance evidence

最后的 `docs(planning): hand off G07 reliability PR` 回填上述真实 PR、工具限制与最终接触点。完整提交清单以 `git log origin/master..HEAD` 为准。

## Oracle 证据对账

| Oracle | 可复现证据 | 结论 |
|---|---|---|
| O01 完成声明阶梯 | `journey.py` 八阶段；缺 render/artifact/context 均不可完成 | 本地通过 |
| O02 12 组双 Oracle | manifest + 30 场景；10 组双侧 + 2 focused | 本地通过，模式明确 |
| O03 关联无跨会话 | 五类关联 ID 检查与零回显 | 本地通过 |
| O04 故障终态 | 20 项跨边界 + 89 项既有 fault/lock/SSE | 本地通过 |
| O05 只读诊断 | owner scope、secret 负例、UTF-8 上限、CLI 子进程 | 本地通过 |
| O06 模式不混淆 | offline/live dispatch/Pi stub；browser mock/flaky；live not_run | 诚实披露 |
| O07 4 组恢复 | 缺观察、瞬态、跨城、持久编辑；恢复域 199 项 | 本地通过 |
| O08 压力资源有界 | 140 场景 wall/CPU/RSS/payload；其余维度 not_evaluated | 本地通过，未测不冒充 |
| O09 两个热点结论 | resume 1.77×；UTF-8 未提速/deepcopy/sha 不优化 | 原始 JSON |
| O10 科学不降级 | focused 255、科学/单位/范围回归；未修开放 issue | 本地通过 |
| O11 原 ratchet 不降级 | record-only，active baseline 不动，not_evaluated 非 pass | 本地通过 |
| O12 双跑+独立 Review | 270 项连续两次通过；B 冷审 P0/P1=0、P2 全关闭 | 已满足 |
| O13 干净 worktree 复现 | 质量文档 §6、分层车道、资源上限、原始报告 | 已交付 |

## 最终本地验证

- Replay/Lab 连续两次：`270 passed, 4 warnings, 107.24s`；`270 passed, 4 warnings, 48.29s`。命令为 `PYTHONDONTWRITEBYTECODE=1 /home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python -m pytest tests/lab tests/harness_replay --no-cov -q -p no:cacheprovider`。
- 真实 dispatch：30/30 场景；报告 `reports/g07/golden.json`（本地产物，不入提交）。
- focused 科学/scope：255 passed；恢复：199 passed；selection 全链：11 passed；journey 修复相关：52 passed；最新 perf 契约：7 passed。
- Pi doctor：全 PASS；显式真实 Pi + stub 旅程：1 passed, 32.17s；不是付费或真实模型质量验证。
- Mypy：Success, 38 files；Ruff/`git diff --check` 通过。
- benchmark：resume before p50 468.510ms / p95 497.973ms，after p50 265.073ms / p95 304.255ms（1.77×，140/140 green）。UTF-8 计量 str 0.154ms vs canonical_json 0.262ms，不宣称提速。原始汇总 `docs/quality/evidence/harness_reliability_bench_report.json`。
- 浏览器：首跑 7 passed/2 skipped；第二跑 6 passed/1 failed/2 skipped，J9 mounted 15s 超时；API mock，非生产后端门。

## 独立 Review

B 冷审 P0/P1=0；P2 四项为 inline artifact、float 独立参照、512B 边界、未测维度。`ce3706e65` 全部修复；二次复审通过，独立复跑 83 项全绿；复审后的 270 项全量复跑满足附带条件。详见 `REVIEW.md`。

## 兼容与未验证风险

- additive 投影，无第二 trace store/planner/executor/registry；旧 ratchet/active baseline 和权限/确认语义不动；`flush_interval=1` 保留旧行为，默认 16 有收尾强制 flush。
- 选择输入增加 omitted 元数据后，贴边样例由 500B 增至 525B 会 fail-closed 拒绝；伪造计数被剥离，ownership 优先。已在 `DECISIONS.md` 披露。
- 真实后端浏览器、真实 PostGIS 故障、跨进程 RPC 重连、live provider、真实模型、生产部署和线上 CI 未验证；artifact bytes、生产 SSE events、渲染 frames、Redis mutation scaling 为 `not_evaluated`。
- #1999/#2001/#2003/#2005/#2006/#2007 保持 baseline debt，不宣称修复；J9 flaky 明示。
