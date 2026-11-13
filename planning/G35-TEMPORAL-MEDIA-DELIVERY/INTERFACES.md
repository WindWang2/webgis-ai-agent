# INTERFACES · G35

## 版本与词表（关闭）

- `SCHEMA_VERSION = "temporal_media.v1"`；manifest `temporal_media_manifest.v1`；
  能力 `temporal_media_capabilities.v1`；数字帧 `temporal_media_digital_frame.v1`；
  状态 `temporal_media_state.v1`；验证 `temporal_media_verification.v1`。
- 消费既有 `temporal_view.v1`（`MapSpecTemporalConfig`），**不新建 temporal 契约**。
- `MEDIA_FORMATS = ("apng","gif")`；`mp4/webm` 只 probe、不开放。
- `timing_policy = equal|proportional`；`order = chronological|reverse`；
  `missing_display = skip|empty|hold`；`time_display = label|label_window|none`；
  `partial_policy = blocking|allow_partial`；`segment.kind = observation|held|empty`。
- 所有请求模型 `extra="forbid"`：不接受路径、URL、shell 参数、编码器选项、外部 provider。

## Pi 标准工具（6 个，注册在 `app/tools/__init__.py`）

| 工具 | 参数模型 | side_effect | produced_refs | latency / memory / scale |
|---|---|---|---|---|
| `webgis_temporal_media_capabilities` | `CapabilitiesArgs` | `cacheable_read` | — | fast / light / small |
| `webgis_temporal_media_plan` | `MediaPlanArgs` | `state_mutation`（写 owner 作用域预览计划） | `("plan_ref",)` | medium / light / medium |
| `webgis_temporal_media_submit` | `SubmitArgs` | `state_mutation` | `("job",)` | fast / light / small（耗时在后台 job） |
| `webgis_temporal_media_status` | `JobRefArgs` | `cacheable_read` | — | fast / light / small |
| `webgis_temporal_media_cancel` | `JobRefArgs` | `state_mutation` | — | fast / light / small |
| `webgis_temporal_media_result` | `ResultArgs` | `cacheable_read` | `("artifact","manifest")` | fast / light / small |

`session_id` 由 dispatcher 注入并写入模型（registry schema 中不暴露给 LLM）。
6 个工具在**生产 surface dump 零改写**下属于 `exposure=deferred` 长尾（与 G26
`webgis_map_export` 同款），由 Pi 内置 `tool_search` 发现激活后按名直调；
`default_active` 仍严格等于冻结的 7 个 native 前门（4 个既有契约测试钉住该不变量）。
`submit` 返回 `status=pending`；`result` 只在 `completed && verification.passed` 后返回授权 ref，
否则 `RESULT_NOT_READY`(409)。`plan_ref`（16 hex）为 Pi/LLM 推荐入口，`plan_hash` 供服务端/UI。
请求面无 `title` 字段（R1 P2-5：空操作参数已删除）。

## REST（`/api/v1/temporal-media`，`app/api/routes/temporal_media.py`）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/capabilities?session_id=` | 实测能力 + temporal 绑定支持矩阵 |
| POST | `/plan` | 预览冻结计划，返回 `plan_ref` + `plan_hash` + 有界计划 |
| POST | `/submit` | 提交 durable job，返回 pending |
| GET | `/jobs?session_id=` | 重开页面后 owner 隔离的任务列表 |
| GET | `/jobs/{job_id}?session_id=` | 阶段/进度/缓存/编码/成果引用 |
| DELETE | `/jobs/{job_id}?session_id=` | 取消（终态） |
| GET | `/jobs/{job_id}/result?session_id=&refresh_urls=true` | 重新签发授权下载 URL |

会话归属由 `verify_session_owner` 证明；成果文件仍走既有
`GET /api/v1/export/download/{filename}` 的 `.owner` 侧车 + 签名 URL fail-closed 鉴权。

## 前端 API（`frontend/lib/api/temporal-media.ts`）

`getMediaCapabilities` / `previewMediaPlan` / `submitMediaJob` / `getMediaJobStatus` /
`cancelMediaJob` / `getMediaJobResult` / `listMediaJobs`；全部经 `apiFetch`，下载经
`downloadWithAuth`。面板 `TemporalMediaPanel` 无 props，内部取 `getSessionIdentity()`。

## 领域状态（`<DATA_DIR>/exports/temporal_media/`）

```
<owner_key>/<job_id>/plan.json          冻结计划（含 document 深拷）
<owner_key>/<job_id>/state.json         阶段投影（version CAS；cancelled 终态）
<owner_key>/<job_id>/svg_cache/*.svg    逐帧 SVG 证据（恢复用）
<owner_key>/preview_<16hex>/plan.json  owner 作用域预览计划（短 ref 解析）
_frame_cache/<owner_key>/<fp>.png|json 有界数字帧缓存（条目 512 / 字节 256 MiB）
```

缓存键 = owner + plan_hash + document/temporal/style/viewport/renderer/font 指纹 +
frame 窗口 + time_label + held_from + kind + canvas + format。命中必须可解码且 checksum 一致。

## 三种身份映射（manifest）

`observations[]`（真实桶）→ `segments[]`（显示段，含 held/empty 与 duration/encoded_repeat）
→ `digital_frames[]`（真实 PNG + SVG + 要素/窗口/诊断/checksum）
→ `encoded_frames[]`（容器帧，含 `segment_indices` / `segment_durations_ms` /
`identities` / `merged` / `equivalent_digital_frames`）。
相同画面合并只在**跨显示段**且 PNG 字节完全相同时发生；显式 fps 拆出的同段多帧不合并。

## 资源上限（与 `MAX_TEMPORAL_FRAMES=512` 的数据契约上限分离）

| 项 | 值 |
|---|---|
| 默认 / 最大画面 | 1280×720 / 1920×1080（最小 320×240） |
| 独立观测画面 | ≤120 |
| 显示段 / 编码帧 | ≤240 / ≤240 |
| 停留 | 100–10,000 ms |
| 总展示时长 | ≤120,000 ms |
| fps | 0（停留复用）或 ≤24；**GIF 仅允许 0**（`GIF_FPS_UNSUPPORTED` typed 拒绝） |
| 按间隔缩放的长间隔压缩 | 10 年（超过披露 `proportional_gap_compressed`） |
| 编码器常驻像素预算 | ≤320 MiB（APNG 单帧常驻；GIF 按 P 帧数 ×3 计中间量） |
| 临时磁盘估算 | ≤512 MiB |
| 帧缓存 | ≤512 条且 ≤256 MiB（PNG + SVG + JSON 证据一起计字节/淘汰） |
| SVG / manifest | ≤8 MiB / ≤16 MiB（写侧校验） |
| rsvg 超时 / 编码超时 / probe 超时 | 60 s / 120 s（逐帧墙钟检查，真实生效） / 8 s |

状态文件用 `<owner>/<job_id>/state.lock` 的 `fcntl.flock(LOCK_EX)` 收口跨进程读-改-写；
`created_at` 用于恢复时按时间取最近任务。

## 兼容与回退

- 无 rsvg-convert → `PNG_RASTERIZER_UNAVAILABLE` 503（typed，不假装已渲染）。
- 无 temporal 绑定 / 帧数 <2 / 未知 temporal 版本 → typed 拒绝，不恢复 ChatEngine。
- 不可重现图层 → `BLOCKED_INCOMPLETE` 409；`allow_partial` 显式选择才移除并披露。
- 源/修订在渲染中变化 → `SOURCE_CHANGED_MID_RENDER` 409，发布前复检中止，不发布混合 revision。
- 编码器退出不代表完成：容器 magic/checksum/帧数/逐帧尺寸与时长/画面像素/标签/要素窗
  独立核验任一失败即不标 completed。
- 取消：后续帧与编码停止、临时目录清理、`cancelled` 终态，迟到成功/进度/成果都不得覆盖。
- 签名 URL 过期可由 `status/result` 重新鉴权签发，不要求重算作品。

## 版本/编号查重

- 新增 `JobKind.temporal_media`（`String(20)` 列，无需迁移）。
- 新增路由前缀 `/api/v1/temporal-media`（与既有 `/atlas/jobs`、`/export/*` 不冲突）。
- 新增 6 个 `webgis_temporal_media_*` 工具名（与 `temporal_view_*`、G26 `webgis_map_export` 不冲突）。
- 旅程 ID `J-G35` / `test_pi_g35_temporal_media_journey.py`；测试夹具全部加 `g35-` 前缀。
- 未新增 ADR 编号、MapSpec schema 版本、alembic revision、前端 Playwright journey 编号。
