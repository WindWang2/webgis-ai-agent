# DEDUP_MATRIX — 02｜移动对象轨迹、时空事件与地理过程分析

基线: origin/master f34cb69dd (2026-11-27)；开放 PR=[]；开放 issue=[]；远端分支无 trajectory/moving/track 命名。
检索: `grep -rniE "map_matching|mapmatch|moving_object|movingobject|trajectory"` 在 app/ tests/ 前端 src 仅 3 处偶然命中（spatial_regression.py 注释、无关测试），无实质实现。

| 工作包 | 结论 | 证据（present/partial/missing） | 归属 | 剩余增量 |
|---|---|---|---|---|
| WP1 entity-observation 数据合同 | missing | `app/services/temporal/models.py` 只有 TimeInstant/TemporalFilter/聚合桶，无 entity_id 身份链；无 source row ID 不可变版本 | 本线独占（移动对象语义） | 全部 |
| WP2 轨迹构建/质量/运动量 | missing | ST-DBSCAN（`spatiotemporal.py`）是点聚类非轨迹分段；无停留/速度/方向估计；无 map matching | 本线独占 | 全部（地理测量可复用 `app/lib/geo_analysis`） |
| WP3 时空事件/区域交互 | missing | 无 enter/exit/cross/dwell 事件；temporal filter/aggregate 的时间窗语义可复用 | 本线独占（事件语义）；时窗合同复用 temporal | 全部 |
| WP4 轨迹地图表达/联动 | partial | #2266 OD 流图（`app/services/flow_product/`）有方向箭头样式可复用模式；`temporal_view_tools.py` 有 MapSpec temporal 写面；无轨迹线速度编码/停留标记/事件标记 | 本线独占轨迹表达；样式/联动模式复用 #2266/#2285 | 轨迹图层模型、事件标记、实体选择联动 |
| WP5 Pi 工具/工作流 | missing | 工具注册框架完备（`app/tools/registry.py` + `app/tools/__init__.py` + `categories.py`），无任何轨迹工具面 | 本线新增；注册/能力/证据框架复用 | 全部工具面 |
| WP6 科学对照/规模 | missing | `tests/science_oracles/` 模式存在可复用；无轨迹 oracle | 本线新增 | 全部 |

已合并近 PR 对照（#2272–#2289）:
- #2266 OD 流图: OD 聚合两点点对，非实体轨迹 → 不覆盖
- #2277 工作流模型: 参数化工作流 authoring → WP5 可挂接复用，不重写
- #2283/#2280 Pi host/决策: 01 线领域 → 不覆盖轨迹语义
- #2285 多源联动探索: 联动键/selection 语义 → WP3/WP4 复用其键集，不重写
- #2286 任务数据供给: 数据获取 → 不覆盖
- #2287 图符: 静态统计图符 → 不覆盖
- temporal engine 族 (#2265 等历史): 通用时序 profile/filter/aggregate/change/trend/ST-DBSCAN/raster → 排除条款明确不重建；本线只做实体轨迹与事件

结论: 五个工作包均为 missing/partial，本任务有充分增量，无在研冲突分支。
