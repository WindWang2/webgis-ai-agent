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
- 已知限制：v1 解说词为文本 + 时长估算（无 TTS，`AudioNarrative.voice` 为后向扩展位）；`validate_track` 阈值为每样本绝对量（对欠采样长 leg 会触闸，规格书 §3.3 已注明该耦合并备"闸门正向"测试）；会话编译走消息降级路径（turn 级 trace 挂接留待 harness 录制接通后直供）；无状态 `compile`/`export` 不挂鉴权守卫（ADR-0196 决策七 + 仓库 auth 闸 allowlist 登记，master 收敛提交已确认该决策）。

## 7. 加固轮（独立对抗评审闭环）

第 1 轮 PR 合并后（merge d8d2b040），对已并入 master 的实现做了一轮**独立对抗评审**（3 个只读评审面：后端算法/安全、前端组件/竞态、发布门禁审计），发现并修复了以下确定性缺陷（每条先复现、再 TDD 红灯、再修复转绿）：

| # | 缺陷（评审发现） | 复现证据 | 修复 |
|---|---|---|---|
| P1 | 离线专报查看器把数据面文本直接 `innerHTML` 拼接 → 会话正文（用户可控）中的 `<img onerror=…>` 在导出的单文件报告里可执行 | 实跑：恶意 narrative 原文出现在 HTML | 查看器全量 `esc()` HTML 实体转义；内嵌 JSON 改 `\u003c/\u003e` 转义（合法 JSON 转义，严格解析无损还原），`</script`/`<!--` 一并天然阻断 |
| P2 | `<!--` 被替换为非法 JSON 转义 `\!` → 整份报告"数据损坏" | 实跑：严格解析 `Invalid \escape` | 同上（废除 `<\/`/`<\!--` 补丁方案） |
| P2 | GeoJSON 扫描只取首个顶点 → Polygon/LineString 图层 bbox 全错 | 实跑：Polygon → 单点 bbox | `iter_coord_points` 全顶点递归（任意嵌套层级），服务壳同步改用公共走子 |
| P2 | 跨 ±180° 经线的 bbox → 相机丢到大西洋（center [0,0]、zoom 触底） | 实跑：[170,-10,-170,10] → [0,0] | 展开域求中心 + 经度归一；`validate_track` 经差走最短弧不误报 |
| P2 | `center`+`zoom` 载荷忽略 zoom → 相机钉死 zoom=18 | 实跑：zoom 11 载荷 → kf.zoom 18 | 按 `span=360/2**(zoom-1)` 合成观察范围 |
| P2 | NaN/None 穿透 → 无鉴权端点 500（响应序列化炸/TypeError） | 实跑：NaN bbox、ts=null | `allow_inf_nan=False` + 源侧有限性过滤 + `_coerce_ts` 显式 ValueError + 路由 `except (ValueError, TypeError)` → 422 |
| P2 | 前端门卫只查 schema_version/chapters → 缺字段 keyframe 过门后渲染期崩溃（无 error boundary = 整页白） | 实跑：`camera_keyframes:[{}]` 崩 | `isValidStorySpecDto` 深校验 + `specToNarratorView` try/catch 双保险 |
| P2 | immersive 排版 `relative`+`absolute` 类并存被 Tailwind 声明序判 relative 胜 → 全屏/浮层双双失效 | 构建产物 CSS 序核实 | position 整体进分支；看板 `calc()` 偏移为右浮面板让位 |
| P2 | 会话切换残留（renamingId/pdfProgress/mapFade）+ 在途 PDF 向新会话写旧章节 → 相机飞旧机位 | 代码路径核实 | 装载清空清单补齐 + 会话快照守卫在途导出 + seek/播放标记防 spec 迟到落位覆盖用户操作 |
| P2/P3 | `specToNarratorView` 取帧条件恒真（死逻辑）；脱敏精确匹配弱于 `bound_meta` 语义；重复 chapter id 静默覆盖；trace 桶全空违背"降级两章"规格；Message.content=null 渲染 "None"；stats 非标量落 Python repr；滚动锁吞事件无补测 | 逐项实跑 | 取 t 最大者；子串折叠匹配（`client_secret`/`x-api-key` 变体命中）；id 唯一校验；桶空回退 messages；content 折叠空串；紧凑 JSON；锁窗 pending 补测 + 锁窗 800ms |

**加固轮验证数据（本分支重放后实测）**：

- 后端 `pytest tests/unit/test_storymap_orchestrator.py -v` → **52 passed**（39 原有 + 13 加固；加固测试先红后绿）
- 前端 `lib/api/storymap.test.ts` + `components/story` + `app/story` → **46 passed**（含 CJK i18n 门禁）
- 前端 lint（--max-warnings 0）/ typecheck（双 tsconfig）→ 通过
- 契约门：openapi 字节一致闸（docstring 说明移出 schema 面，零字节漂移）、字段契约、scope matrix、auth 闸、api-docs drift → 全绿；drift 报告 + 生成物账本按官方流程刷新（`gen_drift_report.py` + `check_generated_staleness.py --update`）
- v3 质量集成闸（coordination/release/manifest）→ 44 passed
- 环境说明：加固轮开发中途共享工作区发生并发清理（兄弟 worktree 与本分支工作区目录被环境进程移除），本分支在主仓重建后按上下文完整重放全部修复并以相同测试套件复验全绿（commit 5fd6c405）。
