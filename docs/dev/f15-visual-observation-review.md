# F15 Independent Review — 视觉观察/批评/修复闭环（ADR-0214）

- 审查人：Subagent C（独立审查员，与实现者分离，全程只读 + 实跑测试）
- 审查对象：`origin/master(9e1ad229)...HEAD`，实现 commit `b6a51e32`、
  测试/文档 commit `c03e2900`
- 审查员实跑验证：F15 五套件 62 例 + scope matrix/openapi 20 例 +
  邻域回归（visual seam wiring / unified findings v7 / product verdict /
  repair planner v6）56 例 —— 全绿；另有恶意 evaluator 运行时 probe
  （error 级经 seam → planner 只给 deferred/executor=user）。

## 结论

**无 P0。** 硬约束全部成立且有真实测试背书：视觉永不 correctness
verifier、默认关零行为变化、user-wins、单一真相（无第二 finding 类型/
修复通道/哈希）、ref-only/有界。

## P1 清偿（合入前必须，已全部完成）

| ID | 问题 | 清偿 |
|---|---|---|
| P1-1 | 上传通道 `read()` 全量物化后才查大小（内存 DoS 面） | 改为 `read(MAX_SCREENSHOT_BYTES + 1)` 有界读取（`visual_repairs.py`）；既有 oversized 负例（4MiB+2 载荷 → 400）即覆盖该路径 |
| P1-2 | 两个「反翻转」测试 green-by-construction（测试内重抄推导/手工 append，未锁生产行为） | 重写为真端到端：① provider 侧 fake evaluator 注入 error 级视觉 finding → 真实 `run_map_finalization` → deterministic unrepairable error（`layer_missing`）仍裁决 failed、视觉只以 warning 披露、verdict 非 READY 系（`test_visual_error_cannot_mask_deterministic_failure`）；② 仅视觉 error 无 deterministic error → 完成时 verdict 必须 `READY_WITH_WARNINGS`（`test_visual_error_alone_cannot_upgrade_or_block`）；③ corpus 侧同型真端到端掩盖测试（`test_deterministic_error_not_masked_by_visual_findings`） |

## P2 处置

| ID | 问题 | 处置 |
|---|---|---|
| P2-1 | `_prune_blob` 跨会话内容寻址共享窗口（A 淘汰删掉 B 引用的 blob → B 侧诚实缺席） | docstring 如实披露窗口 + 降级语义（resolve 失败 = 诚实缺席绝不误判）；跨会话 refcount/GC 记 Out of Scope |
| P2-2 | vshot 孤儿 blob 无 GC（clear_session 不清 blob） | 同上记 Out of Scope（单会话 4MiB×8 上界成立；全仓唯一 `vshot-` 写面便于后续前缀清扫） |
| P2-3 | `_visual_*` 私有键随 map-state 端点下发前端 | 已修：chat.py 沿 `_situation_*` 先例 pop 三键 |
| P2-4 | plan/product 面 severity 未封顶与 ADR 措辞冲突 | 澄清而非改行为：plan 面保留 error 级是 **ADR-0209 决策四的既定语义**（error → deferred/requires_user_approval，是用户批准流的前提）；ADR-0214 §1 措辞已改为「披露面封顶 warning / plan 面保留 error 级」 |
| P2-5 | blob 键无会话绑定（当前不可利用，纵深防御） | 记 Out of Scope（今日无任何按 ref 读字节的用户入口；若未来加该类端点必须先加会话校验） |
| P2-6 | user-wins 措辞与实现相悖（design/route docstring/测试头） | 已修：统一为「user origin 是自有锁的唯一 override，批准经 `touches_locked` 披露后知情执行；agent/system 自动路径仍被 guard 拒绝」 |
| P2-7 | 账本/提案/索引 RMW 竞态未披露 | 已在 ADR-0214 决策五补「last-write-wins、欠计可接受（只会欠披露不会过披露）」 |

## Nit 清偿

- `corroborates:<finding_id>` → 文档统一为 `corroborates:<taxonomy 类>`（与 fusion.py 实现一致）；
- design 文档 `first_verdict` → `first_revision`（与 recurrence.py 一致）；
- 删除 `VisualScreenshotUploadResponse.pruned` 死字段；
- 修复 `test_visual_recurrence.py` 中 L2「不波及」的恒真断言 → 真实校验
  （newly 指纹归属、L2 未硬停、runs==1）；
- apply 路由补注释：显式 mutation_id 重放在收敛账本填满后先落
  `HEAL_CONVERGENCE_EXHAUSTED` 而非 dedup 回放（healer 预检顺序，可辩语义如实披露）。

## 基线失败区分（非本分支引入）

邻域全量（gis_harness + visual critic + scope matrix，-n 2）7 个失败，
**全部在 pristine origin/master 上原样复现**（临时 worktree 逐例验证）：
`test_capability_graph_v8` ra3（套件顺序全局状态污染）、
`test_conformance_corpus`、`test_workflow_guards`×3、
`test_workflow_v4_budget`×2（wallclock 计时敏感）。F15 新增/修改面零回归。

## 修复后回归

- F15 全部 64 例 + wiring/unified_findings 邻域：全绿；
- map-state 路由邻域（session_store_offload / deep_review_runtime）：15 例全绿；
- ruff（app/ + tests/）：全绿。
