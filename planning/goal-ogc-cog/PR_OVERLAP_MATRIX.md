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

## PR 前终刷（2026-11-01，提交 PR 前）
- origin/master 仍为 `6c5e71c2c`（无前进，无需 rebase）。
- 新发现 open PR：
  - **#1877（GOAL 03 temporal / MapSpec v1.8）**：改 mapspec_schema.py(+249)/tools/__init__.py(+3)/categories.py(+1)。与本分支交集 = tools/__init__.py + categories.py，**双方均为纯追加不同条目**（不同模块注册行/分类键），无语义冲突；本分支**未触碰 mapspec_schema.py**（MapSpec 桥走既有 v1.0 vector 源 + data_fabric url，不新增版本）。合并顺序无关。
  - **#1876（GOAL 01 spatial semantics）**：交集同上两文件，同款纯追加。
- 处置：交集登记、无代码调整必要；merge 时若 git 无法自动合并（相邻行），按"各自追加自己的模块行"语义手工并集即可。
