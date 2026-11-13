# DEDUP_MATRIX · G35

判定口径：`present` = 最新 origin/master 生产调用链已完整满足，转为兼容回归；`partial` = 有基础但缺本域闭环；`missing` = 本任务真实增量；`owned-by-other` = 他任务/历史方向所有，不重复制造；`blocked` = 依赖外部能力，须 typed 披露。

| # | 要求 | 判定 | 生产调用链证据 / 本任务增量 |
|---|---|---|---|
| 1 | temporal bind/control/compare/unbind/status | present | `app/tools/temporal_view_tools.py:65-268` → `temporal_products/service.py` → `SetTemporalViewIntent`。只做兼容回归 |
| 2 | 真实观测桶、时间解析、半开边界/时区 | present | `frames.py::detect_time_binding` / `buckets_to_contract_frames`；`time_filter.py::normalize_boundary_value`。媒体计划只消费既有帧，不重解析 |
| 3 | MapSpec temporal 契约与 512 帧上限 | present | `mapspec_schema.py:1011-1148`。媒体预算另立且更严（`MAX_TEMPORAL_MEDIA_OBSERVATIONS=120`） |
| 4 | 前端播放/dispalyed_at 门控/user_pinned | present | `frontend/lib/temporal-map/{controller,live-frame,mutation}.ts` + `temporal-time-controller.tsx`。媒体面板不改 live 游标 |
| 5 | PublicationIR time driver 静态页 | present | `publication_ir.py::_time_pages` / `publication_export.py::compile_publication_page`。**不复用其 atlas 截断结果**（页预算 ≠ 完整帧集） |
| 6 | 矢量/混合单幅 SVG 编译与 PNG 栅格化 | present | `mapspec_to_svg.py::compile_mapspec_to_svg_detailed` + `mixed_map_export.py::_export_png`（rsvg）。逐帧调用既有编译面，不复制 renderer |
| 7 | 静态导出、所有权、签名下载 | present | `map.py::_persist_export_file/_set_export_owner/download_map_export`；`core/signing.py`。媒体产物复用同一事实源 |
| 8 | Pillow 生产依赖 | present | `requirements.txt:94 Pillow>=10.0.0`。APNG/GIF 不引入新 pip 依赖 |
| 9 | G23 durable jobs（claim/fencing/heartbeat/cancel） | present | `jobs/{submit,worker,store}.py`；新增 `JobKind.temporal_media` + 一个 Celery task，不重建平台 |
| 10 | G25 frame runtime / G26 publication / G28 完成证据 | present | `frame_product/*`、`publication_export.py`、`goal_satisfaction/*`。只做 adapter 消费；G33/G34 未合并不影响 |
| 11 | G22 Pi v1.5.2 对齐 | present | gitlink `b4afdddf` = v1.5.2。本任务不动 gitlink/构建/部署契约，只在自己 worktree 固定 pin |
| 12 | **媒体能力查询 / 有版本计划 / 计划 hash / 未知版本拒绝** | missing | 新增 `temporal_media/contracts.py` + `planning.py` + `store.py` |
| 13 | **观测→显示时间确定映射（等长/按真实间隔、缺期、倒序、量化）** | missing | 新增 `planning.py::build_display_segments` |
| 14 | **源/表达/窗口冻结与指纹、输入变化中止** | partial | 复用 `_frozen_document` CAS + `mapspec_store`；新增 `freeze.py` 指纹与发布前复检 |
| 15 | **逐数字帧真实渲染 + 多绑定层验证 + PNG 证据** | missing | 新增 `render.py`（每层 `apply_time_window_to_doc`、SVG 时间标注 oracle、rsvg、逐帧解码） |
| 16 | **有界真实动画编码器（APNG 核心 / GIF 兼容 / 视频 probe）** | missing | 新增 `encode.py`；`ffmpeg -encoders` 真实 probe，Docker 未落实 → 视频 typed 拒绝 |
| 17 | **持久任务阶段/进度/取消/恢复 + 有界帧缓存** | partial | 复用 `durable_job`/`finish_job`；新增 `runner.py` 阶段投影与 `store.py` 指纹缓存（条目+字节双上限） |
| 18 | **manifest + 独立解码 + 内容 oracle + 原子发布** | missing | 新增 `verify.py` / `manifest.py`；故障注入必须红检 |
| 19 | **UI 计划预览/提交/恢复/下载** | missing | 新增 `frontend/components/map/temporal-media-panel.tsx` + `lib/api/temporal-media.ts`，时间轴加薄入口 |
| 20 | **Pi 标准工具通道（能力/计划/提交/查询/取消/成果）** | missing | 新增 `app/tools/temporal_media_tools.py`（6 个 typed 工具，同一 service） |
| 21 | **真实 Pi 子进程 + 生产扩展 + dispatch + 本域服务旅程** | missing | 新增 `tests/integration/test_pi_g35_temporal_media_journey.py`（固定 pin + stub LLM） |
| 22 | MP4/WebM 正式支持 | blocked | 主机有 codec，但 `Dockerfile`/`Dockerfile.prod` runner 未装 ffmpeg；不做「按扩展名猜成功」，probe 结果如实披露 |
| 23 | 时变栅格/cube、外部底图、model3d、MVT 全图动画 | blocked | 现有时序契约 `render_path=client_filter` 面向已流式数据；无完整可冻结源绑定 → `UNSUPPORTED_LAYER` typed 拒绝或显式 partial 披露 |
| 24 | G09 参数化批量分析 / G21 有向 OD 流图 | owned-by-other | 已分配；G21 worktree 存在。不重新占用 |
| 25 | G29 RPC 运行中交互 / G30 技能作业 / G31 DQ / G32 变更提案 / G33 专题 / G34 版式 | owned-by-other | 并行边界明确；本任务不修改其主所有权文件 |
| 26 | R1 审计 issue 清偿 | owned-by-other | 45 条 open issue 为质量门（如 #2071 宿主内存上界、#2044 上传归属）；本任务遵守但不以清偿为目标 |

## 冲突记录

- 历史 architecture 文档仍提及 ChatEngine fallback；`#2036` 已下线，`app/services/chat/` 为 Pi-only。本任务维持 Pi-only：bridge 不可用时 typed `PI_BRIDGE_UNAVAILABLE`/503，不恢复任何兜底。
- G26 `_export_png` 要求 `has_raster_layers`，不可直接用于纯矢量时序图；本任务走 `_compile_single_svg` 同参数的 `compile_mapspec_to_svg_detailed` + rsvg，不人为加入栅格绕过 guard。
