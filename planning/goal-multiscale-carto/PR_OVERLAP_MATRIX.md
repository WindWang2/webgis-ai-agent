# PR_OVERLAP_MATRIX — GOAL 04 多尺度制图综合

对照基线：origin/master `6c5e71c2c`（#1868-#1875 全 merged）。逐项标注 new/extend/reuse/avoid。
最后核查：2026-11-02（PR 列表 + issue 列表 + git log 全量复核）。

| 既有能力（PR/模块） | 关系 | 说明 |
|---|---|---|
| #1875 label/export parity（label_engine/label-geometry/typography） | **reuse** | 只读复用排版原语与 parity 测试范式；不建第二 label solver |
| #1875 layer_browse/aggregate 工具 | **reuse** | importance 规则读值分布复用 feature_aggregate 服务函数 |
| #1874 map_operation.v1（事务/回执/CAS） | **reuse + extend** | apply 工具照抄 preview/commit/receipt 模式；不扩其效果词表 |
| #1870 design candidates / decision trace | **reuse（只读）** | 不改候选生成；importance 证据未来可作其输入，本期只留接缝 |
| #1871 atlas batch | **avoid** | 不做 scale-driven 图册分页（登记为未来接缝） |
| #1868 data preparation / vocabulary | **reuse** | 复用角色词表（等级/行政名词）作 importance 证据源之一 |
| #1869 analysis workflows | **avoid** | 不碰分析编排 |
| #1873 feature editing | **avoid** | 不做要素编辑 |
| v1.6 representation 协议 | **extend** | generalized/aggregate_grid 从「capability seam 声明即信」升级为「真实工件生产 + 消费」；窗口结构 additive 扩展 source 覆盖 |
| scale_rules.SCALE_TIERS / boundary_detail | **extend** | boundary_detail 四档获得第一个真实消费者（profile 几何策略） |
| MVT simplify | **avoid（不改）** | 派生工件是独立通道；mvt.py 语义/缓存/ETag 不动 |
| symbol-law.ts | **reuse（只读）** | import densitySignal/阈值；不改行为 |
| #1876（open，GOAL 01 spatial-semantics） | **avoid** | 无文件交集；其 SpatialSemanticContract 不与本包互相 import |
| issues #1809/#1810/#1811（filter 缺陷族） | **avoid（绕开）** | 多尺度表达不依赖运行时 filter 改写；取舍走预计算工件。若 artifact 窗口需要 filter 语义，在工件生成期物化（避免触发缺陷） |
| issues #1841-#1843（安全） | **avoid** | 不复制信任边界；新工具沿用 session 属主鉴权路径 |

冲突监测记录：
- 2026-11-02 初查：#1876 open 且与本任务零文件交集。
- 每个 M 里程碑开始与提交 PR 前各再 fetch 复查一次（见 LOOP_STATE.md 轮次记录）。
