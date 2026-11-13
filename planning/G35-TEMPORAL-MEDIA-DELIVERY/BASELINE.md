# G35-TEMPORAL-MEDIA-DELIVERY · BASELINE

TASK_ID = `G35-TEMPORAL-MEDIA-DELIVERY`
TASK_SLUG = `g35-temporal-media-delivery`

## 执行时快照（2026-11-12，北京时间）

| 项 | 实测值 |
|---|---|
| remote | `https://github.com/WindWang2/webgis-ai-agent.git` |
| `gh repo view --json nameWithOwner,defaultBranchRef` | `WindWang2/webgis-ai-agent` / `master` |
| 主工作区 HEAD（只读，不清空） | `816705218a5aa6202bf15d745cb31cddb3437f44`（`origin/master` 的祖先） |
| 主工作区未提交 | `M vendor/pi` + 若干未跟踪 `.claude/skills/*`（一律保留） |
| 创建时 `git fetch origin master` 后 base SHA | `ca7b3ec273f2b43336852951e2292a416b2238e6` |
| 提交前再次 fetch 的最新 base SHA（已普通合并） | `ba0c84b840768bba50bd7b18d91c1da7e59360c9`（+36 提交；#2094–#2099 = G29–G34 已合并） |
| open PR（创建时与合流时两次分页查询） | **0 / 0**（查询成功，非失败） |
| #2094–#2099 reviews / inline / conversation | 全部 **0**（查询成功）；独立评审仍在各任务本地 `planning/Gx/REVIEW.md` |
| 最近 closed PR | 读取 60 条，#2087–#2093 为 G22–G28；#2033–#2036/#2084–#2086 为集成修复 |
| GitHub reviews / inline review comments | 对上述 25 个相关 PR 逐条查询：**全部为 0**（查询成功） |
| conversation comments | 仅 #2079、#2021 有独立 review 轨迹记录；其余 0 |
| 独立评审记录（本地） | `planning/G22-…` … `planning/G28-…/REVIEW.md` 共 7 份，已定位 |
| open issues | 45 条，主体为 `[R1-*]` 审计质量门；本任务尊重其约束但不以清偿全部为目标 |
| 远端相关分支 | `zcode/goal-temporal-map-products-v1`（GOAL 03 时序视图，已合并基线）；**无 G35/媒体分支** |
| G09/G21 | 已分配（G21 worktree 存在）；不重新占用 |
| feature branch | `zcode/g35-temporal-media-delivery-20261112T134717Z` |
| worktree | `/home/kevin/projects/webgis/webgis-worktrees/g35-temporal-media-delivery-20261112T134717Z` |
| Pi gitlink（`origin/master`） | `b4afdddf44e059c67ccf5097e5317d913521b470` = `v1.5.2`（`git describe`） |
| Pi 本地初始化 | 网络 clone 失败（HTTP/2 CANCEL）；改用离线共享对象 + G22 同 pin 已构建 bundle 复制，`.git` 指向隔离 gitdir；`git -C vendor/pi rev-parse HEAD` = pin；RPC `get_state` 实测成功 |
| 未改 vendor gitlink / `.gitmodules` / 依赖锁定 | 是 |

## 主机能力（实测）

- `rsvg-convert` `/usr/bin/rsvg-convert`（librsvg；G26 已建立能力探测 + typed 503 纪律）
- `ffmpeg`/`ffprobe` 存在，`libx264`/`libvpx`/`libvpx-vp9`/`apng`/`gif` 编码器存在；**Docker runner 未声明安装** → 视频格式本版不开放，只做真实 codec probe + typed 拒绝
- 项目 venv `/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python`：fastapi 0.141.1 / celery 5.6.3 / Pillow 12.3.0
- Pillow APNG 实测：3 帧 / `n_frames=3` / `is_animated=True` / `duration=[1000,2500,500]` / `loop=0` 独立解码成功 → **核心格式定为 APNG（Pillow 已是生产依赖，无损 RGBA，无需新部署依赖）**
- Pillow GIF 实测：全局调色板量化 + `save_all` 可编码；Pillow 会自动合并相邻相同帧并累加 duration；GIF 时长为 10ms 量化、颜色有损 → 兼容格式并披露
- 系统 python3 另有 `fitz` (PyMuPDF)，但**非项目声明依赖**，不作为生产链
- `weasyprint` 仅在项目 venv（可选），`cairosvg`/`imageio`/`av` 均无 → 不引入
- CPU 16 核 / 62GiB；本任务按 -j2、pytest 单进程、1 个 encoder、公共 heavy-stage 锁执行

## 生产基线（origin/master 实读）

- 时序工具族：`app/tools/temporal_view_tools.py`（bind/control/compare/unbind/status）
- 观测桶：`app/services/gis_harness/temporal_products/frames.py`；过滤：`.../time_filter.py::apply_time_window_to_doc`
- 契约：`app/lib/cartography/mapspec_schema.py` `MapSpecTemporalConfig`（`temporal_view.v1`，`MAX_TEMPORAL_FRAMES=512`）
- 静态单幅编译：`app/services/mapspec_to_svg.py::compile_mapspec_to_svg_detailed`（`SvgCompilation`，`include_chrome=True` 时由 `_temporal_note` 依据 `temporal.cursor_frame` 画真实时间标注）
- PNG 栅格化：`app/services/mixed_map_export.py::_export_png`（G26，rsvg-convert；要求数值栅格层 → **G35 不套用该高层 helper**，直接复用其编译/栅格化口径）
- 冻结：`mixed_map_export._frozen_document`（`current_revision` CAS + `mapspec_store.get_mapspec`）
- 落盘/所有权/签名下载：`app/api/routes/map.py`（`_persist_export_file` / `_set_export_owner` / `download_map_export` / `_MEDIA_TYPES`）+ `app/core/signing.py`
- G23 durable jobs：`app/services/jobs/{submit,worker,store,lifecycle}.py`；`atlas_batch` 为页级 checkpoint 范例
- 工具注册：`app/tools/registry.py::tool` + `app/tools/__init__.py` 模块表；Pi 桥：`app/api/routes/pi_tools.py` + `app/extensions/webgis-tools/index.mjs`（G22 v1.5.2 surface）
- G28 完成证据：`app/services/gis_harness/goal_satisfaction/` + `completion/contracts.py`
- 前端：`TemporalTimeController` 挂在 `map-panel.tsx:1973`；`apiFetch` / `downloadWithAuth`；`map.json` i18n
- 全仓检索 `APNG|MediaRecorder|VideoEncoder|temporal_media|animated_png` 在 `app/`+前端 `lib/components` **0 命中** → 无等价媒体生产者，本方向为真实增量
