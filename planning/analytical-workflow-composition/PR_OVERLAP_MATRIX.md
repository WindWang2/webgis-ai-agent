# PR_OVERLAP_MATRIX — 去重矩阵

## 1. 已合并 PR 重叠面（基线 4b3629c90；本地 git 实测 changed-files）

| PR | 主题 | 与本任务共享面 | 处置 |
|---|---|---|---|
| #1867 g3dv2-c | 3D 科学分析→Harness→制图闭环 | tests/acceptance/gis_agent（5 文件）、gis_harness/scene3d、run_3d_analysis 编排先例 | 只继承模式，不改 scene3d；G05 corpus additive 新族 |
| #1762 G05 | 任务验收 lab | tests/acceptance/gis_agent/** | additive：新增 F 族场景 + 重 pin，不改编排 runner |
| #1761 G03 | 工具能力对账 | app/tools、app/lib/gis/algorithms | 新工具须过 conformance（登记 descriptor，走既有 CLI 门禁） |
| #1760 G02 | 科学契约/MethodUseContract | app/lib/gis/method_use_contract.py、science_final_gate | 只消费（build_method_use_contract），不改 schema |
| #1759 G01 | 数据集语义就绪 | dataset_semantics/role_binding | 只消费角色绑定 API |
| #1836/#1835 g3dv2-a/b | DEM/场景运行时 | 无文件交集 | 无 |
| f02/f05/f07/f08/f09/f12/f13 | IR/复用/目录/调度/replay/compiler | 全部已合并为地基 | 只消费 |

**结论：无在途/已合并 PR 与「2D 多步分析流程组装 + 选择性重算用户可达 + receipt 证据链」同一实质能力。g3d 系列（3D）已交付同构闭环，本方向是其 2D 等价增量，非重复。**

## 2. open PR / issue 复核流程（网络恢复后、PR 创建前必做）

1. `git fetch origin master --prune` → 若 master 前移：`git rebase origin/master` + 重跑受影响测试。
2. `gh pr list --state open` → 逐 PR `gh pr view <n> --json files` 与本分支 changed filenames 求交。
3. `gh issue list --state open` → 检查 #1842–#1866 审查 issues 是否覆盖本方向实质能力；若是 → 按任务书缩窄条款处理并记录。
4. 结果追加到本文件 §3。

## 3. 复核记录

- 2026-10-29 初始：fetch/gh 均 TLS 失败（见 BASELINE 网络注记）；本地 refs 已含基线。待补。
- **2026-10-29 深夜（网络恢复后第一次复核）**：
  - `git fetch origin master`：**master 未前移**（仍 4b3629c90）——分支无需 rebase。
  - `gh pr list --state open`：**0 个 open PR**——无在途冲突。
  - `gh issue list --state open`（#1842–#1866）：全部为审查类 issue（S1–S7 audit 汇总 + 6 High：gis_memory 租户隔离 ×3、仿真 durable 幂等、交通仿真串行、数据质量环截断）。**无一覆盖「多步分析流程组装」实质能力**；本任务不改 gis_memory/simulation/data_quality/交通仿真路径，按任务书隔离条款不越界清偿。
  - `gh auth status`：token 有效（网络恢复后 keyring 正常），PR 创建通道可用。
- **2026-10-30 PR 创建前第二次复核**：master 仍未前移（4b3629c90）；出现新 open PR **#1868**（sdo：多源空间数据自动准备与语义绑定工作台）。文件交集仅 `app/tools/__init__.py`（双方各 append 一行注册——机械合并）与 `.goal-loop-ledger.md`（append-only 分节）。语义上 #1868 是数据准备/绑定层（我的层的**上游供给**：产出可绑定的语义数据集），本分支是分析编排/执行层——互补不重叠，无缩窄需要。
