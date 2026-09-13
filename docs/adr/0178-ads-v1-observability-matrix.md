# ADR-0178: ads-v1 可观测、成本治理与验证矩阵（D4 落库 + 864 组矩阵 + ratchet）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-data-supply/v1-master · DS8（自适应数据供给与接入 · 并行线 P1）
- 关联: ADR-0170（D4 契约）、ADR-0174（ChainResult 产 fact）、迁移 0071（ads_acquisition_facts + ads_cost_budgets）、`data_fabric/acquisition_limits.py`（表面常量校准复核）

## 1. 背景（缺口 A10）

取数无埋点（行数/字节/耗时/重试/降级），无法证明「取数变好了」也无法治理代价；
验证无矩阵；排序权重与代价常数全部 provisional 无校准依据。

## 2. 决策一：D4 全字段落库（迁移 0071，ds_ 命名空间）

- `ads_acquisition_facts`：每次取数一行（成功/降级/失败**全记录**，覆盖率
  100% 闸测试）——request_id/dataset_key/source_id/version/rows/bytes/
  latency_ms/retries/degraded/outcome(CHECK 词表)/fallback_json/drift/wave/ts；
- `ads_cost_budgets`：(source_id × request_type × wave) 预算行；
- **表隔离（§8.1.7）**：`ads_*` 前缀与 V11 `carto_*` 不共享表，仅展示层共享；
- 进程内 `FactsStore` 为缺省后端（单 worker/测试），0071 表同形（多 worker
  DAO 随 DS9 收口接线）。

## 3. 决策二：验证矩阵 864 组（核心 216）

`data_fabric/matrix.py`：**12 源 × 12 请求型 × 3 降级场景 × 2 语言 = 864 组**，
每组确定性执行取数决策栈（D2 计划编译 → D3 链解析 → DS5 版本门 → D4 事实），
断言各组预期（计划有效/决策触发/降级落源/漂移标注/澄清路径/预算建议）。
- **实测：核心 216/216，全量 864/864 全过**（离线、秒级、可重复）；
- 产物 CSV：`docs/dev/ads-v1-validation-matrix.csv`（864 行逐组台账，闸测试
  锁行数与通过率）；
- 英文语言维度暴露并修复了「last five years」数字词解析缺口（评测驱动修复
  的实例）。

## 4. 决策三：ratchet 门禁（注入劣化 100% 拦截）

`FactsStore.ratchet_check(baseline_wave, current_wave)`：按 (源 × 指标 × 波次)
p50 聚合对比，指标 {rows, bytes, latency_ms, retries, degraded}，容差
{25%, 25%, 30%, 50%, 0}。**零基线特殊语义：0 → 任何增长即劣化**（0 次重试
变 3 次正是 ratchet 存在的理由）。闸测试：注入时延尖峰/重试风暴/强制降级
全部拦截（100%）；稳定波次与新增源不误报。

## 5. 决策四：成本看板（共享展示层、表隔离）

`scripts/ads_cost_dashboard.py` → `docs/dev/ads-v1-cost-dashboard.md`：
12 源 × 12 请求型预算行（max_rows 50k / max_bytes 8MB / max_ms 5s，provisional），
`FactsStore.check_budget` 对 rows/bytes/latency_ms/quota 任一超限产生
BudgetAlert。看板渲染语义与 V11 W8 对齐，读 ads_* 域事实。

## 6. 决策五：校准（provisional → 定稿）

`scripts/ads_calibrate.py` → `docs/dev/ads-v1-calibration.md`：
- **排序权重**：278 样本 keyword-only sweep——rel=0.65 优于 shipped 0.55
  （MRR 0.9194 → 0.9203）；**采纳 0.65 但拒绝 sweep 的 cost=0**（会破坏
  本地优先 tie-break），定稿 {rel .65 / cov .10 / fresh .10 / cost .05 /
  trust .10}；39 项检索相关测试复验无回退（混合检索 10 组含反例断言）；
- **代价模型**：bytes/row 启发式 128B vs 网格实测 157.6B（偏差 18.5% ≤ 30%）、
  行数偏差 0%——常数维持；
- DS0 表面常量（acquisition_limits）：行为冻结未触发校准条件，维持。

## 7. 后果

- DS9 收口消费：facts DAO 接线 0071 表、终版台账引用矩阵 CSV 与校准报告；
- 后续所有取数路径（链执行/版本门/矩阵）产出统一 D4 事实——ratchet 的
  输入从此不完备才怪。
