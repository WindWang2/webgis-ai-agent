# PR_OVERLAP_MATRIX（基线 6c5e71c2c；每阶段开始与提交 PR 前刷新）

| PR | 主题 | 与本任务关系 | 处置 |
|---|---|---|---|
| #1868 sdo 数据准备工作台 | inspect→propose→validate→prepare→bind | 复用（M5 接受本方向 descriptor）；不改其核心 | reuse |
| #1869 分析工作流 AnalysisWorkflowContract | 五族编排 | 复用（COG ref 可作其输入）；不动其模块 | reuse |
| #1870 制图候选 | 表达模型 | 无文件交集 | avoid |
| #1871 图册批量 | durable job | 无文件交集 | avoid |
| #1873 要素编辑 | 预览/确认/撤销 | 无文件交集 | avoid |
| #1874 地图操作事务 | map_operation.v1 | 只读参照其 CAS 纪律 | avoid |
| #1875 联动视图/标注 | linked views | 无文件交集 | avoid |
| #1771 g3d01 PMTiles→XYZ/DEM | tile 服务纪律先例 | 复用模式（DfTileCache/ETag）；不改其文件 | reuse(pattern) |
| #1835 g3dv2-a DEM 供给 | 摄入/资格 receipt | 复用 receipt 语义；文件不重叠 | reuse(pattern) |
| #1836/#1867 3D | scene runtime | 无交集 | avoid |
| #1758 issue backlog 清偿 | 安全修复 | 只读依赖其 SSRF/owner 语义 | reuse |

## 本任务触碰文件清单（新增为主）
- 新增：adapters/ogc_tiles_adapter.py、adapters/ogc_coverages_adapter.py、ogc_tiles_proxy.py、coverage_read.py、cog_materialization.py、app/tools/ogc_service_tools.py、tests/data/ogc_service_fixtures.py、tests/unit/test_ogc_tiles_*.py、tests/unit/test_ogc_coverages_*.py、tests/unit/test_cog_materialization_*.py、tests/data/test_service_discovery_*.py、frontend 最小 UI。
- 小幅追加（既有文件，additive）：registry.py（AdapterSpec×2）、source_registry.py（KNOWN_PROTOCOLS）、query/capabilities.py（默认矩阵）、fabric/probing.py（探测映射）、routes/data_fabric.py（1 条瓦片路由）、mapspec_source.py（vector passthrough 分支）、tools/__init__.py + categories.py（新工具模块）、add-source-form.tsx + i18n messages、openapi/api-docs 快照（生成物）。
- 冲突风险最高的共享文件：routes/data_fabric.py 与 mapspec_source.py —— 均为追加式小 diff，PR 前再 fetch 比对 master 前进。

## 刷新记录
- 2026-11-01 初次建立（基线核查时无 open PR；#1868-#1875 全 merged）。
