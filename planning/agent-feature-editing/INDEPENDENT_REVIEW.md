# INDEPENDENT_REVIEW（占位，R1/R2 两轮填充）

Reviewer 不得参与实现；从 origin/master...HEAD 全量重读差异。
格式：发现编号 / 严重性 P0-P3 / 证据 diff / 清偿提交 / 回归结果。

# R1（2026-10-29，独立 subagent，未参与实现）

范围：`git diff origin/master...HEAD` 全量（65 文件）。结论：**P0=0 / P1=3 / P2=10 / P3=10**。

## 发现清单与清偿映射

| # | 严重性 | 摘要 | 清偿 |
|---|---|---|---|
| P1-1 | P1 | REST 预览 snap 预取读旧字段名 target_ref → snap 预览恒被拒 | 路由统一走 service.build_and_store_plan（单一编排）+ REST snap 回归钉 |
| P1-2 | P1 | null 赋值 apply 删键 → undo 链断言永久卡死该 entry | presence 显式化（before_absent/after_absent；null≠删键）+ undo 往返钉 |
| P1-3 | P1 | Agent 自动提交与 undo/redo 后地图不刷新（dataset_updated 零消费者） | commit step_result 决策分支 + refreshDatasetLayer 四通道复用 + MVT 分支 |
| P2-1 | P2 | _process_lock 整表 clear 可清掉在持锁 | 只回收未持有 |
| P2-2 | P2 | journal 单条可突破字节预算（几何全量内嵌） | MAX_GEOMETRY_TARGETS=10 × MAX_GEOMETRY_POSITIONS=1024 + fail-closed 钉 |
| P2-3 | P2 | plans store 无锁 RMW 互相覆盖 | 进程内串行（unlocked 变体防重入死锁） |
| P2-4 | P2 | rename 计入 safe → policy 放行下可无确认改全库 schema | safe 仅限 set_fields |
| P2-5 | P2 | profile 不随 rename 重算 → 顺序语义失真/静默重建旧列 | schema_op 后重算 profile + 旧名 set 误拒钉 |
| P2-6 | P2 | 非 dict op 崩预取段；路由 snapshot 无 try | isinstance 守卫 + 路由走 service（try 守卫内建） |
| P2-7 | P2 | overwrite 成功但 journal 未落的崩溃窗口 | 落账返回检查 + ERROR 披露 + ADR 记恢复路径 |
| P2-8 | P2 | refreshDatasetLayer 把 MVT 层 source 覆写为内联 GeoJSON | MVT 分支只推 descriptor revision |
| P2-9 | P2 | 对已 applied 的 plan 点拒绝谎报"数据未改动" | reject 按服务端响应设终态 |
| P2-10 | P2 | 自定义 Idempotency-Key 跨 plan 重放他人 receipt | journal 键强制 plan 命名空间 |
| P3-1..10 | P3 | Union 缺项/stamp skipped 语义/409 code 信封/文档字段名/materialize kind/借码/TTL 与 applied 顺序/no-op 入账/部分物化/恢复验证 | 全部清偿（P3-8 no-op 短路 + P3-9 全量物化校验 + P3-10 mutation 不剥离键测试） |

E2E journey 自身抓出并修复 P1×1（同要素多 delta 覆盖 bug，早于 R1）。

# R2（2026-10-29，独立 subagent，清偿复核）

范围：origin/master...HEAD 全量重读 + 清偿提交实证复跑。结论：**P0=0 / P1=0 / P2=3 / P3=9**；R1 的 P1×3 全部干净清偿（带回归钉），P2×10 中七项干净、三项部分清偿/组合缺口。

## R2 发现与清偿

| # | 摘要 | 清偿 |
|---|---|---|
| R2-P2-1 | MVT 层编辑后刷新断链（descriptor revision 推进了但 _tileUrl 不重建 → 组合 spec 零变化，地图持续旧瓦片） | MVT 分支同时写 `_tileUrl: buildMvtTileUrl(refId, sid, revision)` |
| R2-P2-2 | _edit_lock 锁序反序（REST 先进程后分布式 vs turn 先分布式后进程）→ turn 中点 approve 冻结在跑 turn 至多 30s | 加锁顺序恒「分布式 → 进程」；透传分支仅进程锁 |
| R2-P2-3 | reject 网络失败被谎报为终态 rejected（服务端仍 pending，safe 计划仍可能被 Agent 提交） | catch 置 error（可重试） |
| R2-P3-1 | journal 落账返回值不消费（静默丢账） | 两处消费 + logger.error |
| R2-P3-2 | receipt 回写误用 unlocked 变体（绕过 plans RMW 串行） | 锁外点改锁定变体 |
| R2-P3-3 | GET plans applied+超TTL 显示 expired | applied 前置 |
| R2-P3-4 | 预览路由重复 store_plan | 移除（service 内单点） |
| R2-P3-5 | undo/redo replayed 分支不可达（head 迁移后 kind 闸先挡） | 披露保留（行为无害） |
| R2-P3-6 | 进程锁回收不看 waiter | 披露保留（256 上限 + 恰有 waiter 的极小窗口） |
| R2-P3-7/8/9 | 工具文案 materialize 残留 / schema 死 model_config / undo fire-and-forget 静默 | 全清偿（P3-9 = onError 回调 + toast + i18n 双语） |

## 终局（R3 轻量验证后）
见下方 R3 节。

# R3（2026-10-30，轻量聚焦验证轮，独立上下文）

只验证 R2 清偿五点（锁序/MVT tileUrl/reject 失败路径/journal 落账消费/undo onError 接线）。结论：**五点全部 ✅，无新引入问题，tsc 全量通过 —— PR-ready**。锁序 ABBA 窗口经推演消除（两路径持序同为 [分布式]→[进程]）；MVT 缓存键真正接线；失败路径语义诚实；journal 落账失败告警为活代码（Redis 后端两条 False 路径实证）；undo/redo 失败 toast 闭环（i18n 双语齐备）。

## 终局状态
- R1（P1×3/P2×10/P3×10）→ 全清偿；R2（P0=0/P1=0/P2×3/P3×9）→ 全清偿（P3-5/P3-6 披露保留，无害）；R3 → PR-ready。
- 无已知新引入 P0/P1/P2；残余 P3 均已披露且不影响用户闭环。
