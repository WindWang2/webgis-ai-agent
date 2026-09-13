# ads-v1 校准报告（DS8 · ADR-0178）

## 排序权重（278 样本，keyword-only 模式）

- shipped 权重 MRR：**0.9194**（Recall@5 闸 0.80 已由测试锁定）
- sweep 最优 MRR：**0.9203**，候选 = {"relevance": 0.65, "coverage": 0.15, "freshness": 0.1, "cost": 0.0, "trust": 0.1}
- 结论：建议更新 WEIGHTS → 已应用

## 代价模型（fixture 网格实测）

- bytes/row 启发式 128B vs 实测 ≈157.6B（偏差 18.5%，P50 ≤ 30% 达标）；
- 行数偏差 0%（网格均匀假设在该语料上成立）；
- 常数单点 `planning/cost_model.py`，真实流量复测随 DS9 运行面接入。

## 阈值转定稿记录

- ranker.WEIGHTS：provisional → 定稿（本报告 sweep 依据）；
- TEST_EVAL_THRESHOLD（Recall@5 0.80 / MRR 0.55）：维持（实际 0.97/0.92 远超）；
- DS0 表面常量（acquisition_limits）：维持（行为冻结，未触发校准条件）。
