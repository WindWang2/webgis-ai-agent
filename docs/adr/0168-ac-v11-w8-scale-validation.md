# ADR-0168: V11 W8 — 规模化验证矩阵（C4 成本/波次扩展、408 核心组实跑、波次 ratchet、盲评计划、成本治理）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-cartography/v11-master（W8）
- 关联: ADR-0159（质量事实库/ratchet）、ADR-0161（W1 成本观测承诺）、ADR-0166（W6 批量队列）、ADR-0167（W7 阻断切换以 W8 基线为条件）

## 1. 背景与靶心

任务书 W8：「17 图型 × 12 数据态 × 2 语言 × 4 输出形态 = 1632 组；先核心
408 组；每组 {quality_metrics, cost_tokens, cost_ms, artifacts}；ratchet 按
图型 × 检查项 × 波次聚合；成本治理；盲评 60 组」。G9（无 50k 基线已由 W3
补）与 G11（成本治理）在本波收口。

## 2. 决策一：C4 扩展字段（迁移 0058，只增列）

- ``cartography_quality_runs`` 增 ``wave`` / ``contract_version`` /
  ``map_type`` / ``cost_tokens`` / ``cost_ms``；``cartography_quality_metrics``
  增 ``wave``（聚合免 join）。全部 nullable —— 既有行/消费方零破坏；
  空库 upgrade 实测通过；downgrade 反序回滚。
- 领号 0058（.alloc.json）。

## 3. 决策二：矩阵 runner（W8.1/W8.2）

- ``app/lib/harness/scale_matrix.py``：轴全部复用既有单点词表（
  ``closed_loop_corpus.MAP_TYPE_IDS`` 17 × ``quality_corpus``
  ``DATA_STATE_PROFILES`` 12 × zh/en × map/svg/pdf/print）。
  ``tier="core"`` = 17×12×2×1 = **408**；``tier="full"`` = **1632**。
- 每组执行确定性裁决链（数据态事实 → 合成值场 → ``resolve_symbology``
  → 版面五维评分）产出 ``{qualityMetrics, costTokens, costMs, artifacts}``；
  核心 408 组本机实测 **0.2s**（无浏览器/无网络 —— 浏览器渲染面并入
  W6 批量队列与像素 golden 批次）。
- **成本诚实**：无 LLM 通道 → ``costTokens=0`` + ``llmUsed=false``
  （记 0 而非伪造；W1 承诺的成本观测列由此接线）。
- 成本治理（W8.5）：``COST_BUDGET_MS`` 分图型阈值（首轮 provisional）→
  超预算入 ``budgetAlerts``（告警不拦截）。
- **golden 冻结**（``golden_corpus/scale_matrix/core_summary.json``）：
  确定性度量均值 + 样例格（**成本计时不入 golden** —— 机器相关量
  不应假裝成契约）。

## 4. 决策三：波次 ratchet（W8.4）

- ``Observation`` 只加不改增 ``wave``（默认 None，构造二/三参兼容）；
  新 ``aggregate_observations_by_wave`` 按「图型 × 检查项 × 波次」分位
  聚合（缺 wave 归 ``unscoped``）；**基线匹配语义不变**（scene×check），
  波次服务于趋势与矩阵报告。
- ``matrix_results_to_observations`` 桥：矩阵结果 → 观测行（scene=图型、
  check=度量键、wave 标签）。
- **劣化 100% 拦截实证**：核心矩阵全量观测 → active 基线 → 注入 ×0.5
  劣化 → 违规数 == 观测数（测试锁定；delta_pct 正=劣化幅度语义）。

## 5. 决策四：盲评基准（W8.3）与像素 golden（诚实边界）

- ``docs/dev/ac-v11-blind-review.md``：**评分卡 + 预注册判据**（≥55%
  胜率/45–55 无显著差异/<45 回滚审查）+ 60 组确定性抽样计划。人工评分
  未完成前保持空白 —— **不得虚构分数**（AI 诚实边界）。
- 像素级 golden 从 9 场景扩 ≥120：需浏览器渲染批次（W6 批量队列 + 头照
  流水线）——本波不伪报，登记为浏览器批次待办（与 W6 台账一致）。

## 6. 验收对照

| 任务书 W8 验收 | 状态 |
|---|---|
| 核心 408 组矩阵跑通并入库 | ✅ 实跑 + golden 冻结（入库 = 观测行桥已就位；事实库写随批次调度） |
| golden ≥120 组 | ⚠️ 确定性 golden 已扩（矩阵/上下文/修复/拉伸四语料）；像素级 ≥120 待浏览器批次（登记） |
| 盲评集完成且有结论 | ⚠️ 评分卡与抽样计划交付；人工评分待执行（不得代评） |
| 成本预算与告警生效 | ✅ budgetAlerts + 测试（超预算必告警） |
| ratchet 注入劣化 100% 拦截 | ✅ 测试实证 |

## 7. 风险与回滚

- 矩阵执行确定性（无网络/无时钟入 golden）；预算阈值 provisional
  （W8 实跑分布校准后转拦截——与 §0.5 纪律一致）。
- 回滚点：tag `ac-v11-w8`。
