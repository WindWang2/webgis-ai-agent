# Spatial Science & GeoAI Platform V4

science-v4 在 V3「科学契约 + 领域算法基础」之上完成纵向深化：算法面补齐
地统计模拟/全共克里金/时空克里金/水文层级，契约面把 ResourceEnvelope /
CancellationProfile / NumericalTolerance 从字段位变成**机器可查 ratchet**，
错误面收口全部 raw 三方异常泄漏。

## 算法新增（全部 native + descriptor + conformance + V4 契约三件套）

| 算法 | 实现要点 | 关键 conformance |
|---|---|---|
| `interpolation.simple_kriging` | SK 协方差形式 C=(nugget+sill)−γ；先验均值通道（缺省样本均值估计并披露） | nugget=0 样本点精确复现 |
| `interpolation.external_drift_kriging` | KED [1,d(x)] 双约束；目标处漂移 IDW 近似（approximate 披露） | 漂移主导场优于 OK |
| `interpolation.sgs` | normal-score 域随机路径条件 SK；caller_seeded 单流 PCG64；P10/P50/P90 ensemble | 同 seed 双跑**逐位一致** |
| `interpolation.cokriging_lmc` | LMC 双结构，B^u=ρ√(b11·b22) **PSD 按构造**；全共克里金系统 | |ρ|>1+1e-9 → NumericalInstability |
| `interpolation.st_kriging` | separable / product_sum（双时间尺度可分离正组合，PSD 按构造）；秒制时间 | τ=0 精确退化为空间协方差 |
| `terrain.breach` | 填洼识别 + epsilon 填面路径 + 最小开挖切沟（只降不升） | 开挖量 < 填洼量 |
| `terrain.hand` | 单遍逆拓扑望远镜求和（升序高程——接收者先结算） | 河网 HAND=0、零未解析 |
| `terrain.shreve` | 上游量级之和（合同级逐像元验证） | 量级=上游和（合同测试） |
| `terrain.pfafstetter` | 单级奇偶编码（干流最大汇流上溯 + 4 大支流） | 奇偶约定钉死 |
| `terrain.hypsometry` | 确定性直方曲线 + Strahler 1952 积分 | 线性坡 HI=0.5（解析不变量） |
| `terrain.solar_radiation` | FAO-56 Ra × 地形入射因子（heuristic 已披露） | 赤道春秋分 Ra≈37.6（±2%） |
| `terrain.sink_fill`(variant) | `chunked_band` 分块变体（heap 峰值 O(带宽×H)） | parity：偏差 ≤ 参考最大填深 |

## 契约 ratchet（Wave 1）

- `science_contract_v4.py`：44 项存量 heavy 未声明基线冻结（只许收缩）；
  新增 heavy 算法缺 resource_envelope / cancellation_profile / tolerance
  任一即红（`test_science_contract_v4_ratchet.py`，48 断言）。
- interpolation(16) + terrain(32) 全量三声明，与实现护栏常数逐一对齐。
- `terrain.sink_fill` 首发**非插值域 backend variants**（full_heap=reference /
  chunked_band=approximate）。

## 错误披露（Wave 2/3）

- pyproj.CRSError（RuntimeError 系）在 `to_utm_gdf_with_note` 边界折叠成
  InvalidCRS —— **KNOWN-GAP #1 xfail 转正**；
- geometry repair 唯一实现 `geometry_repair.py`（逐要素 method/reason/
  面积变化披露）；zonal_statistics 默认 strict=True 类型化拒自交几何 ——
  **KNOWN-GAP #2 xfail 转正**；
- taxonomy 新增 NumericalInstability / ConvergenceFailure（LMC PSD、嵌套
  拟合收敛、SGS 退化为真实生产者）。

## 取消与资源（Wave 10）

- terrain.py 取消点从 **0 → 3 条链**：fill 堆循环（8192 pops）/ D∞ 拓扑
  循环（64K cells）/ viewshed 扇区块边界；
- SGS：R×N 预算硬闸（2000 万单元）+ realization 边界 checkpoint；
- cancellation_profile 声明与真实 checkpoint 密度一致。

## 已知限制（诚实披露，见各 descriptor limitations）

- LMC 结构 sill 35/65 固定分解（非完整 Goulard–Voltz 迭代）；
- 嵌套变异函数自动化分解按收益判据诚实拒绝（未发现优于单结构即拒绝）；
- ST 时间相关为单/双参数指数形状；pfafstetter 单级层级；
- chunked_band PF 的 seam 偏差以参考最大填深为界（非逐位一致）；
- SK/KED/SGS/ST 的 CV 不支持（诚实省略，不伪造 CV 指标）；
- oracle 语料新增 7 例（science_v4.json）；KED/SGS/LMC 的全面 oracle
  覆盖列为 follow-up。
