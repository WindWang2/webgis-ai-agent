# StoryMap Orchestrator 技术规格书（ADR-0196）

- 基线 commit: 3eb2cc6a（origin/master @ agent/12 分支切出点）
- 日期: 2026-09-15
- 结论先行：`StoryMapSpec` 是唯一的跨端叙事契约；后端纯函数编译（trace→章节、bbox→相机轨迹、spec→离线包），前端只做呈现与滚动驱动消费。全部算法无 LLM、无 DB 依赖、可离线单测锁定。

## 1. 领域模型（`app/lib/storymap/spec.py`）

pydantic v2，基类 `_StoryModel(BaseModel)` 配 `ConfigDict(extra="allow")`（未知键保留，additive-only 演进，对齐 MapSpec `_SpecModel` 纪律）。

```
STORYMAP_SPEC_SCHEMA_VERSION = "1.0"
NARRATIVE_ARC = ("introduction", "macro_situation", "focus_dissection",
                 "dynamic_simulation", "recommendation")
# 引言 → 宏观态势 → 重点解剖 → 动态推演 → 决策建言
```

| 模型 | 字段 | 约束 |
|---|---|---|
| `CameraKeyframe` | `chapter_id: str`、`t: float`（章内归一进度 0..1）、`center: tuple[float,float]`（[lng,lat]）、`zoom: float = 3..18`、`pitch: float = 0..60`、`bearing: float = -180..180`、`easing: Literal["linear","ease_in_out"]="ease_in_out"` | pitch 上限 60（任务规格）；bearing 允许 -180 与 180（同一方位） |
| `StoryChapter` | `id`、`title`、`narrative: str`（markdown）、`arc_role: Literal[NARRATIVE_ARC]`、`source_stage_ids: list[int]`（证据链阶段追溯）、`linked_widget_ids: list[str]`、`highlight_refs: list[str]`、`duration_hint_s: float >= 0` | `duration_hint_s` 缺省由 `estimate_duration(text)` 派生 |
| `LinkedWidget` | `id`、`kind: Literal["chart","table","kpi","stats"]`、`ref: str`（`ref:chart-*` 等产物 ref）、`title`、`chapter_id`、`data: dict = {}` | data 承载图表 spec（recharts 兼容形状，原样透传） |
| `AudioNarrative` | `chapter_id`、`text`、`lang: str = "zh-CN"`、`duration_hint_s: float`、`voice: str \| None` | v1 只做解说词文本 + 时长估算（中文 4 字/秒），TTS 是后向扩展位 |
| `StoryMapMetadata` | `title`、`session_id = ""`、`turn_id = ""`、`generated_at: str`（UTC ISO）、`summary = ""`、`theme = "dark-carto"`、`language = "zh-CN"`、`engine_version` | |
| `StoryMapSpec` | `schema_version = "1.0"`、`metadata`、`chapters: list[StoryChapter]`、`camera_keyframes: list[CameraKeyframe]`（平铺全量，按 chapter_id 归属）、`linked_widgets: list[LinkedWidget]`、`audio_narrative: list[AudioNarrative]` | 校验：chapters 非空；每个 camera_keyframe 的 chapter_id 必须存在于 chapters |

辅助纯函数：`estimate_duration(text) -> float`（剥 markdown 后 `ceil(len/4)`，下限 2.0s）、`narration_text(markdown) -> str`（剥语法留正文）。

## 2. 叙事编译（`app/lib/storymap/story_compiler.py`）

### 2.1 输入归一 `normalize_trace(raw: Mapping) -> list[TraceStep]`

`TraceStep = {stage_id: int(1..18), ts: float, payload: dict}`。接受三种形状（按特征嗅探，不猜）：

1. **GisTraceChain 形状**：`{"turn_id","session_id","stages":[{stage|stage_id, ts, **payload}]}`（`stage` 接受枚举名或 int）；
2. **ReplayTrace 形状**：`{"turn_id","user_input","final_text","tool_calls":[...],"artifacts":[...],"outcome"...}` —— tool_calls 逐条映射为 S9 步（payload 取 args/tool_name），artifacts 映射为 S12，final_text 映射为 S18；
3. **消息列表降级**：`compile_story_map(messages=[{role,content}], ...)` 直用消息文本（无 trace 的旧会话）。

### 2.2 阶段→叙事弧分桶

| 弧 | 阶段 | 章节素材 |
|---|---|---|
| introduction | 1–2 | 用户意图原文（`user_intent`/`parsed_intent`/user 消息） |
| macro_situation | 3–8 | 数据画像统计摘要（`stats`/`metrics`/`summary`） |
| focus_dissection | 9–12 | 工具调用与工件（`conclusion`/`tool_name`/artifact ref） |
| dynamic_simulation | 13–16 | 地图变更与验证（`mutations`/`verification`/`repair`） |
| recommendation | 17–18 | 裁决与最终输出（`verdict`/`final_text`） |

规则：空桶跳过；保序输出；全部桶为空且给了 messages 时降级为"首条用户消息→引言、其余最后一条→建言"两章。章节 id = `arc-{role}`（同一弧只出一场）。

### 2.3 文本与统计提炼

- 章节标题 = 弧缺省标题（`ARC_TITLES_ZH`）或 payload 显式 `title`；
- narrative 合成：`## 标题` + 摘要句（首个 `summary`/`conclusion`/`final_text`）+ 统计要点（`stats`/`metrics` dict → `- k: v` bullet，封顶 6 条）；
- bbox 提取：payload 的 `bbox`（`[w,s,e,n]`）/`extent`/`center`+`zoom`/GeoJSON `features` 逐特征坐标扫出，桶内并集；
- 联动图表：payload 含 `chart`（dict，带 `kind`+`data`）或 `ref:chart-*|ref:table-*` 文本 → `LinkedWidget(kind=chart|table, ref, data=chart)`，归属当前桶；
- 解说词：每章生成 `AudioNarrative`（text = narration_text(narrative)，duration 由 `estimate_duration`）。

### 2.4 顶层入口

```
compile_story_map(
    trace: Mapping | None = None,
    messages: list[dict] | None = None,
    *,
    session_id: str = "",
    turn_id: str = "",
    title: str | None = None,
) -> StoryMapSpec
```

trace 与 messages 至少给一个，否则 `ValueError`。spec.metadata.summary = final_text 首句 / 弧标题串。

## 3. 相机规划（`app/lib/storymap/camera_planner.py`）

### 3.1 Bbox → 单章关键帧 `plan_camera_for_bbox(bbox, arc_role, *, aspect=16/9)`

- `center = ((w+e)/2, (s+n)/2)`；
- `zoom = clamp(log2(360 / max(lon_span_deg, 1e-6)) + 1, 3, 18)`（经纬张角口径，不做 cos 修正——故事镜头以经度张角为准，极区同跨度不虚高 zoom）；
- pitch 按弧角色 + 尺度：`base = {macro_situation:10, introduction:18, recommendation:22, dynamic_simulation:32, focus_dissection:45}`，再按跨度收缩上调（span<0.05° 时 +10，span>10° 时归零封顶），最终 `clamp(0, 60)`；
- bearing 缺省 0°；`focus_dissection`/`dynamic_simulation` 给 15°/−20° 的确定性构图偏角（可覆盖）。

### 3.2 轨迹插值 `build_camera_track(keyframes, *, samples_per_leg=16) -> list[CameraSample]`

- 输入按 `(chapter_id, t)` 排序为控制点序列 P0..Pn；
- 中心点：每 leg Catmull-Rom→三次贝塞尔（`B0=P1, B1=P1+(P2−P0)/6, B2=P2−(P3−P1)/6, B3=P2`；端点复制补pad），`bezier(u)` 逐维求值；
- zoom/pitch：三次贝塞尔缓动 `cubic_bezier_ease(u, (0.25,0.1,0.25,1.0))`（Newton–Raphson 解 x(u)=u'，8 轮迭代 + 二分兜底，收敛容差 1e-7）；
- bearing：最短弧 delta（`((b2−b1+540)%360)−180`）后同款缓动，输出归一回 [−180,180]；
- 每 leg 均匀采 `samples_per_leg` 个样本（含两端去重），`CameraSample = {t, center, zoom, pitch, bearing}`，t 全局单调 [0,1]；
- **奇点守卫**：leg 长度为 0（控制点重合）直接输出重合样本；n<2 输出单样本；全部输出经 NaN 清洗（`math.isfinite`，非有限值回退该 leg 起点值）。

### 3.3 连续性闸 `validate_track(track, *, max_center_jump_deg=1.0, max_zoom_jump=1.0, max_pitch_jump=15.0, max_bearing_jump=30.0) -> list[str]`

相邻样本逐维跳变超阈即记违规串；测试断言 `== []`。阈值是**闸门参数**而非魔法数：samples_per_leg=16 时正常轨迹远低于阈值，突变（如直接跳段）必然命中。

## 4. 离线打包（`app/lib/storymap/export_packager.py`）

### 4.1 `build_story_bundle(spec, *, layers=None, mapspec=None, sanitize=True) -> dict`

```
{"schema_version": "storybundle-1", "generated_at": <UTC ISO>,
 "spec": <sanitized spec dict>,
 "data": {"layers": [...GeoJSON FeatureCollection 列表], "mapspec": {...或 None}},
 "manifest": {"chapter_count", "widget_count", "layer_count", "feature_count"}}
```

脱敏（`sanitize_dict`）：递归遍历 dict/list，键名小写化后命中黑名单（`token, secret, password, api_key, apikey, authorization, credential, cookie, owner_token, session_token, refresh_token, access_token`）→ 值替换 `"REDACTED"`；`sanitize=False` 时原样（仅限可信内网场景）。

### 4.2 `render_standalone_html(bundle) -> str`

- `<!DOCTYPE html>` 开头；`<script type="application/json" id="story-bundle">` 内嵌 `json.dumps(bundle, ensure_ascii=False)`，其中 `</` 转义为 `<\/`（防 script 早闭合），`<!--` 转义（防注释逃逸）；
- 内嵌一段零依赖 vanilla JS 查看器：解析 JSON → 渲染标题/摘要/章节文本/镜头参数表；**不出现任何 http(s) 外链引用**（离线可开）；
- `bundle_to_json(bundle)` 给单文件 JSON 形态（`ensure_ascii=False, sort_keys=False`）。

## 5. 服务与 API

### 5.1 `app/services/storymap/`（IO 壳）

- `story_compiler.py`：`async compile_for_session(db, conv) -> StoryMapSpec` —— 复用会话详情路由同款消息查询（`Message.role in (user, assistant)`，时间序），叠加 `session_data_manager.get_map_state` 的 layers bbox 作宏观章相机素材；
- `camera_planner.py`：`bbox_from_geojson(fc) -> bbox | None`（服务侧要素扫描）+ re-export lib 纯函数；
- `export_packager.py`：`async build_session_bundle(...)` 组装并委托 lib 打包；
- `__init__.py` 显式 `__all__` re-export（对齐 `app/services/mapspec/__init__.py`）。

### 5.2 `app/api/routes/storymap.py`

| 端点 | 请求 | 响应 | 鉴权 |
|---|---|---|---|
| `POST /api/v1/storymap/compile` | `{session_id?}: str` 或 `{messages?, turn_trace?, title?}` | `StoryMapSpec`（dict） | session_id 路径挂 `require_owned_session`；无状态路径不挂 |
| `POST /api/v1/storymap/export` | `{spec: dict, layers?, mapspec?, format: "json"\|"html" = "json"}` | json → bundle dict；html → `text/html` + `Content-Disposition: attachment` | 无状态（spec 由调用方提供，已过会话层鉴权） |

路由在 `app/main.py` 以 `app.include_router(storymap.router, prefix="/api/v1", tags=["StoryMap"])` 挂载。

## 6. 前端契约

- `lib/api/storymap.ts`：`compileStorySpec(payload)` / DTO 类型（手写镜像 StoryMapSpec，字段 snake_case 原样透传）；
- `components/story/story-narrator.tsx`：滚动驱动叙事列。props：`chapters`（含 `camera?: {center,zoom,pitch,bearing}` 与 `widgetIds`）、`activeId`、`onActiveChange(id)`、`scrollLockMs=600`；内部 rAF 节流 scroll 监听 + `getBoundingClientRect` 判定活跃章（容器高 40% 线），程序化滚动（scrubber/播放）后 `scrollLockMs` 内忽略滚动驱动，防回环；
- `components/story/story-dashboard.tsx`：联动图表看板，`highlightedIds` 命中即渲染脉冲环（CSS `story-pulse`，`prefers-reduced-motion` 时静态边框）；
- `app/story/story-view.tsx` 升级：装载后 `compileStorySpec` 严格排在既有 messages/map-state 两次 fetch 之后，独立 try/catch 吞错 → `storySpec=null` 走 ADR-0147 本地派生；spec 命中时相机命令升级为 `{center, zoom, pitch, bearing}` 全参 fly_to；排版切换 `split`（左图右文）⇄ `immersive`（全屏地图 + 右浮叙事列）。

## 7. 测试与验收闸

- 后端 `tests/unit/test_storymap_orchestrator.py`：
  1. 8 步证据链 → ≥4 个逻辑递进章节且每章有合法 CameraKeyframe；
  2. 轨迹采样 `validate_track == []`（含 bearing ±180 跨越、重复关键帧零长 leg、单关键帧退化）；
  3. 打包：脱敏键 REDACTED、单文件 HTML 无外链、`</script>` 转义、manifest 计数正确、JSON round-trip；
  4. spec 校验：未知 chapter_id 的关键帧拒绝、pitch>60 拒绝；
  5. API 契约：compile（无状态路径）/ export（json+html）。
- 前端 `frontend/components/story/story-narrator.test.tsx`：滚动→onActiveChange→fly_to(pitch/bearing) 同步、同章不重复派发、图表高亮联动、reduced-motion 降级。
