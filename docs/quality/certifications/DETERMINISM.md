# Determinism Certification（自动生成）

> 由 `python scripts/gen_determinism_certification.py` 从
> AlgorithmRegistry 派生，请勿手改。行为认证（规划/算法/
> artifact 双跑一致）：tests/quality/
> test_determinism_certification.py。

## 分类汇总

| class | count | 语义 |
|---|---|---|
| deterministic | 150 | 确定性计算（同输入同输出） |
| seeded-stochastic | 29 | 种子化随机（可复现；种子来源已声明） |
| stochastic-unseeded | 2 | 无种子随机（声明型 nondeterministic） |
| INCONSISTENT | 0 | 自相矛盾声明（deterministic=True 且 unseeded）——禁止 |
| total | 181 | |

## INCONSISTENT / stochastic-unseeded 明细

- `admin.boundary_lookup`（stochastic-unseeded，status=EXPERIMENTAL）
- `poi.area_search`（stochastic-unseeded，status=EXPERIMENTAL）

- 内容指纹：`1b1da3b823c96397…`
