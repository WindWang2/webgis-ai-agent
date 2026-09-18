# ADR-0196: Autonomous StoryMap Orchestrator——自主空间叙事编排生成器 v1

- 状态: Accepted
- 日期: 2026-09-15
- 线: agent-swarm/12-autonomous-storymap-narrative-orchestrator
- 关联: ADR-0147（/story 会话回放模式，本 ADR 的前端演进基底）、ADR-0103 §十（GisTraceChain 18 阶段证据链，叙事主线的信息源）、ADR-0144（story.* i18n 键化）、MapSpec schema（`app/lib/cartography/mapspec_schema.py`，相机/样式词表来源）

## 1. 背景

现状盘点（agent 12 立项时点）：

- `/story` 路由已按 ADR-0147 实现**会话回放式** StoryMap：章节 = 消息逐条派生（1 消息 1 章节），相机只从消息内 ```json 围栏里的 `fly_to` 被动提取（center/zoom），无 pitch/bearing，无主动轨迹规划；
- 叙事结构是**平铺的消息转写**，没有"引言 → 宏观态势 → 重点解剖 → 动态推演 → 决策建言"的叙事弧（narrative arc）归纳；分析全过程的价值浓缩依赖读者自己读完全部消息；
- 后端没有任何服务参与叙事编排：`/story` 的全部智能都在前端纯函数（`frontend/app/story/chapters.ts`）；
- 缺少镜头时间序列轨迹规划（Camera Keyframe Animation）：宏观 → 微观切换无平滑俯冲（fly-to）曲线，无法与文字观点、图表指标同频联动；
- 缺少一键脱敏导出独立离线交互汇报包（Standalone Interactive StoryBundle）的能力——现有叙事 PDF 是逐章截屏的静态产物。

真正缺口：**把一次分析推演的证据链（GisTraceChain / ReplayTrace）自动编译为带镜头轨迹与联动图表的多章节空间叙事**，并使其可离线分发给无后端的决策场景。

## 2. 决策

### 决策一：叙事编排的真相源是证据链，不是消息文本

`StoryMapSpec` 由 `GisTraceChain.as_dict()` / `ReplayTrace.as_dict()` 形状的 trace 编译而来（stage 分桶映射到叙事弧），消息文本只作为 trace 缺席时的降级输入。理由：证据链有规范阶段序（18 阶段）、有统计摘要与结论文本，是比聊天消息更结构化的叙事素材；且编译逻辑是纯函数，可在无 DB、无 LLM 的单测里锁定。

### 决策二：纯逻辑进 `app/lib/storymap/`，IO 服务进 `app/services/storymap/`

沿用制图先例（"制图规则本体在 `app/lib/cartography`（纯函数、无 IO）；`app/services/cartography` 承载需要 DB/会话 IO 的服务"）：

- `app/lib/storymap/spec.py` — `StoryMapSpec` 领域模型（pydantic v2，`extra="allow"` additive-only 演进，对齐 MapSpec `_SpecModel` 纪律）；
- `app/lib/storymap/story_compiler.py` — trace → 章节归纳（叙事弧分桶 + 结论文本/统计摘要提炼 + 联动图表挂接 + 解说词合成）；
- `app/lib/storymap/camera_planner.py` — Bbox → 相机关键帧（pitch 0–60°、bearing、zoom）+ Catmull-Rom→三次贝塞尔平滑轨迹插值 + 连续性校验（无突变、无奇点）；
- `app/lib/storymap/export_packager.py` — 脱敏 + 自包含单文件 JSON/HTML 打包（零外链，可离线打开）；
- `app/services/storymap/{story_compiler,camera_planner,export_packager}.py` — 会话/DB 编排壳（消息装载、图层 bbox 提取、bundle 组装），算法全部委托 lib。

### 决策三：相机轨迹用"Catmull-Rom 控制点 + 三次贝塞尔段"插值，全程无饱和奇点

- 中心点路径：相邻关键帧中心做 Catmull-Rom 样条，每个 leg 转换为三次贝塞尔（B0=P1, B1=P1+(P2−P0)/6, B2=P2−(P3−P1)/6, B3=P2），端点用复制补pad —— 局部支撑、C1 连续、不过原点抖动；
- zoom / pitch / bearing：每 leg 三次贝塞尔缓动（cubic-bezier(0.25, 0.1, 0.25, 1)），bezier 参数用 Newton–Raphson 从 x 解 t（二分兜底）；
- bearing 走最短弧（Δ 归一到 (−180°, 180°]），跨 ±180° 不产生 350° 大回环；
- 零长 leg（重复关键帧）与零时长轨迹是显式奇点：采样器直接返回重合采样，不除零；
- `validate_track()` 以相邻采样最大跳变（center 度数 / zoom 级 / pitch 度 / bearing 度）为闸，超阈值即 fail —— 测试以此锁定"无突变"。

### 决策四：叙事弧固定五段，章节按证据链阶段分桶归纳

`introduction（引言）→ macro_situation（宏观态势）→ focus_dissection（重点解剖）→ dynamic_simulation（动态推演）→ recommendation（决策建言）`。阶段→弧的映射：S1–2→引言、S3–8→宏观、S9–12→解剖、S13–16→推演、S17–18→建言；空桶跳过，保序输出。每章相机由该桶步骤的 bbox 并集 + 弧角色（宏观=俯视低 pitch，解剖=低空高 pitch）确定。

### 决策五：导出包是"自解压单文件"，脱敏是打包默认项

`StoryBundle = {schema_version, generated_at, spec, data:{layers, mapspec}, manifest}`；HTML 形态把 bundle 以 `<script type="application/json" id="story-bundle">` 内嵌（`<`/`>` 转义为 `\u003c`/`\u003e`，阻断 `</script>` 早闭合），配一段零依赖 vanilla 查看器，**不引用任何 CDN/外链**；模板占位符经单遍 `re.sub` 填充（用户标题含 `__VIEWER__`/`__JSON__` 字面量不劫持）。脱敏按键名**词元边界**匹配递归 REDACT（token/secret/key/sessionid/api 等词元命中真实敏感键，capital/author 等普通键不误杀；`metadata.session_id` 为显式脱敏项），默认开启、显式 `sanitize=False` 才关闭。

### 决策六：前端演进为"编排优先、本地派生兜底"，双排版 + 滚动驱动

- `story-view` 装载会话后尝试 `POST /api/v1/storymap/compile`；成功 → 使用后端 `StoryMapSpec`（章节、镜头关键帧、联动图表、解说词）；失败（旧后端/网络断/非 JSON/DTO 校验失败）→ 静默回退 ADR-0147 本地派生路径，**既有测试与行为零破坏**（编排请求严格排在既有两次 fetch 之后且独立吞错；装载态先行释放，spec 迟到不覆盖用户已进行的 seek/播放——播放启动亦计为用户导航）；
- 排版双模式：`split`（左图右文，本 ADR 后的默认）/ `immersive`（地图全屏沉浸 + 右侧浮层叙事列）；
- 滚动驱动：`StoryNarrator` 组件滚动位置 → 活跃章节变更（rAF 节流 + 程序化滚动 `scrollLockMs`（默认 800ms）锁防回环；锁窗内滚动记 pending、锁到期补测一次且补测前复检新锁防劫持）→ 父层 `dispatchAction({command:'fly_to', params:{center, zoom, pitch, bearing}})`；联动图表按活跃章节高亮（脉冲动画，`prefers-reduced-motion` 降级）。

### 决策七：API 面收窄为三个端点

`POST /api/v1/storymap/compile`（无状态：trace 或 messages + 可选 session_id/turn_id/title → StoryMapSpec）、`POST /api/v1/storymap/sessions/{id}/compile`（会话路径，挂 `require_owned_session`）与 `POST /api/v1/storymap/export`（spec + layers + mapspec → JSON bundle 或自包含 HTML）。两端点共享边界门卫：JSON 深度上限（`MAX_PAYLOAD_DEPTH=64`）超限、非法 ts、模型校验失败一律 422（响应体浅层化，不递归编码深层输入）；孤立代理字符在编译/打包前剥除，极端数值（±1e308 zoom、`10**400` ts）不泄漏 500。

## 3. 后果

- 正面：分析推演的价值传递有了自动化"报告引擎"；相机有了确定性的平滑轨迹与可校验的连续性闸门；离线专报让成果脱离服务端分发；证据链→叙事弧的映射纯函数可单测锁定，不依赖 LLM 也不引入不确定性。
- 代价/风险：`StoryMapSpec` 是新的跨端契约（后端 pydantic / 前端 TS 手写镜像），需要 additive-only 演进纪律；前端 story-view 复杂度上升（双数据源 + 双排版），以"编排优先、本地兜底"与独立组件（`components/story/`）控制；自动归纳的叙事质量受证据链完整度影响（completeness 低的 turn 降级为消息转写）。
- 回滚：后端整体位于 `app/lib/storymap/**` + `app/services/storymap/**` + `app/api/routes/storymap.py`，从 `app/main.py` 摘除一行 include 即回到 master 行为；前端回退 = 忽略 compile 响应（compile 失败路径本来就是一等公民）。
- 测试面：`tests/unit/test_storymap_orchestrator.py`（8 步 trace→4 章节、轨迹无突变/奇点、词元边界脱敏与 session_id 策略、单文件打包与占位符单遍填充、数值/深度/代理字符边界 → 422 契约、API 契约）；`frontend/components/story/story-narrator.test.tsx`（滚动驱动镜头同步、滚动锁补测与新锁顺延、aria 语义、图表高亮联动）；`frontend/lib/api/storymap.test.ts`（全函数 DTO 门卫）；`frontend/app/story/story-orchestrated.test.tsx`（迟到 spec 不劫持 seek/播放、会话切换清除）。
