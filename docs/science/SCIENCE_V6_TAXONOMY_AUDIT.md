# Science V6 Taxonomy 审计（自动生成 · Goal 07 Phase A）

> **本文件由注册表生成，请勿手工编辑。** 事实源：
> `app/lib/gis/algorithms/`（算法 descriptor 的 category /
> algorithm_family / uncertainty_outputs）。再生成：
> `python scripts/science_taxonomy_audit.py`；`--check` 模式 diff。
> 族对齐词表唯一维护于脚本内 `TAXONOMY_FAMILIES`。

注册表算法总数：**213**。

| 族 | 算法数 | native | VALIDATED/PRODUCTION | 代表算法 |
|---|---|---|---|---|
| 栅格 | 4 | 4 | 4 | `raster.algebra`, `raster.reclassify.rule`, `raster.resample.grid` |
| 矢量 | 9 | 9 | 5 | `geometry.buffer`, `geometry.convex_hull`, `geometry.multi_ring_buffer` |
| 地形 | 18 | 18 | 18 | `terrain.aspect`, `terrain.contours`, `terrain.curvature` |
| 水文 | 16 | 16 | 16 | `terrain.breach`, `terrain.dinf_flow`, `terrain.flow` |
| 空间统计 | 36 | 36 | 36 | `point_pattern.cross_k`, `point_pattern.cross_pcf`, `point_pattern.dbscan` |
| 插值 | 21 | 21 | 21 | `interpolation.block_kriging`, `interpolation.cokriging`, `interpolation.cokriging_lmc` |
| 网络 | 19 | 19 | 16 | `network.accessibility`, `network.centrality`, `network.closest_facility` |
| 变化检测 | 7 | 7 | 7 | `remote.change.raster`, `remote.cva`, `remote.mad_change` |
| 遥感 | 31 | 31 | 28 | `remote.band_correlation`, `remote.ica`, `remote.linear_unmixing` |
| 时间序列 | 10 | 10 | 10 | `temporal.aggregate`, `temporal.change`, `temporal.cube_stats` |
| 生态 | 0 | 0 | 0 | — |
| 多准则决策 | 1 | 1 | 1 | `decision.mcda.wsm` |
| 空间抽样 | 0 | 0 | 0 | — |
| 不确定性（横切） | 53（横切） | — | — | 声明 uncertainty_outputs 的算法全体 |

## 族对齐约定

- 对齐键 = descriptor.category（精确）∪ algorithm_family（前缀），按 `raster → vector → terrain → hydrology → spatial_statistics → interpolation → network → change_detection → remote_sensing → time_series → ecology → mcda → sampling ` 顺序首中（互斥；change_detection 的 family 前缀先于
  remote_sensing / time_series 的 category 匹配）；
- Uncertainty 是横切族：`uncertainty_outputs` 非空即计入，成员与
  原族重复计数；
- platform / data_access / flow_analysis 为平台面，不入矩阵
（未对齐且非平台的算法 0 个，见下）。

