# AGENT-12 审查纪要：自主 StoryMap 空间叙事编排生成器（ADR-0196）

- 分支：`agent/12-autonomous-storymap-narrative-orchestrator`
- 基线：`origin/master` @ 3eb2cc6a（独立 worktree 开发）
- 设计文档：`docs/adr/0196-autonomous-storymap-orchestrator.md` + `docs/dev/storymap-orchestrator-spec.md`

## 1. 交付概要

把一次分析推演（GisTraceChain / ReplayTrace 证据链或会话消息）自动编译为"引言 → 宏观态势 → 重点解剖 → 动态推演 → 决策建言"五弧多章节 StoryMapSpec，配套贝塞尔平滑相机轨迹、图文联动看板与一键脱敏离线专报导出；`/story` 前端升级为"编排优先、本地派生兜底"的双数据源页面，支持左图右文 / 沉浸全屏双排版与滚动驱动相机漫游。

## 2. 变更清单

后端（分层纪律：纯算法在 `app/lib/storymap/`，IO 壳在 `app/services/storymap/`）：

| 文件 | 内容 |
|---|---|
| `app/lib/storymap/spec.py` | `StoryMapSpec` 领域模型族（pydantic v2，`extra="allow"` additive-only）：CameraKeyframe（pitch 0–60 硬约束）、StoryChapter（五弧 Literal）、LinkedWidget、AudioNarrative、时长估算/解说词剥离纯函数 |
| `app/lib/storymap/story_compiler.py` | 三形状输入归一（GisTraceChain 枚举名/int stage、ReplayTrace、消息降级）→ 18 阶段五弧分桶保序归纳 → 结论文本/统计摘要/图表产物/解说词提炼 + 逐章 bbox 相机 |
| `app/lib/storymap/camera_planner.py` | bbox→位姿（弧角色 pitch 基准 + 经纬张角 zoom + 构图 bearing）；Catmull-Rom→三次贝塞尔轨迹插值（Newton+二分缓动求解、bearing 最短弧、零长 leg/单帧奇点守卫、NaN 清洗）；`validate_track` 连续性闸 |
| `app/lib/storymap/export_packager.py` | StoryBundle 组装 + 键名黑名单递归脱敏（默认开）+ 自解压单文件 HTML（`</`、`<!--` 转义，零外链 vanilla 查看器） |
| `app/services/storymap/{__init__,story_compiler,camera_planner,export_packager}.py` | 会话消息装载编译（复用会话详情路由同款查询）、GeoJSON bbox 扫描、图层 FC 组装；`__init__` 显式 `__all__` re-export |
| `app/schemas/storymap_schema.py` | compile/export 请求模型（xor 校验 → 422） |
| `app/api/routes/storymap.py` | `POST /api/v1/storymap/compile`（无状态）、`POST /api/v1/storymap/sessions/{id}/compile`（`require_owned_session` + async db）、`POST /api/v1/storymap/export`（json/html） |
| `app/main.py` | 一行挂载 storymap 路由 |
| `tests/quality/snapshots/api_contract_fields.json` | 字段契约快照增量（仅新增 3 端点，官方命令 `API_CONTRACT_FIELDS_UPDATE=1` 刷新，#1217 闸通过） |

前端：

| 文件 | 内容 |
|---|---|
| `components/story/story-narrator.tsx` | 滚动驱动叙事列：rAF 节流滚动→活跃章判定（`pickActiveChapter` 纯函数，容器高 40% 激活线）；程序化滚动 600ms 驱动锁 + `scrollDrivenRef` 来源标记防回环且不吞快速连续滚动；`prefers-reduced-motion` 定位降级 `auto` |
| `components/story/story-dashboard.tsx` | 联动看板：活跃章节 widget 高亮脉冲环（CSS 动画，reduced-motion 全局降级覆盖），chart kind 复用 `ChartCore`/`adaptChartData` |
| `lib/api/storymap.ts` | DTO 手写镜像 + `isValidStorySpecDto` 形状门卫 + `compileStorySpec`/`exportStoryBundle`（apiFetchBlob）+ `specToNarratorView` 视图适配 |
| `app/story/story-view.tsx` | 编排优先（compile 严格排在既有两次请求之后、独立吞错、垃圾形状门卫 → 静默回退 ADR-0147 本地派生）；统一播放序列 `playlist`/`playPos`；编排模式全参 fly_to（center/zoom/pitch/bearing）；沉浸排版切换；离线专报导出按钮；PDF 导出双模式感知 |
| `app/story/story-orchestrated.test.tsx` | 编排模式集成测试：徽标/spec 章节渲染/scrubber 收缩/全参 fly_to/看板挂载 + 垃圾 spec 静默回退 |
| `components/story/story-narrator.test.tsx` | 滚动→fly_to 全参派发恰好一次/同章去重/连续滚动分步跟进/图表高亮联动/reduced-motion 降级/pickActiveChapter 退化面（10 用例） |
| `messages/{zh-CN,en-US}/story.json` | 新增 orchestrated/immersive/exportBundle 等键（双语） |
| `app/globals.css` | `storyPulseRing` 脉冲环 keyframes |

## 3. 关键设计取舍

1. **证据链为叙事真相源，消息只是降级输入**——18 阶段规范序天然映射五叙事弧，纯函数可单测锁定，不引入 LLM 不确定性（ADR-0196 决策一/四）。
2. **轨迹插值用 Catmull-Rom→贝塞尔 + 最短弧 bearing**——C1 连续、局部支撑、跨 ±180° 无大回环；零长 leg/单帧是显式奇点直接输出重合采样；`validate_track` 以相邻采样跳变为闸锁"无突变"。
3. **前端编排严格可降级**——compile 请求排在既有 fetch 链之后且独立 try/catch + `isValidStorySpecDto` 双门卫，旧后端/断网/垃圾响应一律静默回退本地派生，既有 24 个 story 测试零改动通过。
4. **脱敏是打包默认项**——键名黑名单递归 REDACT（token/api_key/owner_token 等），HTML 单文件零外链可离线双击打开。
5. **契约闸显式扩面**——字段契约快照仅增量新增 3 端点，未触碰既有端点签名。

## 4. 测试与验证

后端（`.venv/Scripts/python -m pytest`，asyncio_mode=auto）：

- `tests/unit/test_storymap_orchestrator.py`：**39/39 通过**。覆盖：8 步链路→4 逻辑递进章节且逐章合法镜头视角（含中心点落 bbox 邻域断言）；全弧 5 章；ReplayTrace 形状；统计/图表收割；解说词逐章生成；消息降级两章；轨迹连续性 `validate_track == []`（含 antimeridian 最短弧、重复关键帧零长 leg、单帧退化、双帧长腿采样密度、leg 内 zoom 单调）；脱敏递归 REDACT；单文件 HTML 无外链/`</script>` 转义/manifest 计数/JSON round-trip；API compile（无状态 200/422）与 export（json/html + attachment 头）；`bbox_from_geojson`。
- 全量 `tests/unit` 回归：见 §5 验证记录。

前端（vitest + testing-library）：

- `components/story/story-narrator.test.tsx`：**10/10 通过**。
- `app/story/story-orchestrated.test.tsx`：**2/2 通过**（编排命中 + 垃圾 spec 回退）。
- 既有 story 测试（page/story-view/chapters/narrative-export）：**24/24 零改动通过**。

## 5. 验证记录（本分支实测）

- `pytest tests/unit/test_storymap_orchestrator.py -v` → **39 passed**
- `pytest tests/unit`（全量回归，48min 实跑）→ **11717 passed / 38 failed / 118 skipped**。对 38 个失败逐项做了基线对照（在 `origin/master` 同 commit 的干净 worktree 上跑同一批用例）：
  - **35 个在基线上同样失败** —— 全部是环境固有失败：`test_runtime_validator.py`（headless 浏览器 lane，`REQUIRE_BROWSER` nightly 域）、`extensions_platform/test_resource_limits|streaming_v3`（bwrap/rlimit 为 Linux 专属语义）、`test_data_fabric_local_path_guard`（Windows 符号链接语义）、`test_file_adapters_v2`（pmtiles 真实文件 fixtures）、`test_llm_http_lifecycle`（真实 socket）等；
  - `geocompute_v7_cluster ×2`：分支空闲单测下**通过**（全量跑时 CPU 争用导致的调度抖动）；
  - `pi_bridge_leak ×1`：分支空闲单测下**通过**（tracemalloc 字节校准断言的边缘抖动，测试注释自述"CI 边缘抖动的根因"）；
  - 结论：**本分支零回归**；本分支自身触达的面（storymap lib/services/API、契约闸、story 前端）全绿。
- `pnpm vitest run`（前端全量）→ **398 文件 / 3663 tests 全部通过**
- `pnpm lint`（eslint --max-warnings 0）→ 通过
- `pnpm typecheck`（双 tsconfig）→ 通过
- 开发期发现并修复的问题：
  - lucide-react 无 `PackageDown` 导出（`<undefined/>` 渲染崩溃）→ 经 ErrorBoundary 组件栈定位，换 `HardDriveDownload`；
  - 滚动驱动变更被外部变更锁误吞 → `scrollDrivenRef` 来源标记；
  - jsdom rAF 定时器时序 → 测试冲刷辅助 `scrollAndFlush`；
  - editable 安装因 setuptools 平铺布局失败（环境既有问题，与本分支无关）→ 按 `pytest.ini pythonpath = .` 直跑；
  - `#1217` 字段契约闸按设计扩面（`API_CONTRACT_FIELDS_UPDATE=1` 官方刷新，仅新增 3 端点）。

## 6. 风险与回滚

- 回滚面：后端摘除 `app/main.py` 一行 include 即回到 master 行为（新增模块均为孤立新文件）；前端 compile 失败路径本就是一等公民，忽略响应即回退。
- 已知限制：v1 解说词为文本 + 时长估算（无 TTS，`AudioNarrative.voice` 为后向扩展位）；轨迹连续性阈值为采样密度相关参数（samples_per_leg=16 默认下远低于阈值）；会话编译走消息降级路径（turn 级 trace 挂接留待 harness 录制接通后直供）。
