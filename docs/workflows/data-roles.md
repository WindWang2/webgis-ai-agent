# 数据角色词表（Data Role Vocabulary）

一个 GIS 任务往往不是一个数据源。数据角色（`DATA_ROLES`，稳定契约）让
recipe 声明「这类工作流需要哪些数据、谁供给、缺了怎么办」，并随
Analysis Graph / SessionPlan evidence 可追踪。

## 词表（15 个，值不复用为其它含义）

| role | 语义 | 典型供给 |
| --- | --- | --- |
| `subject` | 分析主体（POI / 要素 / 栅格主题） | poi_query / raster_source |
| `measure` | 被分析的数值变量（统计/检验的字段载体） | admin_aggregation / od_matrix |
| `boundary` | 行政/自然边界（聚合与分区制图的空间骨架） | admin_boundary_query |
| `denominator` | 归一化分母（人口/面积/单元总数） | data_fabric / profile 字段 |
| `network` | 路网/管网等网络数据 | poi_query（network_graph） |
| `elevation` | DEM / 高程 | raster_source |
| `hazard` | 危险源/致灾因子 | raster_source / geometry_buffer |
| `receptor` | 承灾体/受体 | data_fabric |
| `population` | 人口（可为主体也可为分母） | data_fabric |
| `criteria` | 决策准则因子 | user_upload |
| `constraint` | 约束/排斥因子 | user_upload |
| `reference` | 参考底图/辅助参照（观察点、出口断面等） | point_profile |
| `baseline` | 基期数据（变化/趋势） | raster_source / temporal_profile |
| `target_time` | 目标期数据/时间维度 | raster_source / temporal_profile |
| `comparison_time` | 对比期数据 | poi_query / raster_source |

## 缺失策略（missing_policy）

- `block`：必选角色缺失 → `data_blockers` → 完成契约 data 维不满足 →
  verdict **BLOCKED_BY_DATA**（诚实阻断，产品不得带伪结论出门）。
- `degrade`：缺失 → 降级路径 + 强制披露（`degrade_disclosure`）→
  `FallbackDecision(downgrade_class=…, disclosure=…)`。

## 获取通道（acquisition）

- `local`：会话内/本地数据，capability_hint 供给；
- `data_fabric`：数据目录/服务，规划期**不可证伪** → status=`external`
  （不假设存在也不假设缺失，由完成契约 data 维追踪）；
- `user_upload`：必须用户提供（同 external 语义）；
- `derived`：可由其它 capability 派生。

## 红线（由角色表达、由测试锁定）

- `denominator` 缺失 → 禁止 per-capita / rate / equity 结论
  （`EQUITY_MISSING_DENOMINATOR`）；
- `receptor` 缺失 → 产品只能称 hazard，不得称 risk
  （`RISK_RECEPTORS_UNCONFIRMED`）;
- `criteria` 未声明 → 选址不得静默编造权重
  （`SITE_SELECTION_CRITERIA_UNDECLARED`）；
- `must_not_guess=True`（默认）：模型不得编造该数据。
