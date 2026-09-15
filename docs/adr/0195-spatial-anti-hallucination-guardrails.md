# ADR-0195: 空间反幻觉与地理红线安全守护引擎（spatial guardrails v1）

- 状态：Proposed（随 `agent/11-spatial-anti-hallucination-guardrails` 分支评审）
- 日期：2026-09-15
- 关联：ADR-0104（Workflow V4 / 工具证据链）、ADR-0058（MapSpec CAS 突变事务）、
  ADR-0078（cartography findings 结构化发现）、方向 11 任务书
  （agent-swarm/11-spatial-anti-hallucination-guardrails）

## 背景

空间智能体与通用文本智能体的本质差异在于：**地理真实性（Geographic Truth）
不可容忍幻觉**。文本幻觉产出的是错误措辞，空间幻觉产出的是"物理上不存在的
世界"——错误坐标会直接落图、落分析、落产品交付。

Phase 0 勘察确认 master @ 3eb2cc6a 的四类高频空间幻觉路径均无前置守卫：

1. **经纬度倒置（Lat/Lon Inversion）**：LLM 在生成/清洗坐标时混淆
   `[lng, lat]`（GeoJSON 8415 标准）与 `[lat, lng]`（部分国内地图源习惯），
   北京的点落进索马里海域或南极洲。现有 `argument_normalization`
   （`app/tools/argument_normalization.py`）只做参数形状修复，不做地理
   真实性判定；
2. **陆地掩膜违背（Land/Water Mask Violation）**：Agent 凭空捏造点位时把
   学校、医院、陆上道路放进湖泊中心、公海、水库；
3. **虚构行政区划（Fabricated Administrative Divisions）**：编造不存在的
   6 位行政区划代码（如 `999999`、`190000`）或错误省市归属，下游统计
   聚合、属地路由随之污染；
4. **越权空间越界（Geofence Redlines 缺失）**：工具调度面对"对某敏感
   区域发起超高精度抓取/超大 bbox 分析"没有任何范围围栏。

两条注入路径均已在 master 定型且是唯一事实源：

- 工具调度：`app/services/tool_dispatch_service.py::ToolDispatchService.dispatch`
  （两条 agent 路径——legacy 引擎与 Pi 桥接——共用的单一拥有者）；
- MapSpec 突变：`app/services/mapspec/lifecycle_engine.py::apply_mutation`
  （分布式锁 + CAS + 回滚全事务）。

## 决策

### D1 — 独立纯函数引擎：`app/services/spatial_guardrails/`

新建独立模块，核心全部为**纯函数 + 进程内惰性单例数据资产**：

- 不引入任何网络请求（离线可判定）、不引入 numpy/geopandas 等重依赖
  （与 heavy 依赖解耦，单测可在无重依赖环境运行）；
- 不修改既有工具实现语义：守护网关是**前置管道**，不是工具内部补丁；
- `SPATIAL_GUARDRAILS=0` 一键全局关闭（与 `GIS_ANALYSIS_REUSE=0` 同款
  kill switch 惯例），关闭时两条注入路径零行为差异。

### D2 — 四级拦截层级（拦截不判定业务，只判定物理真实性）

| 层级 | 名称 | 判定内容 | 典型证据 |
| --- | --- | --- | --- |
| L1 | `L1_FORMAT_CRS` | 数值类型、经纬度值域、GeoJSON 结构、坐标轴顺序硬信号 | `lat=116.4`（\|v\|>90）；非数坐标 |
| L2 | `L2_GEOGRAPHIC_BOUNDS` | 世界 bounds 合理性、海陆位置、红线围栏 | 点距最近陆地 > 置信海区余量；命中禁抓围栏 |
| L3 | `L3_LANDMASK_PLAUSIBILITY` | 设施类型与地表常识匹配 | "学校/医院" POI 落深海；落大型湖泊水域 |
| L4 | `L4_TOPOLOGY_CONSISTENCY` | 线/面几何自洽性 | 线要素连续顶点间 > 阈值"瞬移"跳变；退化重复顶点面（自交环修复归 quality gate） |

层级是**证据标签**而非串行流水线：一次校验按几何形态各取所需
（点要素走 L1→L2→L3，线/面要素走 L1→L2→L4），verdict 携带触发层级集合。

### D3 — 三种防御策略模式与判级矩阵

- `BLOCK`：硬阻断。调用/突变被拒绝，返回结构化错误 + correction_hint，
  绝不产出物理上不可能的数据；
- `AUTO_FLIP`：置信度自适应纠偏。仅用于经纬度倒置自愈：硬信号
  （值域矛盾）→ 置信度 1.0 直接翻转；软信号（翻转前后海陆证据对比）
  → 按置信度分档，高置信翻转并打标，低置信仅告警不翻转；
- `WARN_DEGRADE`：降级警示。疑似但不确凿（如中国境内 GCJ02 偏移、
  掩膜边缘带、结构合法但未收录的行政区划码）→ 放行 + 结构化 warning，
  证据进入 `guardrail_findings`。

判级红线：**宁可 WARN 放行，绝不 BLOCK 错杀真实位置**。粗粒度掩膜的
边缘不确定带（默认距陆地 150km 内）一律不判 BLOCK。

### D4 — 经纬度倒置检测：硬信号 + 软信号置信度融合

`latlon_inversion_detector.detect(pair)`：

1. **硬信号**：`|a|>90 ∧ |b|≤90` → a 不可能是纬度，判定输入为
   `[lng, lat]` 本序（若调用方语境声明 lat-first 则翻转），置信度 1.0；
   `|a|>180` → a 不可能是经度，若 `|a|≤90` 则判定发生了 lat-first 输入，
   置信度 1.0；
2. **软信号**：两个解释（原序 / 翻转序）分别过陆地掩膜评分：
   `score = f(land≈1.0, coastal≈0.5, deep_ocean≈0.0)`，叠加值域余量。
   原序 deep_ocean 且翻转序 land，且距离差超阈值 → 判定倒置，置信度
   由海陆证据强度插值（0.6–0.95）；
3. 置信度 ≥ `AUTO_FLIP_MIN_CONFIDENCE`（默认 0.60）→ 输出
   `corrected=[lng, lat]` 并打 `auto_flipped=True` + 原值留痕（evidence），
   供上游审计与 LLM 回显；低于阈值 → WARN_DEGRADE。

### D5 — 离线超轻量陆地掩膜：嵌入式粗粒度多边形 + bbox 空间哈希

- 数据：`app/services/spatial_guardrails/data/world_landmask.py` 内嵌
  全球主要陆块（各大洲 + 主岛约 30 个多边形）与主要大型水体
  （里海、五大湖、贝加尔湖、青海湖等）粗粒度简化多边形（顶点合计
  数千级，纯 Python 列表，无外部文件、无网络）；
- 索引：10°×10° bbox 空间哈希网格，查询 O(1) 定位候选多边形，
  候选集内 ray-casting 点在多边形判定 + 顶点/边距近似测距；
- **三区制**消除假阳性：`land`（多边形内）/ `ocean_confident`
  （距最近陆地 > OCEAN_CONFIRM_KM，默认 150km）/ `coastal_band`
  （其余）。只有 `ocean_confident` 上的陆上设施类型才触发
  `GeographicImpossibilityError` 硬阻断；`coastal_band` 一律
  WARN/不评估；
- 精度声明：粗粒度掩膜只回答"是否在开阔水域深处"，不回答海岸线
  级问题。海岸线级校验留给未来接入高精掩膜（数据源接口已预留
  provider 抽象）。

### D6 — 行政区划校验：GB/T 2260 结构规则 + 内嵌表 + 模糊修正

- 第一道防线（纯结构，零数据依赖）：6 位数字形态、省级前两位必须
  命中法定省级行政区集合（11–82 中的 34 个）、市级段/县级段非零。
  `999999`/`190000`/`000000`/`1234` 等在此被截获；
- 第二道（内嵌表）：省级行政区全量 34 条（代码/名称/中心点/bbox）
  + 地级市全量内嵌表（`data/admin_divisions.py`）。码在表中 → 校验
  归属关系（给定 claimed_parent 省市是否一致）；码不在表但结构合法
  → WARN_DEGRADE（县级码体量大且年年调整，未收录 ≠ 虚构）；
- 模糊容错：名称→代码（含简称/别名，如"深圳"→440300）、近似码修正
  （编辑距离 ≤2 时给出 suggestion 而非直接改写）、归属纠偏
  （claimed_parent 错误时返回正确上级）。

### D7 — 挂载点：两条唯一事实源路径的前置管道

- **工具调度**：`ToolDispatchService.dispatch` 在去重检查之后、分析
  复用（1.5 节）之前插入 `guardrail_middleware.check_tool_args`：
  BLOCK → 返回 `status="error"` 的 `ToolDispatchResult`（llm_payload
  携带证据与修正建议，不执行工具）；AUTO_FLIP → 就地改写
  `tc["function"]["arguments"]` 后照常调度（dedup key 保持原参——
  同样的倒置参数重复提交同样被纠偏，语义幂等）；
- **MapSpec 突变**：`lifecycle_engine.apply_mutation` 入口（取锁前）
  插入 `guardrail_middleware.check_intent`：对 `SetViewIntent.center`、
  `UpsertLayerIntent.layer/source_data`（GeoJSON features）、
  `InitProjectIntent.view` 做全层校验。BLOCK → 返回
  `MapSpecResult(is_error=True, error_msg, correction_hint)`
  （不占锁、不推进 revision）；AUTO_FLIP → 以修正后的 intent 替换
  原 intent 进入事务（突变留痕进 warnings）；
- 两个挂载点共享同一引擎与同一 verdict 结构，观测统一走
  `guardrail_findings`（对齐 ADR-0078 findings 词汇）。

### D8 — Geofence 红线与抓取预算

`redlines.py` 提供两层围栏：

- **区域围栏**：内嵌/可配（env 指向 JSON）敏感区域多边形清单 +
  动作策略（`no_fetch` / `coarse_only`）。工具参数中出现的 bbox/
  半径与围栏相交 → 按策略 BLOCK 或降级；
- **预算围栏**：单次请求 bbox 面积/缩放精度上限（默认可配），
  超限 → BLOCK 并提示缩小范围。默认策略保守（仅明显滥用拦截），
  具体清单为运维配置而非代码内嵌敏感目标。

### D9 — 性能预算与可观测

- 单次坐标校验（含倒置检测 + 掩膜）预算 **< 5ms**（p95）；空间哈希
  网格与行政区划表在进程内惰性构建一次；
- 每次拦截/纠偏产出结构化 verdict（层级、策略、证据、耗时）， BLOCK
  事件计数暴露给 `tool_metrics` 既有采集面（后续面板化）。

## 影响

- 新增 `app/services/spatial_guardrails/`（9 个文件，含数据资产）；
- `tool_dispatch_service.dispatch` 增加一个前置校验分支（kill switch
  可关闭）；`lifecycle_engine.apply_mutation` 入口增加锁前校验；
- 新增单测 `tests/unit/test_spatial_guardrails.py`（TDD，先于实现
  编写），覆盖 20 组倒置样本、深海建筑阻断、虚构行政区划截获、
  完全离线守护（socket 封禁下全功能可用）、性能预算；
- 无 schema/迁移/前端变更。

## 风险与回滚

- **误杀真实位置**（最高风险）：三区制 + 边缘带不 BLOCK + AUTO_FLIP
  仅高置信触发 + kill switch 四层缓解；掩膜数据带版本号与精度声明，
  误报案例回填样本库；
- **行政区划表时效**：结构规则层不依赖数据时效；表数据带
  `edition` 字段，未收录码降级 WARN；
- **GCJ02 偏移**：中国境内偏移 ~百米级，远小于 150km 海区置信余量，
  不影响海陆判定；不做坐标系猜测（L1 只报值域证据，不判 CRS 转换）；
- **回滚**：`SPATIAL_GUARDRAILS=0` 即回到 master 行为；两处挂载点
  均为单向可摘除的前置调用，无状态残留。
