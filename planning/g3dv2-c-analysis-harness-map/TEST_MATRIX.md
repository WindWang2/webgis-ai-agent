# TEST_MATRIX — g3dv2-c-analysis-harness-map

## 新增测试（本 track）

| 层 | 文件 | 覆盖 | 关键不变量 |
|---|---|---|---|
| 科学层 | tests/unit/lib/test_viewshed_geometry.py (14) | bool→GeoJSON 提取 | 像元原子性对账恒等式（shoelace = n×\|det\|）；精确世界坐标闭式；环取向 RFC 7946；洞/nodata 排除；有界化确定性；旋转仿射恒等 |
| 科学层 | tests/unit/lib/test_terrain_volumes.py (16) | cut/fill | 平面/斜面/台阶闭式 oracle（<1e-6 m³）；mean 基准 net=0 恒等；分区可加；nodata/边界披露；ft 精确换算；datum 混算拒绝 |
| 工具层 | tests/unit/test_3d_analysis_product_tools.py (9) | viewshed 几何 + cut/fill 工具 | **stats 零漂移**（include_geometry 两模式逐位一致）；GeoJSON 上链前提；证据块算法 id；负例（栅格外/词表/缺参） |
| 缝层 | tests/unit/test_profile_artifact_seam.py (3) | profile_chart→ref→组件→端点 | ref:profile-* 铸造+内联剥离；MapSpec 组件 profileRef；指纹一致；白名单放行/拒绝 |
| 编排层 | tests/unit/gis_harness/scene3d/test_analysis_orchestration_staleness.py (12) | 六算法 dispatch + staleness | LOS/剖面/面积/土方真实 dispatch + MethodUseContract；拖点判旧（场景 ACK）；camera-only fresh（含直接裁决面）；DEM 源消失；prior stale 上行；绑定 FIFO/幂等/损坏剔除 |
| 制图层 | tests/cartography/test_analysis_products_pack.py (10) | 三模板 | 注册/确定性/模型解析/fallback；输出目标诚实（矢量仅 2D 产品）；必填槽 conformance 反证（缺=error）；工厂对账 |
| 验收层 | tests/acceptance/gis_agent F27 (14) | 任务级 E2E | 见 EVIDENCE.md 场景矩阵；pin 61 场景 |

## 修改的既有测试（语义升级，非删断言）

- test_decision_camera_staleness_verdict.py::test_capabilities_pending_directions_typed_unavailable：
  line_of_sight 从「待 G3D03 pending」改为真实探测语义（裸进程 = 工具未注册），
  skyline/scene_asset 保持 pending；
- test_analysis_orchestration_staleness.py::test_options_json_passthrough：
  geojson 断言从 result 内层改到编排层提升位（ADR-0276 D3 产物提升契约）。

## 回归证明（既有零回归）

- tests/unit/lib/test_terrain_measurements.py 25 全绿（四量测数值零回归）；
- tests/unit/test_terrain_measurement_tools.py + test_measurement_mapspec_wiring.py +
  test_terrain_science_vnext.py 36 全绿（viewshed 内核零漂移）；
- tests/unit/gis_harness/scene3d/ 125 全绿；tests/acceptance/gis_agent 61 全绿；
- tests/cartography -m cartography 2060 全绿（golden corpus 5 新案例 + stress 零改写）。

## 未运行/部分运行（诚实披露）

- 全量后端 6-shard + --cov-fail-under=75（CI 面）：本地跑 tests/unit 全量 -n 2 +
  定向族；覆盖率门未本地复现（limitation）；
- perf lane / Playwright E2E：本 track 未触碰 perf 基线文件与 E2E journey；
  前端 vitest 全量后台运行（零前端源码改动）；
- mypy 白名单面（app/core + 3 文件）：本 track 未触碰该面（改动均在其外）。
