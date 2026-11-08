# REVIEW_R2.md — 独立冷审报告（Review-2）

> 审查者：全新 subagent（未参与本分支任何实现/审计），只读审查 + 亲自实测。
> 审查范围：origin/master 585812ee4...HEAD（Fix-1 的 7 commits）。
> 本文件为 R2 报告原文存档；Fix-2（commits 4b12c8359 + 1b8077691）已按其建议闭环 R2-N1/N2/N3/N4/N6，R2-N5 记录于 FINDINGS.md。

## 结论摘要

**无 P0/P1 阻断。** 7 项修复全部判定"修复有效"；缺陷均在 master 真实存在（TESTFLAKY-1 的 master flaky 在 /tmp 的 master 快照上独立复现：3 轮 -n4 第 2 轮 1 failed）。新发现 6 项：1×P2（R2-N1，C-1 新代码继承既有 SSE 路径的 stale-sync 竞态，预存在模式、新调用点扩大暴露面）+ 5×P3。无 PROTECTED 路径违规。

## 逐项审查（摘要，全文见审查过程）

1. **C-1 0c74f572d → 修复有效**。master 缺陷属实（handleRestoreStyle 只 reload；restore 响应带 mutation_revision 而前端游标只经 mutation/SSE 推进 → 必 409）。pendingRemoved 不复活（syncSpecLayersToStore:288/346 跳过与保留语义）；会话守卫完整（fetch 前后双复核，第二次复核后无 await）；失败路径诚实。实测 vitest 88 passed ×2。
2. **A-1 88cea9daf → 修复有效**。to_crs 方向正确（4269×4326 实测 1 pair）；属性逐位保真（nested/None/大整数/全精度 float）；未声明+未声明零改动（passthrough 实测）；50k features 1.24s/208MB 预算内；federation 其余调用方不受影响（104 用例全绿）。
3. **B-5 c2707b4dc → 修复有效**。8 分支鉴权矩阵全实测：认证他者→session 文件 403 无误放行；匿名+token→user-owned 401 无绕过；错误 token 与不存在 session 响应无差异（R2-N3 为 detail 文本级弱差异，已修）。
4. **TESTFLAKY-1 b450012c4 → 修复有效**。master flaky 独立复现（/tmp/r2_master_tree -n4 ×3：6P/1F/6P）；修复后两文件合跑 -n4 ×2 18 passed。
5. **A-2 74b1f9ac8 → 修复有效**。守卫仅 graph≠None∧dataset=None 触发，引擎路径成对传入不触；错误信息准确。残余 R2-N4（both-None）已并入修复。
6. **B-4 42621250a → 修复有效**。5s 有界等待可接受（清理路径、超时不 cancel、token 幂等无双释放）；_track_release 对 concurrent Future 正确；dead-loop 诚实降级。残余 R2-N6（coro 未 close）已修。
7. **B-1 7fc214cac → 修复有效**。守卫实测：锁外计算窗口内 delete_ref → 不复活 descriptor/数据行；旧覆写计算失败不误删新覆写 descriptor；rev_after 捕获严密（identical-object 短路在 bump 前 return 不破坏不变量）。RedisSessionStore 无此问题（B-1 仅 Memory 侧成立）。

## 新发现问题（Fix-2 处置）

| ID | 级别 | 摘要 | 处置 |
|----|------|------|------|
| R2-N1 | P2 | stale map-state/SSE spec 被旧代次拒绝后仍无条件镜像/修剪 HUD（剪掉新行/复活旧层，面板漂移） | ✅ Fix-2 4b12c8359（refresh+SSE 双路径 commit-accept 守卫） |
| R2-N2 | P3 | refresh 游标不取 fetch 到的更高 revision → 一次 spurious 409 | ✅ Fix-2 4b12c8359（Math.max） |
| R2-N3 | P3 | 诊断端点 404 detail 文本差异构成弱存在性 oracle | ✅ Fix-2 1b8077691（统一 detail） |
| R2-N4 | P3 | service_area 双 None 仍 AttributeError（master 预存在） | ✅ Fix-2 1b8077691（并入类型化拒绝） |
| R2-N5 | P3 | A-1 声明 CRS 路径 to_json 对非 JSON 原生属性（如 datetime）TypeError；feature id 重编号（无功能影响——join 只消费 properties/geometry） | 📝 FINDINGS.md 记录（JSON 来源 features 不可能携带 datetime，未发现生产者） |
| R2-N6 | P3 | B-4 dead-loop 分支 coro 未 close → never-awaited RuntimeWarning | ✅ Fix-2 1b8077691（coro.close） |

## PROTECTED_PATHS 违规检查

`git diff origin/master..HEAD --name-only`（15 文件）逐条对照清单 —— 零命中。map.py（保护的是 chat/upload/health）、geocompute/ops.py（保护的是 gis_harness/*）、sidebar（保护的是 components/map/）、lib/mapspec（保护的是 interaction/map-kit/map-operations）均不在占有面。pyproject/.goal-loop-ledger/vendor 未触碰。**结论：无违规。**

## 测试实测记录（审查者亲跑）

| 命令 | 结果 |
|------|------|
| 后端 targeted 8 文件 -n4 | 104 passed |
| vitest lib/mapspec + 组件 | 88 passed ×2 |
| tsc --noEmit / ruff 5 文件 | exit 0 / All checks passed |
| master 快照 flaky 复现 -n4 ×3 | 6P / 1F / 6P |
| 修复分支 export 两文件 -n4 ×2 | 18 passed ×2 |
| B-5 鉴权矩阵 8 case / A-1 探针（方向/保真/真阴/性能）/ B-1 守卫探针 | ALL OK |
