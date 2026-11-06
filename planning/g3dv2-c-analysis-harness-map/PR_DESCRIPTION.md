# feat(g3dv2-c): 三维科学分析 → AI GIS Harness → 智能制图产品闭环

## 问题证据（C0 审计，file:line 见 planning/g3dv2-c-analysis-harness-map/BASELINE.md）

#1773/#1829/#1830 之后三维能力以零件形态存在，产品链六处断链：

1. `viewshed_analysis` stats-only——内核 bool 栅格在工具层被丢弃，视域无图层；
2. `run_3d_analysis` 仅编排 viewshed（decline 码自认 `SCENE_CAP_ALGORITHM_PENDING_G3D03`）——#1773 量测与 #1829 编排互相没接；
3. `evaluate_staleness` 纯函数零生产调用——拖点判旧/camera-only 不判旧未闭环；
4. `elevation_profile ref:profile-*` 双重断链——无生产写入方 + 端点白名单拒绝 + 组件工厂缺失；
5. 无土方体积算法；无建筑高度数据 → 净空 fail-closed；
6. 编排层嵌套 dispatch 产物不进外层缝。

## 与已合并 PR 不重复

- #1773 四量测数值内核**零改动**（25 例回归 + stats 逐位一致断言钉住）；
- #1829 的 SceneSpec/cue/相机 user-wins/能力快照结构保留（LOS 从 typed-pending 升真实探测）；
- #1830 的三场景模板不动，本 PR 新增三个**分析产品**模板（analysis_products.py）；
- A 方向 #1835（DEM 供给）文件面零触碰；B 方向 scene-runtime（前端内核/observation/render_work_corpus）零触碰。

## 已知 issues 避让矩阵

| issue | 处理 |
|---|---|
| #1797 terrain 水文 DoS | 不抢修；新算子自带有界窗口/checkpoint |
| #1805 classify_crs | 不替修；米制换算走 cos-lat 既有政策 + CRS 无关整数记账 |
| #1809–#1811/#1821–#1827 | 仅兼容验证，零触碰 |

本 PR 吸收的既有欠账（additive 记账，非重叠修复）：openapi.json 缺 #1830 KeyframeScene schema（再生）；migration_watermark 98→99（0099_story_map_assets 为 #1830 迁移）；tool_dispatch `_parsed` 未初始化（E2E 显形的登记路径恢复）。

## 真实架构（ADR-0276）

- **科学层**：`viewshed_geometry.py`（cell-edge 整像元归并、CRS 无关像元原子记账、有界多边形化）+ `volumes.py`（cut/fill 中点求积——像元中心是唯一观测，≤1 次表面解析精确）
- **工具层**：viewshed_analysis v2 `include_geometry`（stats 零漂移）+ `cut_fill_volume_analysis`（site/挖/填分区 GeoJSON）+ 算法注册/参数契约/G03 对账面
- **缝层**：`profile_chart` 认可键 → `ref:profile-*` → elevation_profile 组件 upsert → 端点白名单；`mutate_component` points↔profileRef 互斥；组件工厂补 elevation_profile/scene_disclosure
- **编排层**：run_3d_analysis 六算法表驱动 + options 透传（目标工具契约为权威）+ 产物提升（geojson/profile_chart hoist——薄包装纪律）+ 绑定登记/旧绑定对账 ACK
- **失效裁决**：analysis_binding_store（会话状态面，FIFO ≤8）+ 三生产消费方；选择检查收窄到观察者语义目标；登记时戳选择上下文
- **制图层**：mountain_los_obstruction / route_profile_joint（纯 2D 矢量合法）/ earthwork_cut_fill（methodology_note 必填）
- **验收层**：F27 族 14 场景 + MAPSPEC_SOURCE 验收步 + scene3d_analysis 证据面

## 设计取舍（DECISIONS.md 摘要）

- 像元原子记账：地理 CRS 度² shoelace 永不冒充 m²（1e10 倍错误防线）；
- 建筑净空 fail-closed：无可靠 3D building geometry 源不开放（不编造白模高度）；
- skyline honest pending：算法未实现不假装可做；
- route_profile 模型收窄 graduated_line：不以 terrain×pdf 键抢既有报告模板（golden stress 零改写证明）。

## 变更清单

- 新增：app/lib/geo_analysis/{viewshed_geometry,volumes}.py；app/services/gis_harness/scene3d/analysis_binding_store.py；app/lib/cartography/composition_packs/analysis_products.py；docs/adr/0276；tests ×7 文件；planning/g3dv2-c-analysis-harness-map/*
- 扩展：measurements 无改动；terrain_analysis.py（工具面）；scene_orchestration_tools.py；tool_dispatch_service.py；chat_session_views.py；components.py；staleness.py；capabilities.py；algorithms/terrain.py；fixtures_data/contracts/runner/projections/corpus（验收基建）
- 再生：openapi/api_contract_fields 快照、generated-artifacts 账本、golden corpus（5 新案例）、catalog docs、g05 pin（47→61）

## 测试与结果（2026-10-28，16C/62G 本地）

- 新增 64 测试（科学 30/工具 9/缝 3/编排 12/pack 10）+ F27 14 场景
- 回归：measurements 25 / 工具 36 / scene3d 125 / acceptance 61 / cartography 2060 / coordination 32 / contract 18 全绿；tests/unit 全量与前端 vitest/typecheck/eslint 见 EVIDENCE.md
- 命令：`pytest tests/acceptance/gis_agent -m cartography`、`pytest tests/unit/gis_harness/scene3d -q`、`pytest tests/cartography -m cartography` 等（完整清单 EVIDENCE.md）

## 诚实局限

- F27 用确定性合成 DEM（simulated 披露）——真实供给管线归 A 方向 #1835（合并后 raster_path 参数面无缝换源）；
- --cov-fail-under=75 全量覆盖率门与 perf lane 未本地复现（CI 面）；
- verdict 双门的运行时渲染观察消费仍走 acceptance 投影（依赖 B 方向 observation 面演进）；
- origin/master 既有 ruff 3 处（scene_quality E741 等，git diff 证明非本分支引入）。

## 回滚

单分支 revert 即可（无迁移、无破坏性数据变更；快照/账本再生可逆——revert 后重新再生）。profile 白名单/认可键均为 additive。

## 安全/许可/准确性

- 端点白名单扩展仅放行 ref:profile- 前缀（session 所有权校验复用 require_owned_session）；无新 URL/外部服务/密钥；
- 全部数值披露 CRS/垂直基准/单位/nodata/分辨率/方法（MethodUseContract 摘要随编排结果上行）；
- 合成数据永久 simulated 标注。

## 独立 review 结论

见 planning/g3dv2-c-analysis-harness-map/INDEPENDENT_REVIEW.md（review→fix→复验记录）。
