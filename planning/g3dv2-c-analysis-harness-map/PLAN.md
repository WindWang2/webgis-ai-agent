# PLAN — g3dv2-c-analysis-harness-map

## 使命（非重复定位）

已有 #1773/#1829/#1830 提供了量测内核、SceneSpec 编排与制图组件骨架。本 track 的新增价值是把「分析→上图→交互失效→验收」真正接通并补科学算子：

1. **viewshed 可视几何**（B1）：内核 bool 栅格 → 生产 GeoJSON → 既有 dispatch geojson 通道自动挂层；
2. **run_3d_analysis 全算法编排**（B2）：LOS/profile/surface/volume 接入；
3. **staleness 生产接线**（B3）：观察点漂移/DEM 消失失效、camera-only 不失效，Agent 可观察；
4. **elevation_profile 生产 artifact**（B4）：ref:profile-* 写入方 + 端点白名单 + 组件自动挂接；
5. **cut/fill 土方体积算法**（B5）：解析 oracle 证明；
6. **专业组合模板 2–3 个** + **F27 验收族 ≥12 场景**。

## 里程碑

| M | 内容 | 交付判据 |
|---|---|---|
| M1 | 科学层：app/lib/geo_analysis/viewshed_geometry.py（bool 栅格→GeoJSON 面，含洞/边界/nodata 披露）+ volumes.py（cut/fill，基准面 constant/mean，中点求积对线性面精确） | 单元测试 + 解析 oracle（平面/斜面/立方坑）通过；数值零回归（既有 measurements 测试全绿） |
| M2 | 工具层：viewshed_analysis 补 include_geometry（stats 字段零漂移）；新工具 cut_fill_volume_analysis；profile_ref 铸造缝（dispatch 认 `profile_chart` 键 → ref:profile-* + elevation_profile 组件挂接 + chat_session_views 白名单） | 工具单测 + mapspec wiring 契约测试 + 端点测试 |
| M3 | 编排层：run_3d_analysis 支持 viewshed/line_of_sight/terrain_profile/surface_distance/surface_area/cut_fill_volume（capability 门/垂直门/参数映射/staleness_binding）；staleness 生产消费：binding 持久化 + apply_scene_representation 变更时裁决 + ACK 输出 | 场景单测：拖观察点→stale、camera-only→fresh、DEM 源移除→stale |
| M4 | 制图层：composition_packs/analysis_products.py（mountain LOS 通视/视域遮挡图、沿线剖面联合制图、土方工程地形图）+ conformance 测试 | pack 注册测试 + 组件选择测试 |
| M5 | 验收层：tests/acceptance/gis_agent F27 族 ≥12 场景（正常/无 DEM/垂直基准非法/旧结果失效/3D 不可用/中断/离线/2D 退化/负例）+ g05 pin + CLI 可定位 first failing stage | pytest -m cartography 通过 + pin 更新 |
| M6 | ADR-0276 + 全量本地质量门 + 独立 review→fix→review + PR | ci-local 相关门退出码 0；PR 创建 |

## 依赖顺序

M1 → M2 →（M3 ∥ M4）→ M5 → M6。M3 依赖 M2 的工具面；M4 只依赖 M2 的组件键。

## 关键架构决策预置（详见 DECISIONS.md）

- 不改 tool_dispatch_service 的 geojson 通道语义，只增 `profile_chart` 认可键（沿 #517/uncertainty 先例）；
- viewshed 数值内核零改动，几何提取独立模块（算法计算与几何可视化产物分离）；
- 建筑净空 fail-closed：无可靠 3D building geometry 源 → 不开放净空结论（能力探测 typed unavailable + alternatives）；
- cut/fill 用 cell-center 中点求积（线性面上精确——解析 oracle 可证），不用角点插值发明数据；
- staleness 不新建存储：binding 走 session workspace 已有状态面。
