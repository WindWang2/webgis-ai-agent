# 0306. 移动对象轨迹、时空事件与地理过程分析（moving_object 域）

**Date:** 2026-11-28
**Status:** Proposed
**Branch:** `zcode/goal-moving-object-events-20261127`
**Baselines:** master `f34cb69dd` · ADR-0270（大数据面 ref 纪律）· ADR-0284（typed artifact envelope）· ADR-0099 §34（算法/能力域包）· G21（flow 产品/原子 apply 先例）· MapSpec 2.2 additive 链

## Context

方向 02 需要的是**有实体身份、采样时间与移动语义**的轨迹与区域事件分析：
导入车辆/巡查/生态观测点表，构建可解释轨迹（分段/速度/驻留），识别区域
enter/exit/cross/dwell 事件，并以地图+事件表双表达交付。既有 `TemporalEngine`
族只提供通用时序 profile/filter/aggregate/change/trend/ST-DBSCAN/raster——
**无实体身份链**，OD 流图（#2266）是两点聚合而非实体轨迹。启动审计确认
`map_matching`/`moving_object`/`trajectory` 全仓零实质命中，五工作包均为
missing/partial（见 planning/GOAL-20261127-02-*/DEDUP_MATRIX.md）。

## Decisions

1. **独立领域包，不扩写 TemporalEngine**。`app/services/moving_object/`
   （contracts/ingest/motion/builder/dwell/quality/events/oracles/service/
   mutation/presentation）独占移动对象语义；temporal 域只读复用其时间语义
   先例。域包与能力包按 ADR-0099 §34 各自成模块注册。
2. **科学诚实是硬合同**（contracts.py 顶部即声明）：
   - 距离/方位一律 WGS84 测地（pyproj.Geod）；度永不当作米；
   - 速度/方向是**相邻观测间的区间估计**，dt=0 对 → `speed=None`+degenerate
     （无假零、无 inf）；same-time 不同位置=数据矛盾 → `zero_dt_conflict`
     断点（可溯源到两端 source_row_id）；
   - enter/exit 时刻**只给观测区间**（`inferred=true` + t_interval_*），绝不
     伪造精确时刻；两端皆在区域外的观测段若真穿过区域 → `cross`（同样只有
     区间）；首修复已在内 → 区间起点为 None 的披露式 enter；
   - 边界规则 closed（covers，边界算内）/ open（contains）显式声明并随结果
     披露；洞/多面为 shapely 原生语义；
   - 反经线：段解绕 ±360，区域按三帧（0,±360）bbox 预筛后做 prepared 谓词；
     经度跨幅 >180° 的区域 typed 拒绝（要求预切分）；
   - 驻留=锚定首修复径内连续游程（半径/时长/点数全显式），漂移拆分如实
     披露且**不自动合并**；near-miss 游程计数披露。
3. **轨迹身份 = entity_id 稳定字符串**，数组索引仅作稳定排序 tie-break
   （`order`），永不为身份；source_row_field 缺省派生 `row:<input position>`
   并在诊断 notes 披露（输入版本不可变前提下稳定）。
4. **原始点不可变**：清洗（quality.propose_cleaning）只给有界预览，应用=
   以合并配置重摄取到**新 ref**；诊断与清洗建议分属两个函数面。
5. **双入口单一引擎**：REST `/chat/sessions/{sid}/trajectory/{inspect,build,
   apply,query}` 与 Pi 工具面（trajectory_inspect/build/apply/query）都经
   `service.py`；J4 旅程测试断言 ToolDispatchService 与 service 直调产生同
   product_ref/同指纹/同科学证据。
6. **产品=ref 家族 + 一笔 CAS 事务**（G21 同构）：产品存 `ref:traj-product-*`，
   轨迹线/驻留/事件 FC 存 `ref:geojson-*`；`ApplyTrajectoryProductIntent` +
   registry descriptor + Body codec 进既有 mutation 单点登记（一致性门禁与
   parity corpus 同步扩充）；apply 校验 family/源 revision（STALE 拒绝）/
   ref 活性，任一拒绝整族不落图（无半成品）。
7. **MapSpec 2.3 纯 additive**：`layer.trajectory`（MapSpecLayerTrajectory.v1）
   缺失=层走既有表达路径；upgrader 为 identity；types.generated 再生成。
8. **表达诚实**：速度编码域=实测 p05..p95（km/h，图例注明区间估计语义）；
   退化域（单速度）→ constant paint（interpolate stops 需严格递增——J2 旅程
   测试抓到该真 bug）；零路径段不画（计数披露）；驻留半径为显示标注而非
   分析真值（分析阈值随参数与驻留要素属性走）。
9. **规模纪律**：MAX_INPUT_ROWS 50 万 / MAX_ENTITIES 2 万 / MAX_EVENTS 10 万
   / MAX_TRAJECTORY_SEGMENTS 10 万 全 typed 拒绝；区域谓词 O(1) bbox 预筛
   （100k 修复 events 11.6s→0.36s，见 tests/unit/moving_object/test_scale.py
   实测输出）；LLM 上下文只见有界 envelope（ref-first）。

## Consequences

- 轨迹/驻留/事件全部可复现：确定性 oracle（公布测地常数 + 合成公交/跨日界
  线/零时冲突/噪声驻留 fixture）+ 指纹缓存 + deterministic=true 声明。
- 不做地图匹配（network 图时间能力未具备即如实不做；观测轨迹保留为权威），
  不做插值补齐，不做实时监控——这三点是显式排除项。
- 已知限制（随结果披露）：区间多次进出不可分辨（采样假设）；谓词为
  plate carrée 拓扑约定（与测地运动量并行披露）；unknown 谓词失败计数。
- 后续可扩：map matching（需合格路网时间拓扑后走有界候选+时空一致性）、
  清洗应用工具化、区域级时间对齐钻取（复用 #2285 传播键）。
