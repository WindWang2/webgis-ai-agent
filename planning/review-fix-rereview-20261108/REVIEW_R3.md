# REVIEW_R3.md — 独立终审报告（Review-3）

> 审查者：全新 subagent（未参与实现、未参与 R1/R2），只读审查 + 亲自实测。
> 范围：origin/master 585812ee4...HEAD（9 commits，17 个变更文件，全量 diff 逐文件读完 + 关键支撑源码亲读）。

## 终审结论

**不存在未解决的可复现 P0/P1；本 PR 可交付（PASS with notes）。** Fix-2 两 commit 正确闭环 R2 全部 5 项移交发现；全量累积 diff 无互相冲突/重复逻辑；PROTECTED 零违规；后端 115 passed、前端 136 passed、tsc/ruff 全绿（均亲跑）。

## Fix-2 闭环验证（全部 ✅）

| 项 | 判定 | 证据摘要 |
|----|------|----------|
| R2-N1 commit-accepted 才 sync | ✅ | SSE（use-sse-stream.ts:625-636）与 refresh（user-mutation.ts:155-161）双路径守卫；无误伤——合法全新 spec（revision ≥ 游标）经 session-cursor.ts:159 仅拒绝 `< curRevision()`，等于/高于都提交并镜像；无 revision 事件走无条件提交路径返回 true 正常镜像；形状无效与 syncSpecLayersToStore 自身早退行为等价 |
| R2-N2 Math.max | ✅ | setMapSpecRevision 内建单调保护（session-cursor.ts:140-142），不可能回退 |
| R2-N3 统一 404 | ✅ | detail 与 sidecar 缺席逐字一致（map.py:481 vs :458）；except 只捕 HTTPException，DB 异常仍 500 传播；合法 owner 仅在 token 失效/会话被 GC 时见此 404（fail-closed 本就正确） |
| R2-N4 both-None 守卫 | ✅ | graph≠None∧dataset=None 分支与 Fix-1 逐字相同；both-None 新增 message B；graph=None∧dataset≠None 不触 |
| R2-N6 coro.close | ✅ | 三分支互斥，else 分支的 coro 必为"已创建未递交"，close 安全；fut.result 超时不 close 已递交 coro（正确） |

## 全量 diff 终审

P0/P1：无。9 commits 合并无冲突/重复（service_area 两守卫逐字合并、map.py 404 叠加、lock coro 卫生叠加、C-1 与 Fix-2 同函数同向演进）。B-1 迟到守卫（对象身份+revision 双检、锁内原子读）、B-4 _track_release 对 concurrent Future、_bound_loop 仅 redis acquire 成功记录——复核无误。

**R3 新发现（P3×2 + 观察×2，均不阻断）**：
1. ~~A-1 空输入行为收紧~~ —— **主 Agent 整合裁决：驳回（refuted）**。R3 探针对照的 `spatial_join_local([], right)` 是 federation 函数直调，非算子路径。主 Agent 在 /tmp/master-probe（origin/master 快照）与本分支分别实测：算子路径对空 features 输入**双方均抛同款** `NodeExecutionError("SPATIAL_JOIN node requires two feature inputs (left, right)")`——本分支保留了 master 的非空前置检查（空列表 falsy → 计数 <2 → raise），行为与消息逐字一致，零行为变化。
2. B-4 理论关停竞态：`is_running()` 检查与 `run_coroutine_threadsafe` 递交之间 bound loop 恰好关闭的极窄窗口 → never-awaited warning。关停路径、窗口极窄、TTL 兜底 —— 记 FINDINGS 供后续小修。
3. 观察：map.py:462 owner-is-None 404 detail 与统一 detail 不同（master 预存在、对任何请求者恒定，不构成 oracle）；匿名+token→user-owned 401 与 download 同款分层（需 48-bit uuid 文件名，R2 已实测接受）。

## PROTECTED 违规终检

17 个变更文件逐条对照 PROTECTED_PATHS.md —— **零命中**。语义级条款（#2012 SSE 幂等/TurnPhase）已核：use-sse-stream.ts 改动仅位于 mapspec commit/HUD 镜像段（line 621-637），不触碰 per-turn 去重与 TurnPhase。pyproject/.goal-loop-ledger/vendor/package.json/pnpm-lock 未触碰。**结论：无违规。**

## 测试实测记录

| 命令 | 退出码 | 输出 |
|------|--------|------|
| 后端 9 文件 -n4 | 0 | 115 passed in 9.36s |
| vitest（mapspec + use-sse-stream + 组件） | 0 | 15 files / 136 passed |
| tsc --noEmit / ruff 7 文件 | 0 / 0 | 零错误 / All checks passed |
| 主 Agent 补充实测（R3-P3-1 裁决） | — | master 快照与分支算子路径对空 features 输出逐字一致（均 NodeExecutionError 同款消息） |

## 交付判定

**PASS with notes** —— 无未解决的可复现 P0/P1；notes（P3-2 关停竞态、两条观察）记 FINDINGS 供后续，不构成交付障碍。
