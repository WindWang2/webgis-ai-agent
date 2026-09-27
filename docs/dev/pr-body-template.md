# PR Body 模板（goal-loop 统一格式）

> 2026-09-27 由 g18 沉淀。来源实践：f 波次的 PR-body 单独成文存档（金样例
> [f05-pr-body.md](f05-pr-body.md)；更早先例 ac-07 / ac-09 / ac-v11-m3-m5 各 `*-pr-body.md`）。
> 适用：goal-loop 产出的所有 PR（g 波次起使用）；`gh pr create --body-file` 与 docs/dev 存档共用同一份内容。

## 用法

1. 复制下方模板正文，逐节填写；不适用的节写「不适用：<原因>」，**不得删节或留空**。
2. PR 创建：`gh pr create --base master --title "<标题>" --body-file <PR_BODY.md>`；不等线上 CI（CLAUDE.md 约定）。
3. 如需存档：`cp <PR_BODY.md> docs/dev/<goal-id>-pr-body.md`，并在总账 `.goal-loop-ledger.md` 所属波次节补完结行。
4. 自校验：用文末「字段核对清单」逐项核对成文；核对结果随 PR body 提交。

## 模板正文

````markdown
# PR: <type>(<goal-id>): <一句话标题>（<ADR 编号，如有>）

## 来源（issue / 文档债 / 任务书）

- 目标来源：<issue 链接 ｜ goal 任务书 ｜ 文档债条目；写明依赖顺序与前置目标状态>
- Baseline：`origin/master @ <完整 sha>`（<日期>，执行时实测；与任务书快照比对结论：<无漂移/已前移的处理方式>）
- Dedup：<与 open PR、已合并工作的重叠核查结论；兄弟目标边界划分（谁管什么）>

## 根因与证据（Problem Evidence）

1. <证据点：`file:line` 级引用 + 现象 + 后果；缺失能力写「已建未接/零调用方」这类可验证事实>
2. <…>

## 改动清单（Architecture & Files）

- <设计要点逐条列出，每条单一事实；关键契约/invariant 成文；ADR 链接>
- 新增：<文件/模块列表>
- 修改：<文件列表，标注「一行追加」这类最小接触面>

## 验证命令与输出摘要（Test Evidence）

- 命令：`<逐条原样可复制的命令>` → <结果：N passed / 退出码 / 关键计数>
- 回归：<相邻面回归范围与结果；基线既有失败要附「干净 master 同口径复现」记录>
- 纪律：本地串行；无 pytest-cov 环境加 `--no-cov`；后端跳过 `-m "not perf and not cartography and not real_services"`；
  前端迭代期 `pnpm run test:ci` + `pnpm run typecheck`（build 仅终验一次）；不等线上 CI。
- 关键 Oracle 验证连续两遍一致：<两遍结果摘要>

## Review 意见与清偿（Independent Review）

- Reviewer：<subagent 标识/角色，独立于实现者>；结论：<ACCEPT / ACCEPT-AFTER-FIXES / FIX-FIRST>
- 计数：P0 ×<n> / P1 ×<n> / P2 ×<n> / P3 ×<n>
- 逐条清偿：`P1-1 <一句话> → <修复方式> + 覆盖测试/验证证据`
- 未清偿项：<如实列出 + 理由 + 归属>

## 风险与回滚（Compatibility & Rollback）

- 兼容性：<schema/API/DB/flag 层面结论；additive 与否>
- 回滚开关：<env 键或 flag → 回滚到的具体行为；无开关时写回滚方式（如 revert 单 commit）>
- 残余风险：<如实披露>

## Out of Scope

- <明确不做什么 + 归属（兄弟目标/后续方向），防 scope 漂移>
````

## 字段核对清单（自校验用）

成文后逐项核对，核对结果随 PR body 提交：

- [ ] 标题含 `<type>(<goal-id>)` 与 ADR 编号（如有）
- [ ] 来源：issue/文档债/任务书可追溯，baseline sha + 比对结论，Dedup 结论
- [ ] 根因与证据：≥1 条 `file:line` 级证据
- [ ] 改动清单：新增/修改文件齐全，关键契约成文
- [ ] 验证命令：可原样复制复跑，含输出摘要与两遍一致结论
- [ ] Review 意见与清偿：P0/P1 全清偿或有归属，逐条可追溯
- [ ] 风险与回滚：兼容性结论 + 回滚开关/方式
- [ ] Out of Scope：边界成文

## 字段与金样例（f05-pr-body.md）对照

| 模板字段 | f05 金样例对应节 |
|---|---|
| 来源（issue/文档债/任务书） | `## Baseline & Dedup`（baseline sha + 对 open/merged 工作的去重核查） |
| 根因与证据 | `## Problem Evidence`（4 条编号证据） |
| 改动清单 | `## Architecture` + `## Key Contracts` + `## Files` |
| 验证命令与输出摘要 | `## Test Evidence (local, serial, --no-cov)` |
| Review 意见与清偿 | `## Independent Review`（P1×3 逐条 → 修复 + 回归测试名） |
| 风险与回滚 | `## Compatibility`（schema/flag 兼容结论 + kill switches） |
| Out of Scope | `## Out of Scope`（逐条 + 归属说明） |
