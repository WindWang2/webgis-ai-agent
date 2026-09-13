# ADR-0169: V11 W9 — 横向收口（债清核准、契约升级测试、文档/ADR 定稿、埋点与安全复核、终版门禁）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-cartography/v11-master（W9）
- 关联: ADR-0160~0168（本线全部）、ADR-0159（质量基座）、S1 债扫描（`docs/dev/ac-v11-debt-scan.csv`）

## 1. 死代码与重复清理（核准结论，§0.5 以代码为准）

任务书 W9.1 期望「deprecated 适配层清零」。逐项核准结果：

1. **W0.2 的 label 适配层（label_engine / label_collision）**：**不是 deprecated
   层**——它们是生产公共 API 模块（`mapspec_to_svg` / `publication_export`
   直接 import），内部几何已收敛到 `label_typography` 单点。删除 = 破坏
   冻结公共 API（违 §8.1）。**保留并核准**：适配层职责 = 公共名 re-export
   + 求解语义，非重复实现。
2. **S1 登记的六个「ADDL-ORPHAN」**（catalog_docs / design_system /
   export_component_catalog / isoline_model / layout_solver / style_tokens）：
   逐模块引用核验显示**均有测试或工具脚本消费**（`symbology_audit.py`
   用 design_system + isoline_model；`golden_corpus/corpus.py` 用
   layout_solver；各模块有其测试族）。**结论：按 §0.5「以代码为准」改登记
   为「工具/测试面消费模块」，不删除**（删除会连根拔起审计脚本与其测试
   锁——那不是债清是破坏）。债扫描 CSV 同步更新标注。
3. **三套渲染残留路径**：W6 已清 `lib/map-exporter/` 残壳与陈旧注释；
   渲染器物理合并不在 W6/W9 打勾前完成（G3 主战役为跨波次工程），残留
   路径以 W6.1 parity 骨架锁防再分叉——如实登记（不假打勾）。

## 2. 契约升级测试（W9.2 机械化）

`tests/cartography/test_contract_upgrade_v10_v11.py`（6 例）：C1（v10
SymbologyDecision dict 在 v11 可读且再序列化保形）、C3（v10 标注 spec 无
新键 → 缺省语义）、C2（publication v1 逐字段不变 + IR v2 fixture 校验）、
C4（无 wave/cost 旧观测行聚合面不变 + unscoped 归组 + 旧构造兼容）。
「只加不改」从口头纪律变为机器断言。

## 3. 文档与 ADR 定稿

- ADR-0160~0169 全落 `docs/adr/`（每波一篇 + 本收口篇）。
- `docs/dev/ac-v11-*`：复核纪要、债扫描、契约 JSON、六波台账、盲评卡、
  高分评估、格式矩阵、M2/M3 PR body 存档 —— 齐备。
- `CHANGELOG.md` Unreleased 逐波增补；`docs/cartographic-closed-loop.md`
  的 L5 现状与阻断切换条件以 ADR-0167 为准（指针不复制，防双头）。

## 4. i18n 与可观测（W9.4）

- **i18n**：本线新增用户可见文案**零条**（交付物为引擎/契约/工具面；
  story 大纲与 atlas 标题均取自既有用户输入字段）。如实登记，不虚构
  翻译面。
- **埋点四类映射**（既有证据结构承载，不新建采集设施）：
  | 类别 | 承载结构 | 查询面 |
  |---|---|---|
  | 决策来源 | `SymbologyDecision.reasons/rejected[]`、`intent_evidence` | `query_intent_evidence` / decision 工件 |
  | 自愈动作 | `carto_feedback_signals(target=selfheal:*)` | `action_success_table` |
  | 成本 | `cartography_quality_runs.cost_tokens/cost_ms`、矩阵 budgetAlerts | `quality_trend_report` / 矩阵报告 |
  | 降级 | 各链路 degradations[]（golden/highdpi/raster/atlas…） | 工件与诊断码 |

## 5. 安全复核（W9.5）

- **导出临时文件**：批量队列不落盘（状态调用方持有）；导出物走既有
  上传通道；W6 新增 svg2pdf 为纯内存转换（无文件系统访问）。
  `publication_export` 的 url_fetcher 全拒（V10 既有）不变。
- **VLM 数据外发**：本线**未接线**任何真实 VLM provider（W7 只落本地
  判据 + 开关）；外发通道不存在 → 无脱敏缺口。接线时按 ADR-0167 的
  fail-closed 前提另行评审（登记待办）。
- **批量队列权限**：队列为纯执行器（无路由暴露）→ 无新增攻击面；
  路由接线时随 auth 依赖评审（登记）。
- 新增依赖：`svg2pdf.js`（MIT，前端运行时动态加载；无 postinstall 脚本
  执行——pnpm approve-builds 提示不适用运行时包）。

## 6. 终版门禁与交付台账

- `quality_gate_local.sh` 全量一次跑通（终版原文入 PR）；
  最终覆盖 57.7% 区间（floor 50）；lane 约 935 测试。
- 终版台账 `docs/dev/ac-v11-w9-ledger.md`（任务 → 文件 → 测试 → 证据
  全量四列 + 全波汇总）。

## 7. 风险与回滚

- 保留孤儿模块 = 保留审计能力（回滚无需动作）；契约升级测试失败即
  「只加不改」被破坏的信号。
- 回滚点：tag `ac-v11-w9`（本线终点）；各波 tag 独立可回。
