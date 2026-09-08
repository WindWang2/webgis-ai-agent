# 04 Numerical Risk Map（数值风险图 · 跨域汇总）

> 逐域详表见 domains/*.md §4。本文件只收跨域共性风险与本分支的处置。

## 已处置（本分支）

| 风险 | 域 | 处置 |
|---|---|---|
| 方位×像元联合内存（25-77GB 包络内爆炸） | 地形 openness/horizon/SVF | P0 修复：逐方位累加 O(h·w) + 联合包络 64M azimuth-cells 类型化拒绝 |
| viewshed 无 lib 护栏（~6-8 个 (h,w) 工作数组） | 地形 | 补 _guard_cells |
| indicator 阈值守卫检查字符串长度（合法请求误拒+消息失实） | 地统计 | F1 修复：按阈值个数 |
| kriging scipy_linalg 变体窗口缺失（选择层死亡） | 地统计 | F5 修复：[8,100k]/[100k,500k] 窗口 |
| 坐标约定半像元偏差（contours/viewshed-xy 路径） | 地形 lib | 实测复核定案：GDAL 仿射原点=UL 角点；统一「整数索引=像元中心」 |

## 跨域共性风险（存量防护良好，记录在案）

1. **重复坐标/奇异系统**：kriging 聚合重复点 + ridge 分级稳定化 + 逐行
   LAPACK 回退 + degraded_cells 计数（地统计，低风险）。
2. **零分母/NaN 传播**：光学指数全线 NaN 语义（零分母→NaN 不伪装 0）；
   SAR log 域除零有护栏。低风险。
3. **dB↔linear 转换**：SAR 六处守卫为符号启发式，全正 dB 场可穿透
   （M-，记录；建议后续显式 unit 参数）。中低。
4. **偏态场无变换**：克里金族无 log/normal-score 变换，偏态场可能负预测
   （已 ±3√sill 钳制；P2 计划变换）。中。
5. **大 n 退化**：directional_variogram 大 n 未预抽稀（meta 披露
   n_pairs_kept 可察觉；建议入口统一 stratified_subsample）。中低。
6. **浮点求和顺序**：openness/horizon 重构后 j 升序累加与原 axis=0 归约
   在 n_az≤64 时逐位一致（numpy 单 block 顺序求和）；SVF 测试 atol 1e-12 通过。

## 数值验证体系（Wave 9 锚点）

- oracle 语料：1084 例/12 域（闭式解析解、独立实现复算、手算黄金值三类）。
- golden 套件：tests/unit/gis/test_golden_gis_numerics.py。
- 双跑确定性：descriptor random_seed_policy → 实现（seed=42 置换族被
  逐位测试钉住）。
- 新增算法数值锚要求：合成场解析恢复（rtol 1e-4 级）+ 约束性质 + 守卫。
