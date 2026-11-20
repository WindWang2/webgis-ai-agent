# G47-DORLING-CARTOGRAM — BASELINE

## 任务与仓库

- TASK_ID: `G47-DORLING-CARTOGRAM`
- 仓库: `WindWang2/webgis-ai-agent`（`gh repo view` 确认 `nameWithOwner` 一致，defaultBranchRef=master）
- 主工作区: `/home/kevin/projects/webgis/webgis-ai-agent`（含用户未提交 `vendor/pi` 与 `.claude/skills/*`，**未触碰**）
- 本任务 worktree: `/home/kevin/projects/webgis/webgis-worktrees/g47-dorling-cartogram-20261115T124822Z`
- 分支: `zcode/g47-dorling-cartogram-20261115T124822Z`
- git-common-dir: `/home/kevin/projects/webgis/.bare`

## 启动快照（2026-11-15，北京时间）

| 项 | 值 |
|---|---|
| `origin/master`（base） | `655f769ca17f17c5797da13b4c299b8fa600e370` |
| 研究快照 SHA（本文档依据） | `34f4e3670f6703f255e1b2cfd04816070fbeeb5c` |
| 本地 `master`（主工作区落后 380 提交，仅供读取） | `816705218a5aa6202bf15d745cb31cddb3437f44` |
| open PR | **0**（#2124 G40 符号资产已合并进 base） |
| open issue | #2123（旧 master 后端/前端/lint 基线债报告，非本任务实测） |

G40 #2124 已合并：`origin/master` 即其 merge commit。本任务**不认领**其符号/纹理资产，
核心路径不依赖它（只在可选样式处消费既有接口，若无则用自己的受控样式）。

## 复用的既有权威（生产调用链已核）

| 能力 | 位置 | 本任务用法 |
|---|---|---|
| 强版本冻结（单调 generation + 会话锁 + 单次深拷贝） | `app/services/bivariate_reliability_authoring/snapshot.py:139-222` | 同合同实现本域 `snapshot.py`（ref-only，几何资格另加） |
| 层解析 + CAS 令牌 | 同上 `service.py:84-124` | 本域 `service._resolve_layer`（不走私有导入） |
| 单笔 MapSpec 提交（CAS/checkpoint/undo） | `apply_gis_mutation` + `UpsertLayerIntent` | apply 阶段写本域证据块，不改原几何 |
| 进程内 durable job | `app/services/data_operations/runner.py:375-444`（`submit_async_job`）+ `run_async_job` | 布局求解作业（取消/重试/重启由既有 runner 保证） |
| job 生命周期 | `app/services/jobs/lifecycle.py:46-62` | 新增 `JobKind.cartogram_layout`（String(20) 加性） |
| 会话 ref 存储 | `session_data_manager.store/get/get_ref_descriptor` | 布局 artifact 持久化（`ref:g47layout-*`，非地理 GeoJSON） |
| 工具注册 | `app/tools/__init__.py` 模块表 + `@tool` | 新 `cartogram_tools` |
| 路由注册 | `app/main.py` include_router | 新 `cartogram_authoring` 路由 |
| 出版纸面组件 | `app/services/print_layout_authoring/{compile,html}.py`、`app/lib/cartography/print_layout_schema.py:57-60` | 新增受控 `diagram` kind + 渲染 adapter |
| 导出渲染后端 | `rsvg-convert`（系统）、`weasyprint 70.0`（venv） | PNG/PDF 由同一冻结 SVG 生成 |

## 环境

- 宿主 shell = **fish**：所有 `VAR=...` 前缀命令必须 `bash -c '...'`。
- Python: `/home/kevin/projects/webgis/webgis-wt-g25-frame-runtime-20261111T173941/.venv/bin/python`
  （shapely 2.2.0 / pyproj 3.8.0 / weasyprint 70.0 / aiosqlite / numpy）。`PYTHONPATH=<本 worktree>`。
- `ulimit -v` 无限制，16 核 / 62GB，但按纪律 `-j2`、pytest 单进程、最多一个重阶段。
- 同仓重阶段锁：`$(git rev-parse --path-format=absolute --git-common-dir)/webgis-heavy-stage.lock`。

## 既有 cartogram 事实（本任务的真实验收起点）

- `presentation_views.py:42-56`：`cartogram_map.runtime_status="planned"`，泛 cartogram 仍未实现。
- `render_diagnostics.py:134-137` + `frame-composer.ts:122-127`：非 Dorling 的泛 cartogram 请求按原几何导出并披露 `cartogram_unsupported`（本任务**不** blanket 放行）。
- G33 `SizeChannel`（`thematic_authoring/contract.py:113-128`）已能表达"原位比例圆"，但那不是位移布局；本任务增量为 **Dorling 位移求解 + 独立质量核验 + diagram 成果/出版**。
