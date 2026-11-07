# PLAN — G05-SITUATED-WORKBENCH

> 基准：origin/master `585812ee4`（2026-11-07 实测）；worktree
> `../webgis-wt-situated-gis-workbench-20261107`；branch
> `zcode/goal-situated-gis-workbench-20261107`。
> 方向：地图—对话情境一致性与可解释闭环 —— 只做 #1974 之后的增量。

## 使命

消除"用户看到的联动"与"模型收到的事实"之间的断裂：选择范围保真披露
（#2004 族）、Agent 操作可撤销面扩展、终验四层诚实分层、面板实时化、
图表键盘可达、金标回放钉扎。

## 审计结论 → 增量选择（详见 BASELINE.md §3/§8）

- P0：#2004 族（双侧 8 截断 + 白名单丢披露 + 无 id 零上行 + 死标志 +
  chart categories 截 8 + bbox 谓词不进 IR）→ **全清偿**。
- P1：SW-04 undo 覆盖面 / SW-09 display auto 静默 → **清偿**。
- P2：SW-05 面板实时 / SW-08 chart 键盘 → **清偿**。
- 明确不做（记录于 ADR-0287 Consequences）：display 默认模式翻转（部署
  级决策）、视口史代、服务端 map-op redo、SW-01 面板大改版。

## 轮次执行记录

| 轮 | 内容 | commit | 验证 |
|---|---|---|---|
| M0 | 实时审计（子代理 A 10 节 file:line + 主 agent 核验）+ BASELINE + 冲突矩阵 | — | gh/git 实测；#2011/#2012 零交叠 |
| M1 | SW-02 核心：失败测试先行（11 红）→ 双侧契约修复 → 复验 | 7be12d224 | IR+situation 3464 绿 |
| M1+ | SW-02 残余：categories 20 对齐 + bbox 谓词进 IR | fe149e00a | 163 绿 + 前端 33 绿 |
| M3 | SW-04：invertible 扩展 + styleCommand + 录制扩展 | 6cc0c248c | 后端 68 + 前端 74 |
| M3 | SW-09：FinalAcceptanceLine 终验收口披露 | af6bdbe2e | 8 绿（含 3 新） |
| M3 | SW-05：AgentRunPanel 事件驱动刷新 | af554bf9a | 13 绿（含 2 新） |
| M3 | SW-08：chart 键盘选择器同链 | 3c7d805c6 | 10 绿（含 1 新） |
| M2/M6 | ADR-0287 + 4 回放金标语料 + 三契约文档 | （本 commit） | replay 29 绿 |

## Oracle 表（验收时逐项打勾）

| Oracle | 证据 |
|---|---|
| 选 20 → Agent 见 20（或 50+披露） | test_selection_range_disclosure 全链 + box_select_20_range_faithful 金标 |
| matched_count 三面同源 | normalize→projection→ClaimedScope/ValidatedScope 透传测试 |
| 截断服务端派生、客户端不可信 | test_server_rewrites_false_truncation_claim |
| 切会话/取消不回灌 | 既有 epoch/水印/守卫测试回归 + session_switch_fresh_instance 金标 |
| 终验四层分层、auto 不冒充 | FinalAcceptanceLine 3 测试 + receipt 状态词表测试 |
| 破坏性操作 owner 门 | 既有 confirmation_required 测试回归 |
| 双闸无漂移 | manifest bounds/kind_specs 测试（pytest+vitest 两侧） |
| 本地全绿 + 独立 review | 见 TEST_MATRIX/REVIEW |
