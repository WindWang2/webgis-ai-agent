# V3 关键决策记录

## D1 — 不新建平行 registry
所有新算法走既有 AlgorithmDescriptor + ParameterContract + CapabilityDescriptor 域包模式；
新工具并入既有 `app/tools/*_tools.py` 注册面；目录由 gen_science_catalog.py 再生成。

## D2 — MGWR 为真 backfitting，非 GWR 改名
逐变量带宽向量 + backfitting 迭代（每轮对残差逐变量做带宽搜索的局部 WLS），
收敛残差 RSS 披露；与 GWR 单一带宽结果在常数带宽情形下应重合（作 conformance 锚点）。
带宽搜索用 LOO-CV 分数（与既有 GWR 一致），确定性、无随机。

## D3 — co-kriging 选 Markov Model 1 collocated 变体
完整 LMC（线性模型 of coregionalization）需多变量交叉变差函数拟合，稳健性差；
MM1 collocated cokriging 只需主变量变差函数 + 相关系数，可解析表达并在 meta 中
披露近似性质（Journel & Huijbregts MM1）。非共位协变量取最近邻主变量位置值。

## D4 — 自然邻域用 Sibson（scipy Delaunay 邻接）
自然邻域插值权重大部分场景等价于 Sibson 坐标；凸包外诚实 NaN（不外推）。
数据规模守卫 ≤200k 点。

## D5 — 相干性估计只接受复数 SLC 通道
强度数据不伪造相干性；输入通道 re/im 分离传入，样本窗口估计 + 有效对数披露。
EXPERIMENTAL 成熟度 + 严格形状守卫。

## D6 — p-median/p-center exact 走 scipy.optimize.milp
需求点×候选点乘积 ≤ 25000 且候选点 ≤ 500 时 MILP exact（与实现常量
_MILP_MAX_PRODUCT/_MILP_MAX_CANDIDATES 同步）；超界类型化拒绝退回既有
Teitz-Bart/greedy 并在 solver 字段披露（不静默）。

## D7 — SAR MT 滤波 intensity 域
Quegan 谱域滤波需要 SLC 复数谱；本库 GRD 强度路径实现 MT-Lee（时间均值引导的
局部 MMSE），meta 披露与 Quegan 的差异。

## D8 — 云检测只做亮度阈值热云基础 + Fmask 诚实 planned
无热红外/大气校正链，Fmask 完整实现超出本库数据模型；提供 EXPERIMENTAL 亮度
基础掩膜 + planned 描述符诚实拒绝全功能 Fmask。

## D9 — oracle corpus 生成式 fixture + 回放
scripts/gen_science_oracles.py 一次性生成（依赖 scipy/sklearn/esda 可用环境），
期望值硬编码进 JSON；回放测试零重计算（除被测实现本身），保证确定性与速度。

## D10 — backend selection 增量扩展
ScaleProfile 消费 raster_cells（raster 主导型算法 tier 判定），新增 estimated_bytes
可选输入；输出形状（BackendDecision/rationale/diagnostics）不变，向后兼容。

## D11 — 契约层校验增量收紧
output_artifact_type ⊆ capability.output_artifact_types 校验加入 registry.validate()；
现网唯一违例（admin.boundary_lookup）通过补 capability 输出类型修复（非弱化校验）。

## D12 — Gi* permutation 为 additive 选项
默认 normal 近似（现网行为不变）；significance_method="permutation" 启用 seed=42
置换 p，同参数契约 bump v3（additive 字段）。
