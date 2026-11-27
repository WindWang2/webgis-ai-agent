# BASELINE — 05｜GIS 工程包、跨系统数据交换与可移植复现 Harness

## 启动审计记录（2026-11-28 UTC）

| 项 | 值 |
|---|---|
| 目标仓库 | WindWang2/webgis-ai-agent（origin，https 已脱敏） |
| 审计时刻 origin/master | `f34cb69dda6150ee4135e6d2a476708149d9eef7`（= 参考快照，无漂移） |
| 任务分支 | `zcode/goal-portable-project-exchange-20261127`（基于 origin/master f34cb69d 新建） |
| 独立 worktree | `/home/kevin/projects/webgis/webgis-portable-project-exchange-20261127` |
| vendor/pi gitlink（master） | `ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe` = tag **v1.6.1**（2026-11-25） |
| 本 worktree vendor/pi | 已 submodule update --init，HEAD=ef4a1cfc，describe=v1.6.1 |
| Pi 上游最新 tag | **v1.7.0**（2026-11-27，diff 约 1400 文件）。按任务裁决：仅 01 线可动 pin；本线（05）不动 vendor/pi，针对 v1.6.1 保持兼容 |
| 开放 PR | #2290（01 线 Pi 运行策略）、#2291（03 线概率抽样）；无本线主题的开放 PR |
| #2272–#2289 | 全部 merged（含 #2283 Pi 决策辅助、#2284 全量审查修复、#2285 多源联动、#2286 数据供给、#2287 图符地图、#2288 合并集成、#2289 会话文件面板） |
| 开放 issues | 0 |
| 主机环境 | Linux x64，zsh；项目 venv Python 3.12.15；系统 GDAL 3.13.3（ogr2ogr/gdalinfo 可用） |
| venv GIS 库 | geopandas ✅ rasterio ✅ pyogrio ✅ shapely ✅ pyproj ✅ pandas ✅ sqlalchemy ✅ fastapi ✅ pytest ✅；fiona ❌（pyogrio 替代，无需新增依赖） |
| 共机纪律 | 同机有 ~18 个其他任务 worktree；编译/测试限 -j4，重进程串行，独立端口/缓存路径 |

## 基线能力盘点（启动时确认存在的权威面）

- Project Workspace 域：Project / ProjectDataset / Workflow / WorkflowRun / Artifact / ArtifactLineage（详见 DEDUP_MATRIX）
- MapSpec + checkpoint 内容寻址 blob 存储 + ref/DataObject（SessionStore）
- 数据交付：data_fabric adapters（GeoPackage/COG/GeoParquet…）、static map export、MVT
- Pi 宿主：pi_native_surface（7 natives + webgis_execute）、webgis-tools 扩展
- #2289 会话文件面板（消费新产物的既有入口）
- `project_bundle` / `project_package` / `project_import` / `workspace_bundle` 关键词在 master 零命中（同义实现深查见 DEDUP_MATRIX）

## 基线遗留/非本任务缺陷

（启动基线测试后回填）

## 引用快照命令

```
git rev-parse origin/master          # f34cb69dda6150ee4135e6d2a476708149d9eef7
git ls-tree origin/master vendor/pi  # 160000 commit ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe
git -C vendor/pi describe --tags     # v1.6.1
```
