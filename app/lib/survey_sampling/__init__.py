"""Survey sampling —— 概率抽样调查、专题图精度核查与面积估计纯函数库。

数学契约以 Olofsson et al. 2014（DOI 10.1016/j.rse.2014.02.015）
good practices 为基线（Agent 已核对原始论文的估计量与方差公式）：

- ``design``：抽样设计合同（分层 SRS 不放回；分配/样本量/digest）；
- ``selection``：可复现选样（纳入概率 π、权重 w=1/π、替补序列）；
- ``estimation``：设计一致加权误差矩阵、OA/UA/PA、类别面积与 CI；
- ``frame``：分类栅格 → 版本化抽样框（层面积等面积/测地、NoData）。

原则：无概率设计或无独立参考标签时**不得**输出总体精度；估计不可估
时显式 ``not_evaluated``，不伪造 0/CI。
"""
