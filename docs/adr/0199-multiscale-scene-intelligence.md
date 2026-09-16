# ADR-0199: Multiscale 2D / 2.5D / 3D Cartographic Scene Intelligence

- 状态：Proposed（本 PR）
- 日期：2026-09-17
- 分支：`cartography/multiscale-scene-intelligence-v1`
- 基线：`origin/master` = `faa453a8935101378c23eb6694a42c3616d9c670`
- 关联：ADR-0095（fill-extrusion 通道）、ADR-0154（label.zoomBands）、
  ADR-0155（raster-dem/hillshade 数据面）、ADR-0185（VLM critic 契约冻结）、
  ADR-0186（视觉自愈边界）、ADR-0120（MapSpec 契约权威与 TS 投影）

## Context（问题）

系统此前的"3D 能力"是三条互不相连的孤岛：

1. **fill-extrusion 数据面**：`create_3d_extrusion_map` 工具 → converter 写
   MapLibre paint 表达式 → spec 层渲染。高度来源是要素属性字段，与 DEM 无关；
   证据溯源（这高度哪来的）不可表达。
2. **前端 is3D 布尔**：用户手动开关；`enable3DTerrain` 硬编码 AWS terrarium
   DEM，与 spec/会话数据完全脱节；`adapter.ts` 在 is3D 时把**所有**多边形层
   自动挤出 `coalesce(get height, 20)` —— 无 height 字段的层被**虚构 20m 高度**
   （伪造垂直证据）。
3. **DEM 分析面**：terrain 科学栈只产服务端预渲染 PNG；无任何机制让会话内的
   DEM 证据成为交互式 3D 地形。

没有 SceneIntent/SceneDecision 规划层（目的/数据/媒介 → 2D/2.5D/3D 的决策），
没有确定性场景质量门，没有系统退化链，自愈词汇不含任何 scene 缺陷。

## Decision（决策）

### D1 — 场景模式词表 `2d / 2.5d / 3d`

`2.5d` = 地形/晕渲呈现、要素无垂直挤出；`3d` = 要素垂直挤出（fill-extrusion
高度通道激活）、terrain 可选共呈。词表单源在 `mapspec_schema.SCENE_MODES`。

### D2 — MapSpec v1.4 纯 additive

顶层 `scene`（`MapSceneConfig`：mode/terrain/camera/reason_code/degrade_to/
reduced_motion）+ `MapSpecLayer.extrusion` 类型化（`MapSpecLayerExtrusion`，
吸收 converter 既有开放 dict 键面，新增 `elevation_ref` 垂直证据溯源）。
identity upgrader（1.3→1.4），旧 spec canonical byte-stable（golden 锁定）。
TS 投影经 `ts_projection.py` 再生成（byte 幂等）。

### D3 — terrain 是指针不是第二数据面

`scene.terrain.source` 指向 spec.sources 内的 raster-dem 源 id；悬空引用在
**两道闸**阻塞（`coordinator.validate` 的 `SCENE_TERRAIN_SOURCE_REF` 与
`scene_quality` 的同名 finding —— fail-closed 同口径）。`elevation_ref` 记录
"这份地形来自哪份已核实 DEM 数据"。

### D4 — 会话 DEM → MapLibre terrain 的唯一正门：terrarium 瓦片

`raster_tile_service.render_terrarium_tile`（+ 路由
`GET /layers/data/{ref}/terrain-tiles/{z}/{x}/{y}.png`）。fail-closed：

- 多波段 → `TERRAIN_REQUIRES_SINGLE_BAND`（猜波段 = 伪造高程）；
- 无 CRS → `TERRAIN_REQUIRES_CRS`（地形放错位置 = 伪造证据；可视化路径的
  "警告后假定 3857" 对地形面不可接受）；
- nodata（声明值 / -9999 哨兵 / NaN）→ 透明像素，**绝不编码为 0 高程**；
- 双通道再投影（高程 bilinear + 有效权重 bilinear，阈值 254.5）—— nodata
  边界的插值会产生 -4000m 级"过渡高程"，是伪证据，一律判无效；
- 错误不缓存、不降级为透明瓦片（"无地形"与"证据不可用"语义可分，HTTP 422
  结构化错误码）。

编码纯函数在 `app/lib/cartography/terrain_encoding.py`（terrarium 规范：
`elev = (R*256 + G + B/256) - 32768`；已知答案 + round-trip 测试锁定）。

### D5 — 挤出证据门控（消灭伪造默认高度）

`adapter.ts` 的 is3D 自动挤出改为证据门控：显式 `layer.extrusion` 契约
（已声明的数据语义）或要素数值 height 字段（几何 profile 缓存扫描）。
无证据 → 平面渲染 + 有界证据环登记（`scene_extrusion_no_height_evidence`，
符号律 evidence ring 先例），导出链汇入降级披露。**删除**了
`coalesce(get height, 20)` 伪造路径（旧 pin 测试断言的正是该伪造语义，
按 Oracle 改写为 fail-closed + 两个证据变体）。

### D6 — 场景切换 = presentation 事务

`SetSceneIntent`（lifecycle_engine 分支，classified presentation）：COW 只拷
顶层 `scene` 分支 —— layers/sources/legend_spec/thresholds **构造性不变**，
统计/分级/图例不漂移不靠事后校验靠事务形状。`MapSceneConfig` 严格校验 +
exaggeration 硬界 (0,10]（契约单源 `MAX_TERRAIN_EXAGGERATION`）；非法输入
整笔拒绝，last-known-good 不变。facade `mapspec_store.set_scene`。

### D7 — 确定性场景质量门（VLM 契约零改动）

`scene_quality.py`：封闭词表 9 个 finding code（测试锁定）。blocking（terrain
源悬空/类型错、exaggeration 越界、pitch 越硬上限）/ warning（无证据挤出、
失真夸张、未知垂直单位、空 3d 模式、pitch>60 建议）/ info（失真披露）。
`check_legend_invariance` = 逐层 legend_spec digest 对比（Oracle G1 的确定性
判据）。ADR-0185 的 5 轴 extra=forbid 契约**未动** —— 视觉遮挡等主观轴仍由
VLM 通道，本门只做确定性可判项。

### D8 — 自愈只产生既有 intents

`scene_selfheal.py`：blocking finding → `SetViewIntent`（pitch 钳制）/
`SetSceneIntent`（exaggeration 钳回 1.0 诚实档 / terrain 撤销声明——宁无地形
不伪地形）。intent 类型白名单测试锁定。warning 一律披露不自动改写。

### D9 — 相机与 LOD 是纯函数，落既有承载面

`scene_camera.py`（overview/detail/compare；antimeridian 最短弧；zoom 3-18；
产品 pitch ≤60；compare 同高异向；reduced-motion → transition 0）。
`scene_lod.py`（zoom 分档 → label topRatio / symbol scale / terrain maxzoom /
抽稀预算，单调性契约）：投影到 ADR-0154 `label.zoomBands` 与
`thresholds.maxFeatures` 既有承载面，**不新增 spec 字段**。

### D10 — 前端 spec 驱动 + fallback 永在

`map-panel` 3D 开关读 committed spec 的 `scene.terrain`（exaggeration +
raster-dem 源）与 `scene.camera`；**未声明 scene 时回退既有 AWS terrarium
默认** —— MapLibre fallback 始终可用（`renderer.enable3DTerrain` 既有签名
不变）。`compiler.ts` 把 `scene.terrain` 投影为 MapLibre style `terrain` 对象
（悬空/类型错 → 编译错误），3d 场景 symbol 层默认视口对齐（显式声明永不
覆盖；symbol 布局面透传缺口一并收口）。

### D11 — Agent 面

`plan_map_scene`（只读规划；证据诚实边界：工具不编造证据，has_*_evidence
责任在调用方）+ `set_map_scene`（facade 事务）。注册于 `app/tools/__init__`。

## Consequences

### 正面
- 无证据不伪造：挤出/地形都有证据门（Oracle G2），错误可区分可披露。
- 2D↔3D 切换零分析重跑：presentation 事务（Oracle G1 构造保证 + digest 门）。
- 旧 spec 零行为变化：v1.4 additive + golden byte-stable（Oracle G3）。
- fallback 永在：无 scene → 全部旧路径（Oracle G5）。
- 性能有界：规划 O(1)、门禁线性、编码矢量化；perf 标记基准（Oracle G6）。

### 负面 / 已知边界（诚实披露）
- **无垂直基准转换**：单位 = 米（`vertical_unit: "m"`）；EGM96/椭球高换算
  不存在，混源 DEM 的基准假设由 methodology 层披露（与既有 ADR 同口径）。
- **`layer.extrusion` 类型化收紧披露语义（接受）**：v1.4 前 `extrusion` 是
  unknown 开放键（`valid=True` + unknown 披露）；类型化后缺 `height_field`
  的畸形 extrusion 字典 → `valid=False`（invalid 披露）。生产写入面只有
  converter（恒带 height_field），影响面 = template_codegen_evaluator 对
  畸形历史 spec 的评分与披露种类 —— 这正是"开放面收口"的既定代价
  （显式披露优于静默容忍，ADR-0120 R1-C2 同哲学）。
- terrarium 瓦片仅服务**会话内** DEM ref；外部 raster-dem URL（含 AWS
  fallback）原样透传给 MapLibre，不代理。
- is3D 手动布尔保留（交互自由度）；scene 是 desired state，两者并存时
  scene 提供源/夸张参数、布尔提供开关意图。
- 自愈的 registry 级接线（`selfheal_actions.py` 动作表 + ADR-0167 策略学习）
  是后续 PR：当前 `scene_selfheal` 产出的是可提交 intents，编排面待接。
- exporter 的 terrain 场景仍是栅格位图；SVG 侧既有
  `3d_perspective_not_vectorized` 披露不变。

## Verification（本 PR 的证据）

- 后端新增 9 个测试文件 + 1 个 perf 基准（~120 用例），全部绿；
- 前端新增 scene-compiler.test.ts + adapter 证据门控用例，compiler 232 /
  runtime 212 / exporter 84 全绿；
- 既有回归：golden corpus、whatif（pin 更新为 LATEST-relative）、TS 投影
  契约、raster tile、extrusion、sidecar 诊断全绿；
- perf（marker=perf）：规划 <5ms、1k 层门禁 <50ms、1k² 编码 <40ms（本地
  基线，仅回归棘轮用途，非普适 SLO）。
