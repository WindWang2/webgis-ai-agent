# DEDUP_MATRIX — G20 排重与所有权

口径：`最新 master(816705218) + 本任务职责 + 其他开放 PR`。状态：
`present`（已有，转兼容回归）/ `partial`（有零件未闭环）/ `missing`（本任务需补）/
`owned-by-other`（兄弟任务，禁止接管）/ `blocked`（外部条件）。

| # | 事项 | 状态 | 证据（源码调用链 / PR 状态） | 本任务处置 |
|---|---|---|---|---|
| 1 | 多波段注册描述子 `RasterArtifactDescriptor` / `inspect_raster_artifact` | present | `app/schemas/raster_spec.py:55-181`；生产写者 `app/lib/geo_analysis/raster_windowed.py:340-359`、`app/services/synthetic_terrain.py:170` | 复用；补单位/scale/offset 与统计口径披露，不改指纹格式 |
| 2 | `RasterStyleSpec` 声明 | partial | `app/schemas/raster_spec.py:239-279`；唯一构造点 `tests/unit/test_raster_spec.py:8-53` | 升级为严格可序列化表达契约并接入生产；保留旧类兼容 |
| 3 | XYZ PNG 瓦片路由（owner/LRU/singleflight/ETag/epoch） | present | `app/api/routes/layer.py:964-1017`；`app/services/mvt.py:1720-1759` | 保留原路由做回归；新样式路由复用同款守卫 |
| 4 | 窗口读 / overview / 跨瓦片全局归一 / RGB+单波段色带 | present | `app/services/raster_tile_service.py:129-186,309-544` | 增量接入 stretch/resampling/opacity；禁止重写渲染器 |
| 5 | 重投影 `Resampling.bilinear` 固定 | partial | `raster_tile_service.py:445-459` | 只把 resampling 参数化，保留 nearest 掩码通道 |
| 6 | 量化 stretch payload（Python/TS parity + golden） | present | `app/services/raster_stretch.py:46-137`；`frontend/lib/map-kit/raster-stretch.ts:62-103`；`tests/cartography/test_raster_stretch_payload.py` | 仅可用于可逆色带预览；不把 uint8 当真实值反推 |
| 7 | 连续/分类/hillshade/bivariate 渲染与图例 | present | `app/services/raster_cartography_converter.py:39,411-550` | 复用图例结构与 `resolve_palette_colors`；不扩张算法 |
| 8 | MapSpec `type:"raster"` image 源 + PNG 服务 | present | `app/lib/cartography/mapspec_schema.py:225-232`；`app/api/routes/raster.py:52-108` | **兼容回归**：旧 PNG 热力图/外部底图行为不得变 |
| 9 | 数值栅格 ref + 规范样式进权威 MapSpec | missing | 全仓无生产消费点（见 BASELINE） | 本任务新增 `numeric_raster` 源子模型 + 层 `raster_style` |
| 10 | 真实像元检视（CRS/行列/原值/mask/单位） | missing | 无 metadata/histogram/probe 路由；`terrain_profile` 是 DEM 纵剖面不是通用像元检视 | 本任务新增领域 probe，不改 terrain |
| 11 | 表达提交 → 持久恢复 → 撤销/重做 → 图例 | missing | `UpsertSourceIntent`/`UpsertLayerIntent` 已在 `app/services/mapspec/intents.py:188-199`；handler `mutation_handlers.py:99-187,264-304`；无任何栅格样式写者 | 复用既有 intent + CAS/锁/checkpoint，不建第二 store/intent |
| 12 | compiler/runtime 数值栅格源编译 | missing | `compiler.ts:74,507-575,881-886`；`runtime.ts:1041-1128` | 局部 additive 分支；同源双层各自派生 source id |
| 13 | 栅格专属工作台 UI | partial | `layer-style-panel.tsx:379-402` 禁用 specBacked 控件；`context-panel.tsx:532` 挂载 | 新增局部子组件，不改全局布局/主题（#2032 所有权） |
| 14 | Pi 可调用栅格表达/像元工具 | missing | `app/tools/__init__.py:11-120` 注册表；`tests/integration/test_pi_stub_llm_journeys.py` stubLLM | 新增独立 tools 模块 + stubLLM 旅程 |
| 15 | 单幅地图同语义输出（PNG/SVG/PDF） | partial | `exporter.ts:2384-2473`；`mapspec-to-svg.ts:806-831`；`mapspec_to_svg.py:1362-1404`；`map.py:230-264` | 冻结修订快照 + 栅格嵌入/离线可读；不重写出版链 |
| 16 | Pi pin / RPC / bridge / extension / 原生工具暴露 | owned-by-other | G12；`.gitmodules` pin v1.5.0；`#2036` Pi-only | 不修改；仅用既有 `@tool` 注册 |
| 17 | capability availability / qualification 生产接线 | owned-by-other | G13；`#2013/#2016` 已合并基础 | 不修改资格政策/调度器 |
| 18 | 持久要素编辑 / 稳定要素 ID / lineage | owned-by-other | G14；`#1873` 已合并 | 不改事务/ID；像元检视用独立状态 |
| 19 | 多画幅 / frame / atlas / PublicationIR frame 字段 | owned-by-other | G15；`mapspec_schema.py:788-800` 已有 frame | 只交付单幅；不改 frame |
| 20 | 要素级标注覆写 / 避障 / label 字段 | owned-by-other | G16；`mapspec_schema.py:454-472` | 不接管 |
| 21 | staged 上传 / 容器清单 / 导入计划 | owned-by-other | G17（本机 worktree 在途） | 消费当前已登记 owner-scoped ref，不改上传协议 |
| 22 | 多格式数据成果导出 / 受权下载 | owned-by-other | G18（本机 worktree 在途） | 只做地图视觉输出，不建成果交付系统 |
| 23 | 字段表达式 / 计算列派生 | owned-by-other | G19 | 不接管 |
| 24 | OD 语义 / 方向流图 | owned-by-other | G21 | 不接管；schema 字段分区（raster vs flow） |
| 25 | 地图简报 / 自动数据刷新 / 全局 UI 重设计 | owned-by-other | 开放 `#2021/#2022/#2032` | 局部入口挂载；冲突段在 INTERFACES.md 登记 |
| 26 | 栅格计算器/重分类/重采样/变化检测/Lakehouse 时间轴 | present | `app/services/spatial_analyzer.py`（CONTEXT.md）、`app/tools/terrain_measurements.py:75` | 不重复算法；表达调整不得触发分析重算 |
| 27 | G09 参数化批量分析 | owned-by-other | 任务书明示排重 | 不做计算编排 |
| 28 | 线上 CI 门禁 | blocked（有意不等待） | 任务书 A：无需等待线上 CI | 保留现有 CI/分支保护；只做本地真实验证 |

## 与开放 PR 的文件冲突面

- `#2032` 改 `frontend/components/layout/context-panel.tsx`、`layers-tab.tsx`、
  `frontend/lib/store/*`、`tailwind.config.ts`、`messages/*/layout.json`。
  本任务**只在 `layer-style-panel.tsx` 内**新增子面板与 `messages/*/hud.json` 新 key，
  不改 `context-panel.tsx`/布局/主题；若必须挂载，仅一行 `<RasterAuthoringPanel/>`
  局部导入（登记在 INTERFACES.md）。
- `#2022` 改 `app/main.py`、`app/tools/__init__.py`（新增 router/tool 模块）。
  本任务同样只在这两个共享文件做**单行 append**，冲突时按最新主干重新校正。
- G15/G16/G21 都可能改 `app/lib/cartography/mapspec_schema.py`。本任务只新增
  `NumericRasterMapSpecSource` / `MapSpecRasterExpression` 两个模型与
  `MapSpecLayer.raster_style` 一个字段，不碰 `MapSpecFrame`、`MapSpecLayerLabel`、
  flow 子模型、`KNOWN_VERSIONS`（见 INTERFACES.md 版本决策）。
