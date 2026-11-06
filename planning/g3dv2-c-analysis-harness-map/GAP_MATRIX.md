# GAP_MATRIX — g3dv2-c-analysis-harness-map

C0 审计断链（BASELINE.md B1–B6）→ 本 track 清偿状态：

| # | 断链 | 状态 | 落点 |
|---|---|---|---|
| B1 | viewshed stats-only 无几何 | **已闭合** | viewshed_geometry.py + 工具 include_geometry（stats 零漂移）+ dispatch geojson 通道自动挂层；F27-02 |
| B2 | run_3d_analysis 仅 viewshed | **已闭合** | 六算法表驱动 + options 透传 + 产物提升；F27-01/05/13 |
| B3 | evaluate_staleness 零生产调用 | **已闭合** | analysis_binding_store + 三个生产消费方（run 前 ACK/场景 ACK/直接裁决面）；F27-03/04/14 |
| B4 | elevation_profile ref 双重断链 | **已闭合** | profile_chart 认可键 + 端点白名单 + 组件工厂 + patch_component 挂接；F27-01/12 |
| B5 | 无体积算法 | **已闭合** | volumes.py 中点求积 + 解析 oracle；F27-05 |
| B6 | orchestrator/verdict 仅测试消费 | **部分** | 编排表已消费（dispatch 面）；verdict 双门仍由 acceptance projections 消费（#1829 设计面，display 门依赖渲染观察，留待后续渲染观察链） |

## 任务书验收标准对照

| 标准 | 状态 | 证据 |
|---|---|---|
| 图层+剖面+ACK+合规导出 | ✅ | F27-01/02/12/14（fill/line 层 + elevation_profile 组件 + stale ACK + export.failed_aspects=[]） |
| 拖点判旧 / camera-only 不误判 | ✅ | F27-03/04/14 + 单测直接裁决面 |
| ≥1 新算法解析 oracle | ✅ | cut/fill 16 例（平面/斜面/台阶闭式 <1e-6 m³） |
| LOS/profile/viewshed 零回归 | ✅ | 既有 25+36 例全绿 + stats 逐位一致断言 |
| 多轮任务完整可重复 | ✅ | F27-14 五轮旅程 + F27-01/10/13 |
| 无真 DEM → 替代/blocked 不假装 | ✅ | F27-06 typed decline + alternatives；capability 探测同口径 |
| ≥12 任务级场景 | ✅ | F27 = 14（pin 61 总量） |
| CLI 定位 first failing stage | ✅（复用） | scripts/g05_task_acceptance.py run/report 原生支持（47→61 场景增量即 F27） |

## 遗留（P3，有证据不阻塞）

- skyline 算法（honest pending，decline + alternatives）；
- 建筑净空（数据不具备，fail-closed）；
- verdict 双门的运行时渲染观察消费（B6 部分——依赖 B 方向 observation 面）；
- LOS 遮挡物（建筑/线状设施）几何遮挡分析（需 3D building geometry 源）。
