# G28 ACCEPTANCE — 验收矩阵 → 测试映射（oracle 独立手写）

状态标记：✅ verified（测试实跑）/ 📝 documented（实现或有明确理由）/ ⏳ 部分。

| ID | 要求 | 测试/证据 | 状态 |
|---|---|---|---|
| A01 | 新 adapter 有真实生产调用 | finalizer wiring（pipeline 锁外预取→块持久化）+ 旅程 JG28 真实 dispatch/MapSpec | ✅ |
| A02 | 已有 evaluator 仍唯一 | 既有 v1/corpus 64 绿；无第二评估入口 | ✅ |
| A03 | 工具 success ≠ 已显示 | GC-receipt-computed-but-not-displayed + import-failed-vs-row + JG28 旅程反假完成 | ✅ |
| A04 | structural 不越级 | PASS_CAPABLE_CLASSES 白名单（v1 已锁）+ 新证据全 deterministic | ✅ |
| A05 | 旧 revision 截图不 pass | GC-receipt-frame-stale-observation-partial + export stale（既有）+ frame 闸 stale 按未观察 | ✅ |
| A06 | mapcommit/artifact freeze 身份 | wiring revision 对账 + delivery source_revision 投影 | ✅ |
| A07 | partial/sample/unknown 不冒充全集 | compute skipped/rejected/missing_ids caps（4 例）| ✅ |
| A08 | 计数 vs 率 | GC-receipt-compute-density-undeclared + caps_from_compute | ✅ |
| A09 | 单位/CRS 不匹配不虚构 | crs_unverified（import crs_source unknown/assumed）+ unit caps | ✅ |
| A10 | 缺分母/空数据明确状态 | density_without_denominator + envelope.empty 披露 | ✅ |
| A11 | 合法空结果 ≠ 执行失败 | GC-receipt-import-empty-legal（completed+0 行 → fulfilled）| ✅ |
| A12 | 用户锁优先且不触发破坏性 repair | repair_actions blocker（user_owned/lock → 无动作）| ✅ |
| A13 | 多 frame 逐个验收 | frame 闸逐帧缺口码 + GC-receipt-frame-snapshot-only-partial | ✅ |
| A14 | snapshot 不冒充 live | snapshot_only 闸（summary+行双侧）| ✅ |
| A15 | 非地图任务不强制浏览器 | GC-receipt-no-map-statistics-legal satisfied | ✅ |
| A16 | 数据导出与地图出版不混 | GC-export-csv-map-export-receipt-rejected + GC-receipt-delivery-current-fulfilled | ✅ |
| A17 | owner/scope 不匹配证据拒绝 | receipt_evidence_rows bundle session 过滤 + delivery 授权（复审确认不放宽）| ✅（生产惰性防御已记录）|
| A18 | 同名不同 checksum 不可混用 | GC-receipt-delivery-artifact-mismatch | ✅ |
| A19 | 取消 job ≠ complete | GC-receipt-delivery-cancelled-absent + JG28 旅程 pending 不落块 | ✅ |
| A20 | 迟到结果不改当前裁决 | wiring 快照确定性重评估测试 + ADR-0183 评估快照语义 | ✅ |
| A21/A22 | repair 预算/无进展停止 | repair_actions（blocker 停止 + expected_new_evidence）+ 既有 runtime_repair MAX_PASSES | ✅ |
| A23 | 写操作不盲目重试 | ACTION_REGISTRY 写路径登记 + 只读/写入分类测试 | ✅ |
| A24 | UI/SSE/最终文本同裁决 | bounded_payload.requirements + 前端 10 测试 + 旅程 SSE≡落库 + j17 真实浏览器 | ✅ |
| A25 | 旧回执兼容 unknown 而非假 pass | GC-receipt-legacy-fields-unknown-neutral + 复审 F5 证实 unknown 中性 | ✅ |
| A26 | 真实 Pi 闭环 | PI_JOURNEY_E2E=1 套件 1 passed（合成标注 fixture + 真实 dispatch/MapSpec）| ✅ |
| A27 | 浏览器观察缺席不伪造 | j17（真实 Chromium）map not_evaluated+reobserve + JG28 pending 诚实 | ✅ |
| A28 | ≥6 端到端独立 oracle | 语料 118 案例（12 假成功 + 3 合法无地图 + 6 任务族；frame 块由真实 verify_frame_product 产出）| ✅ |
| A29 | 数据更新使旧证据失效 | GC-receipt-refresh-stale-observation-partial + finalizer 漂移守卫（既有）| ✅（compute 侧陈旧锚为已知限制，见 REVIEW F8）|
| A30 | 独立运行不伪造未来能力 | G22-27 缺席 → unknown/not_observed（wiring 断言 refresh 空/frames not_observed）| ✅ |

## 已知限制（有明确理由；非阻断）

1. 回执↔行绑定 opt-in（行缺 bound_ref → 语义闸不生效）：A25 兼容姿态；
   不变量「回执族行必带 bound_ref」留 planner 侧后续（REVIEW F3）。
2. edit stamp_status 生产不可达（journal 头无该字段）：披露面仅在显式
   bundle 出现；journal 提交本身是真实事实（REVIEW F4）。
3. refresh/raster caps 为披露面（消费面待 G25/G27 语义接入）（REVIEW F5）。
4. compute/import/delivery 无陈旧锚（修订空间不同，避免假 stale；
   A29 由 render/refresh/MapSpec 漂移守卫覆盖）（REVIEW F8）。
5. 实模式浏览器 E2E（nightly 基建：admin 用户 + redis + celery）未在本
   worktree 跑 —— mock 档 j17（真实浏览器 + 生产契约 SSE）+ Pi 旅程
   （真实后端）两腿组合覆盖；nightly 车道沿用既有基建跑。
