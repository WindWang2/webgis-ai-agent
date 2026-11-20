# PLAN · G35 时序地图动画媒体交付

## 用户闭环（一条纵向链）

「当前已绑定 temporal 的真实地图」→ 能力查询 → 选择真实观测帧/顺序/时间显示/
画面尺寸/停留/格式 → 预览冻结计划（含缺期、跨度、资源、遗漏层）→ 提交后台任务
→ 逐帧真实渲染 → 有界编码 → 独立解码核验 → 原子发布 → UI / Pi 重开恢复并下载
动画 + PNG 帧证据包 + manifest。

## 十二个工作包与落点

| WP | 实现 | 关键文件 |
|---|---|---|
| 01 侦察 | 已完成；见 BASELINE / DEDUP_MATRIX | — |
| 02 有版本计划与能力合同 | 关闭词表 Pydantic 请求、真实 encoder/rasterizer/codec probe、能力查询、计划 hash + 16 位短 ref、未知版本拒绝 | `contracts.py`, `capabilities.py`, `planning.py`, `store.py` |
| 03 观测→显示时间映射 | 契约帧 `[start,end)` 直接消费；日历缺期推算（只用于披露）、等时长/按真实间隔 Fraction 精确比值、整数毫秒量化、末帧停留、倒序显式标记、fps 拆帧不改观测数 | `planning.py` |
| 04 冻结 | CAS 修订 + 单读 MapSpec + **深拷**冻结副本、文档/temporal/style/viewport/渲染器/字体指纹、G26 静态栅格绑定、发布前复检（变化即中止） | `freeze.py`, `runner.py` |
| 05 逐数字帧真实渲染 | 逐绑定层 `apply_time_window_to_doc`、窗外要素 escape 检查、SVG 时间注记 oracle、`compile_mapspec_to_svg_detailed(include_chrome=True)`、rsvg-convert 参数数组 + 超时、Pillow 逐帧解码、SVG/PNG checksum、诊断与要素清单 | `render.py` |
| 06 有界编码 | **streaming APNG writer**（acTL/fcTL/fdAT + 逐帧 zlib，单帧常驻）、GIF 全片共用全局调色板 + 时长/色差量化披露、ffmpeg `-encoders` 真实 probe 但视频部署未落实 → typed 拒绝 | `encode.py`, `capabilities.py` |
| 07 持久任务 | 新增 `JobKind.temporal_media`；复用 `submit_durable_job` / `durable_job` / `finish_job` / `confirm_cancelled`；六阶段进度、总帧数已知才报百分比；owner+计划+源+frame+renderer 指纹缓存（条目+字节双上限）；取消终态不可被迟到成功覆盖 | `tasks.py`, `runner.py`, `store.py`, `jobs/lifecycle.py` |
| 08 manifest/解码/发布 | 三种身份完整映射、独立 Pillow 逐帧解码、内容 oracle（标签/要素窗/SVG sha/PNG sha/时长/合并映射）、原子发布 + `.owner` 侧车 + 签名 URL、PNG+SVG+manifest ZIP | `verify.py`, `manifest.py`, `store.py` |
| 09 UI | 时间轴薄入口按钮 + 独立媒体面板：能力、观测清单、缺期、时长规则、固定图例/范围、资源预估、提交/轮询/取消/恢复/三个下载 | `temporal-media-panel.tsx`, `lib/api/temporal-media.ts`, `temporal-time-controller.tsx`, i18n |
| 10 Pi 标准通道 | 6 个 typed 工具，同一 service；`side_effect`/`produced_refs`/memory/latency/scale 如实；固定 pin 真实 Pi 子进程旅程 | `tools/temporal_media_tools.py`, `tests/integration/test_pi_g35_…py` |
| 11 科学/故障/资源反验 | 24 场景覆盖表见 TEST_MATRIX；故障注入红检、唯一渲染帧/RSS/磁盘/解码像素实测 | `tests/unit/temporal_media/` |
| 12 独立审查与交付 | REVIEW.md + PR | — |

## 关键设计决定（含理由）

1. **核心格式 APNG**：Pillow 已是生产依赖，颜色无损 RGBA、逐帧毫秒时长、可独立解码；不引入 imageio/ffmpeg 部署面。GIF 作为兼容输出并披露有损。
2. **自写 streaming APNG writer**：Pillow `append_images` 会把全片帧放进内存；本实现逐帧读 PNG→写 `fdAT`，常驻一张 RGBA 帧，并把「工作量字节」与「常驻内存字节」分开预算（不能因传入 iterator 就宣称常量内存）。
3. **16 位 `plan_ref`**：生产 `ToolDispatchService` 为上下文预算会截断长字符串，实测 64 位 `plan_hash` 被截成 `91e8…` 导致模型无法提交；短 ref 由 owner 作用域的服务端预览计划解析为完整冻结计划，仍拒绝未知/漂移版本。
4. **不套用 G26 `_export_png`**：它要求数值栅格层；G35 直接复用同参数的 `compile_mapspec_to_svg_detailed` + rsvg，纯矢量主链不人为加栅格绕过 guard。静态数值栅格背景经 G26 `freeze_raster_bindings` 冻结并传 `raster_bindings`。
5. **时间标注来自既有 `_temporal_note`**：`apply_time_window_to_doc` 盖章 `cursor_frame`，SVG 编译面据此写真实时间注记；G35 注入本段身份 frame 并做内容 oracle，不另造一套标签渲染。
6. **partial 显式移除不可重现层**：外部底图/MVT 样本/时变栅格不能暗中掉层；`blocking` 默认拒绝，`allow_partial` 才移除并写入完整 omitted 清单。
7. **视频格式不开放**：主机 codec 存在但 runner 镜像未声明 ffmpeg → probe 如实报告、`available=false`；不按扩展名猜编码成功。
8. **Docker 运行面补 `librsvg2-bin fontconfig fonts-noto-cjk`**：让声明的运行环境真正能栅格化并正确显示中文时间标注；不改 Pi gitlink/构建锁定/RPC 协议。
9. **UI / Pi 同一 `session:<sid>` principal**：保证同一份计划 hash / 同一成果 / 同一授权作用域；下载沿用既有 `.owner` 侧车存在性检查 + 签名 URL（签名 URL 是实际授权，PR 如实披露不是双闸门）。
10. **固定地理窗口必须显式传 `bounds`**（R1 P0-1）：冻结时从全部要素求一次并集 extent；否则编译器按当帧要素重算范围会让动画逐帧平移缩放，与 manifest 的 `fixed.viewport` 自相矛盾。
11. **Pi 工具走生产 deferred 发现路径**（R1 P0-2）：与 G26 `webgis_map_export` 同款，由 `tool_search` 发现激活后按名直调；不改写 surface dump、不改 `NATIVE_TOOL_NAMES`/`default_active`（首轮尝试改动实测破坏 4 个既有契约测试，已完整回退）。
12. **GIF 显式折叠 + fps typed 拒绝**（R1 P1-1）：Pillow 会静默合并相同 P 帧；`write_gif` 先按量化像素显式折叠并回写 `effective_encoded`，核验/manifest 一律使用实际容器帧映射。
13. **跨进程状态临界区**（R1 P1-3）：`state.lock` 的 `fcntl.flock(LOCK_EX)` + 终态字段整体保护，cancel 与 worker finalize 不会互相覆盖。
14. **编码有真实墙钟预算**（R1 P2-1）：`write_apng`/`write_gif` 每帧检查 `time.monotonic()`，超时 typed `ENCODE_TIMEOUT`，不再只回执一个死常量。

## 并行边界遵守

- 未改 `vendor/pi` gitlink、`.gitmodules`、`requirements.lock`、RPC/extension 内核、AGENTS.md/技能/测试配置、审批配置。
- 共享入口只做局部加性接线：`app/main.py`（1 import + 1 include_router）、`app/tools/__init__.py`（1 行）、`app/services/jobs/lifecycle.py`（1 enum）、`app/api/routes/map.py`（3 个 media type：`.apng/.gif/.zip`；未加入 `.json`，避免放宽既有下载面）、`frontend/components/map/temporal-time-controller.tsx`（1 按钮 + 1 panel）、两个 locale 的 `map.json`（同键加性）、两个 Dockerfile（各 1 行 apt 包）。
- 未新增第二套 agent loop / DAG / 工具注册表 / MapSpec store / 全局 ID / 撤销栈 / 事件总线 / 证据库。
- G29–G34 主所有权文件零改动；G33/G34 未合并不影响本方向（只消费现有 title/legend/viewport/temporal）。
