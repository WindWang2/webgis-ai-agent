# G28 ACCEPTANCE — 验收矩阵 → 测试映射（oracle 独立手写）

| ID | 要求 | 测试/证据 | 状态 |
|---|---|---|---|
| A01 | 新 adapter 有真实生产调用 | test_goal_satisfaction_finalizer_wiring（pipeline 持久化块） | pending |
| A02 | 已有 evaluator 仍唯一 | 既有 v1/corpus 测试全绿 + 无第二评估入口 grep | pending |
| A03 | 工具 success ≠ 已显示 | render_status absent → map NOT_EVALUATED（v1 已锁）+ 新 receipt 面 | pending |
| A04 | structural 不越级 | PASS_CAPABLE_CLASSES 测试已有 + 新证据全部 deterministic | pending |
| A05 | 旧 revision 截图不 pass | frame observed_at_revision≠checked → STALE；export stale 已锁 | pending |
| A06 | mapcommit/artifact freeze 身份正确 | receipt revision 对账 checked_revision 测试 | pending |
| A07 | partial/sample/unknown 不冒充全集 | semantics.population_partial（compute skipped_rows/导入 rejected/交付 missing_ids/聚合 __other__） | pending |
| A08 | 计数 vs 率 | measurement_kind+unit_status 闸 | pending |
| A09 | 单位/CRS 不匹配不虚构 | crs_unverified/unit_undeclared 闸 | pending |
| A10 | 缺分母/空数据明确状态 | density_without_denominator + empty 路径 | pending |
| A11 | 合法空结果 ≠ 执行失败 | envelope.empty + receipt completed → 空=fulfilled（披露） | pending |
| A12 | 用户锁优先 | repair plan 不产生写动作 for locked/user-owned | pending |
| A13 | 多 frame 逐个 | frame 闸逐 frame 缺口列 missing | pending |
| A14 | snapshot ≠ live | observation=snapshot-only → PARTIAL | pending |
| A15 | 非地图任务不强制浏览器 | statistics-only 契约无 MAP → satisfied 无 render 证据 | pending |
| A16 | 导出/出版不混 | delivery_expected 面与 map EXPORT 分离评估 | pending |
| A17 | owner/scope 不匹配证据拒绝 | adapter scope 过滤测试 | pending |
| A18 | 同名不同 checksum 不可混用 | artifact_checksum_mismatch → FAILED | pending |
| A19 | 取消 job ≠ complete | delivery status=cancelled → FAILED/absent | pending |
| A20 | 迟到结果不改裁决 | 评估只在 finalizer 快照内；receipt 戳 > checked_revision → 忽略+missing | pending |
| A21/A22 | repair 预算/无进展停止 | runtime_repair MAX_PASSES/exhausted 消费测试 | pending |
| A23 | 写操作不盲目重试 | repair 动作分类只读/写入 + 写入需 CAS 路径 | pending |
| A24 | UI/SSE/最终文本同裁决 | 同一持久化块三面投影测试（bounded_payload/goal_line/前端 payload） | pending |
| A25 | 旧回执兼容 unknown 而非假 pass | 缺字段回执 → unknown 码、不 PASS 不 FAILED | pending |
| A26 | 真实 Pi 闭环 | P7 旅程测试/证据 | pending |
| A27 | 浏览器观察缺席不伪造 | observation absent → not_observed（诚实） | pending |
| A28 | ≥6 端到端场景独立 oracle | corpus 新族 + 测试场景清单 | pending |
| A29 | 数据更新使旧证据失效 | revision 对账（refresh map_observation + stale 闸） | pending |
| A30 | 独立运行不伪造未来能力 | 仅消费已合入接口；G22-27 缺席 → unknown/blocked | pending |
