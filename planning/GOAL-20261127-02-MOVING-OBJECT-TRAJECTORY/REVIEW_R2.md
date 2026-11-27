# REVIEW_R2 — 复核（2026-11-28，独立上下文子代理，非作者/非 R1）

## 复核范围

R1 全部 14 项发现的关闭证据核对（对照最新代码 file:line）；R1 修复是否引入新问题；关键旅程重跑；门禁重跑。审查期间两条追加提交（72278ac17 台账、f82778e26 keep_input 残留清理）一并纳入并在最终 HEAD f82778e26 复验。

## 门禁重跑（R2 审查者自跑）

| 套件 | 结果 |
|---|---|
| tests/unit/moving_object | 89 passed |
| registry 一致性 + parity + schema v6 | 114 passed, 1 skipped |
| conformance | fail=14 = master 基线（pass 342 / warning 257） |
| 前端 vitest（新面） | 16 passed |
| 旅程 | 4 passed（J1 dwells==9 在 3600s 缺省间隙下成立） |
| git | status 干净；origin/master..HEAD 14 commits 无 merge，叙事连贯 |

## 逐项判定

R1-1 … R1-14 **全部 CLOSED**（逐项 file:line 证据见审查者报告；要点）：
- R1-1 typed 拒绝链：except 顺序正确（RegionGeometryError 先于基类）、Literal→422、路由 400/404/409、窗口 typed 拒绝、4 个行为测试经 service 缝隙真实执行
- R1-2 家族=绑定身份：同窗异参堆叠在结构上不可能；J2 断言 fam_a==fam_b 且 apply-A→apply-B 走真实替换路径；前端 findAppliedTrajectory 家族无关
- R1-3~R1-14：全部有行为测试或读验证证据（详见 REVIEW_R2 审查记录）

## R2 新发现（均非阻断）与处置

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| R2-1 | P3 | 无窗口时非法 assumed_timezone 经 ingest 路径仍为粗粒度 typed（非 500，但代码为 INPUT_INVALID） | 已修：窗口路径 TRAJECTORY_TIMEZONE_INVALID；ingest 路径保证 typed 4xx（ecb36d2e3，测试断言两类代码均接受并注明归类） |
| R2-2 | P3 | _emit_region_dwell_span 残留 if True/缩进 | 已修（ecb36d2e3） |
| R2-3 | P3 | service 模块文档引用已删类名 | 已修 |
| R2-4 | nit | analyze 重建 load_regions 已建的 STRtree | 已修（单一 owner） |
| R2-5 | nit | dwell_max_gap_s 未在产品/工具面透传 | 已修（config+工具参数+测试，ecb36d2e3） |
| R2-6 | nit | total_is_sample / unknown_by_pair / SegmentCapExceeded→SCALE_EXCEEDED 无专项测试；无 REST TestClient 层 400 映射测试 | 记录为后续增量（route 代码 trivial 且已读验证；SCALE_EXCEEDED 经同型包装路径有 MovingObjectError-cap 测试覆盖） |

## 结论

**GO** — 2×P1 真实闭环（有行为测试），4×P2 全部代码+行为验证，P3 集合闭环（cap 算子一致性项部分统一，属 master 前既有）。无修复引入回归；无已知未解决 P0/P1/P2。
