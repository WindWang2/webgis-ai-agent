# INDEPENDENT_REVIEW — Track A（zcode/gis-harness-map-operations-vnext）

评审人：独立 general-purpose subagent（未参与编写；2026-10-31）。
方法：全量 diff 逐文件读 + 周边模块语义核对（mutation facade/lifecycle
引擎/render_observation）+ 5 个只读复现脚本（真实引擎）+ 复跑测试。
原则：不依赖测试通过下结论，每条 finding 附 file:line 证据。

## 发现 → 修复 → 复验

| ID | 严重度 | 发现（证据） | 修复（commit） | 复验 |
|---|---|---|---|---|
| B-1 | **P0** | symbol 效果把编译期陈旧 presentation 快照经 UpsertLayerIntent 整层写回：`service.py:355-361` 构造、`mutation_handlers.py:163-174` 整层替换、`_preserve_durable_presentation`（`mutation_primitives.py:564-640`）只护 incoming 缺席（且 process_layer_ingestion 注入默认 visibility，编译期剥离无效）；upsert 守卫只读 visibility，opacity 不在面（`mutation.py:202-235`）。三个场景复现：同操作显隐被洗回 / visibility applied 回执与事实相反 / 窗口内用户 opacity 被洗白（红线 2 违例） | symbol intent **延迟到执行时构建**（`_build_deferred_symbol`：读新鲜 spec 只覆盖尺寸键 + CAS 链 `expected_revision=链上值` 闭合微竞态） | `test_review_remediation.py::test_b1_symbol_does_not_revert_earlier_visibility` + `::test_b1_symbol_does_not_launder_concurrent_user_opacity`（用户 0.3 保留、visibility=none、radius=8/9）双绿 |
| B-3 | **P1** | 中链 superseded 被 `_status_of` 聚合为 committed（applied>0 时）；chain_aborted 不进任何计数；message 不披露（评审真实引擎复现：status=committed, counts=1/0/0 而 effects=[applied,superseded,chain_aborted]） | `_status_of`：superseded 恒显式（部分生效→partial）；`aborted_count`/`superseded` 字段 + message「被并发修改取代（N 项未尝试）」 | `::test_b3_mid_chain_superseded_reports_partial_not_committed`（竞态注入：partial + superseded=True + aborted_count≥1）绿 |
| C-1 | **P1** | 全失败（零 applied）→ status="partial" → 工具 success=True | status="failed"（零 applied + 有 failed）；工具 success 集移除 failed | `::test_c1_all_failed_is_failed_not_partial` 绿 + `test_a5` 断言 failed |
| C-2 | **P1** | 咨询态回执（clarification/noop/rejected）粘滞 operation_id：同 id 重试永远返回存证（评审复现：歧义消除后重试仍 clarification_needed, replayed=True）—— 与工具「重试复用同一 id」指示组合成死锁 | `_REPLAYABLE_STATUSES` 重放门只对已执行回执生效 | `::test_c2_clarification_receipt_not_sticky`（歧义→消除→同 id committed）+ `::test_c2_executed_receipt_still_replayable`（已执行仍幂等）双绿 |
| A-2 | P2 | commit 路由无限速（ack 有） | 60/min/IP 限速（与 ack 同款） | `test_routes` 全绿（limiter stub） |
| A-3 | P2 | 双批准竞态由引擎 mutation_id dedup 兜底（先于 CAS），零双删，仅审计面冗余 | 接受（记录）——引擎 dedup 是权威 | 评审复现脚本确认 |
| A-5 | P2 | drop_receipt 与 commit 之间崩溃窗口丢失 pending | **bypass_replay** 替代先删后提交：提交失败 pending 保留，owner 可重试；成功后 `_persist_receipt` 覆盖同 id | `::test_a5_failed_approval_keeps_pending` 绿 |
| B-2/B-5 | P2 | 回执环 RMW 无锁（跨两次锁窗口）—— 并发 commit 可互丢环条目；后果限于审计面（引擎 dedup 权威，不产生重复 mutation） | 接受（记录）；后续可改 set_map_state_fields | 评审分析确认上界 |
| B-4 | P2 | `_read_revision` 锁外读、失败按 0 → 引擎 CAS 拒绝（fail-closed），仅偶发伪 superseded | 接受（记录）；公共面 `read_current_revision`（F-1） | 引擎锁内 == 比对是权威（`lifecycle_engine.py:413-427`） |
| C-4/D-6 | P2 | status:'error' 兜底与部分 reason_code 无 i18n 键（卡面显示原始键） | zh/en 补 `status_failed` + 4 个 reason 键 | i18n 对称（lint CJK 门通过） |
| D-3 | P2 | 客户端 `?limit=` 服务端不消费（幻影契约） | 移除参数 | typecheck 绿 |
| D-4 | P2 | `fetchMapOperations` 零调用方（服务文档宣称的前端对账未接线） | MapHarnessCards 挂载 5s 拉环回填 bus（跨刷新恢复） | 组件测试绿（不抢观察窗口） |
| E-3 | P2 | symbol 走完整摄取管线（性能定位错配，非正确性） | 接受（记录）；中期方案=PatchLayerStyleIntent 扩展尺寸键（与 B-1 一并重构最优） | — |
| F-2/F-3 | P2 | `_raise_if_deleted` 三处同构副本 / receipt 自由 Dict | 接受（记录；与 interaction_events 先例一致的仓库惯例） | — |

## 通过面（评审确认）

- 红线 1（归属）：5 端点全部 require_owned_session + 410 tombstone + 无客户端身份信任面（A-1/A-4/A-6）。
- 红线 3（ack 新鲜度）：render_seq==最新观察 && ≥裁决代次，双 409 typed（A-6）。
- 红线 4（幂等零新 revision）：环 + 引擎 dedup 双层（C-2 修正后语义收窄到已执行回执）。
- 兼容：auto 模式 pipeline 披露零形状漂移（D-1）；注册/路径/capability 无冲突（D-2）；i18n 对称（D-5）。
- 资源：环 16×slim、pending 载荷 ≤8 效果、preview 无 mapspec、前端 ≤3 卡（E-1/E-2/E-4）。

## 结论

P0×1、P1×3 全部关闭（含回归测试）；P2 记录 12 条，其中 5 条已顺带修复
（A-2/A-5/C-4/D-3/D-4/F-1），7 条接受并附理由（A-3/B-2/B-4/B-5/E-3/F-2/F-3）。
修复后全量：后端 map_operations 63 + registry 族 116、前端 16（新增）+ 全量 4676。
