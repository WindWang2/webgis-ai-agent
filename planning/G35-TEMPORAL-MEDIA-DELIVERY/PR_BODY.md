## G35 — 可核验的时序地图动画媒体交付

把已经存在的 `temporal_view.v1` 时序地图变成**可计划、可恢复、可下载、可独立检查**的动画成果：
用户从当前已绑定的真实观测帧出发，选择顺序/时间显示/画面尺寸/停留/格式，预览冻结计划后
由后台任务逐帧真实渲染、编码并独立解码核验，最终交付可播放动画 + PNG 逐帧证据包 + 可解释 manifest。
网页里能播放不等于媒体已交付；本 PR 不录屏、不伪造帧、不把静态 PDF/ZIP 冒充动画。

### 具体用户问题

- 旧状态只有播放/暂停/步进和静态导出（PDF/PNG/SVG、atlas 时间页），没有可下载的动画文件，
  也没有逐帧证据能证明「某年画面确实是那年数据、标签没提前一帧」。
- 缺期、跨时区日界、epoch 秒/毫秒、倒序、同画面不同观测、停留拆帧等时间语义没有可解释的成品映射。
- 用户在任务中平移/换样式可能造成前半旧数据、后半新数据的作品；编译器按当前帧重算地理范围
  也会让动画逐帧平移缩放。

### 行为增量

- **核心格式 APNG**：自写 streaming 容器 writer（acTL/fcTL/fdAT，逐帧 zlib，单帧常驻），
  Pillow 已是生产依赖，颜色无损 RGBA、逐帧整数毫秒时长、独立解码通过；编码阶段有真实墙钟预算。
  **GIF** 为兼容输出，全片共用一张全局调色板并披露 256 色与 10ms 时长量化；相同画面由
  writer 显式折叠并把合并后的容器帧映射写进 manifest（GIF 只接受 `fps=0`）。
  MP4/WebM 只做真实 `ffmpeg -encoders` probe，部署依赖未落实 → typed 拒绝。
- **固定地理窗口**：冻结时从**全部**要素（含静态栅格声明范围）求一次并集 extent，作为
  每一帧共同的 `bounds` 传给 `compile_mapspec_to_svg_detailed`；显式 layout frame extent 优先；
  无法确定时 typed 拒绝。不逐帧 fit、不重算分类/色域。
- **逐帧真实渲染**：复用 `apply_time_window_to_doc` 逐个 temporal 绑定层过滤（含窗外要素
  escape 检查）、既有 `_temporal_note` 写真实时间注记、rsvg-convert 栅格化、Pillow 逐帧解码；
  保存 PNG + SVG + 要素/窗口/指纹/诊断/checksum。不套用要求数值栅格的 G26 高层 PNG helper；
  静态数值栅格背景复用 G26 冻结绑定。沿用（held）段内容来自源观测，但窗口注记写本段缺期窗。
- **时间科学**：契约 `[start,end)` 与 timezone 直接消费，不重解析；缺期只 skip / 明确空态 /
  明确沿用（沿用段标明真实观测时间，绝不插值）；等时长 / 按真实间隔 Fraction 精确比值 +
  最大余数法补偿量化误差；数据时间单位 ≠ 展示 ms ≠ fps；
  observation / digital_frame / encoded_frame 三种身份在 manifest 完整映射。
- **冻结与复检**：一次读 CAS + 深拷冻结源/表达/图例/窗口/渲染器/字体，发布前复检修订与
  文档指纹；变化即中止，不发布混合 revision 作品。
- **持久任务**：复用 G23 `submit_durable_job` / `durable_job` / `finish_job` /
  `confirm_cancelled`，新增 `JobKind.temporal_media`；六阶段进度（总帧数已知才报百分比）、
  owner+计划+源+frame+renderer 指纹有界帧缓存（PNG/SVG/证据共享缓存，条目+字节双上限）、
  跨进程 `flock` 状态临界区、终态字段整体保护、取消杀后续帧并清理临时文件、
  派发失败写 failed 而不是留永久 pending 孤儿。
- **证据与下载**：独立 Pillow 逐帧解码容器，核对 magic/checksum/帧数/尺寸/逐帧时长/
  画面像素/时间标签/窗口注记/独立重算的要素窗/合并映射；故障注入（标签提前一帧、全片首帧、
  删除变化帧、容器损坏、时长错）必须红检。核验通过才原子发布 + `.owner` 侧车 + 签名 URL。
- **UI**：时间轴薄入口 + 独立媒体面板（能力、观测清单、缺期、时长规则、资源预估、
  提交/轮询/取消/按 created_at 恢复最近任务、动画/证据包/manifest 三个下载）。
  参数变更自动重新预览再提交，不改 live 游标、不解除 user_pinned。
- **Pi 标准通道**：6 个 typed 工具（能力/计划/提交/查询/取消/成果）与 UI 共用同一 service；
  按生产 ADR-0288 **deferred 面**注册（与 G26 `webgis_map_export` 同款），由 `tool_search`
  发现激活后按名直调；不改写 surface dump，不改 `NATIVE_TOOL_NAMES`/`default_active` 契约。
  `plan_ref`（16 位短句柄）避开生产 dispatch 的长字符串上下文截断；
  `submit` 返回 `pending`，`result` 只在 `completed && verification.passed` 后返回授权 ref。

### 复用与范围

复用：temporal 观测桶/时间过滤、MapSpec SVG 编译与 rsvg 栅格化、G23 durable jobs、
既有 export 目录/所有权/签名下载、G26 静态栅格冻结绑定、G22 Pi v1.5.2 surface。
未重建 agent loop / DAG / 工具注册表 / MapSpec store / 全局 ID / 撤销栈 / 事件总线 / 证据库。

共享入口只做局部加性接线：`app/main.py`、`app/tools/__init__.py`、`jobs/lifecycle.py`、
`api/routes/map.py`（3 个 media type）、时间轴组件、两个 locale、两个 Dockerfile。
不改 `vendor/pi` gitlink / `.gitmodules` / 依赖锁定 / RPC 内核 / `pi_native_surface.py` /
AGENTS.md / 技能 / 审批配置。Docker 运行阶段补 `librsvg2-bin fontconfig fonts-noto-cjk`
（缺席时能力探测 typed 503）。

### 实际验证（本地真实执行，未等线上 CI）

| 套件 | 结果 |
|---|---|
| `pytest tests/unit/temporal_media -q --no-cov`（单进程） | **61 passed** |
| `PI_JOURNEY_E2E=1 pytest tests/integration/test_pi_g35_temporal_media_journey.py`（公共 heavy 锁） | **1 passed**，`verdict.fail=[]` |
| 既有 Pi surface / 导出鉴权回归（10 个文件） | **120 passed** |
| `vitest run … --maxWorkers=1`（面板/API/时间轴/i18n/命令目录） | **35 passed**（合流前；合流后见下） |
| `tsc --noEmit` | **0 error** |
| `ruff check`（新增后端文件）/ `compileall` / `git diff --check` | 全通过 |

**合流后复跑**：本分支创建时 base = `ca7b3ec27`；提交前 `git fetch origin master`
发现主干已前进到 **`ba0c84b84`**（G29–G34 六个 PR #2094–#2099 已合并，36 个提交，
open PR = 0，六个 PR 的 GitHub reviews / inline / conversation 均为空）。已用普通
`git merge origin/master` 合流，两处同锚接线冲突（`jobs/lifecycle.py` 的 `JobKind`、
`tools/__init__.py` 的 `_TOOL_MODULES`）**双侧保留**：G30 `skill_job`、G34
`print_layout_tools` 与 G35 `temporal_media`/`temporal_media_tools` 共存。合流后
再次真实执行：后端 61、Pi 旅程 1、既有回归 120、前端 **34 passed / 35**
（1 例基线失败见下）、tsc 0 error、ruff/diff-check 全通过。同一独立 reviewer
另做合流后静态复核：双侧接线完整、master 侧 `bounds`/`default_active`/
`tool_search`/`session_id` 接口无漂移、无新 P0/P1，结论「可 push + 创建 PR」。

**1 例基线失败，与本 PR 无关**：合流后前端 `lib/map-commands/catalogue-contract.test.ts`
的「后端命令发射 ⊆ 前端命令目录」报幽灵命令 `steer`。`steer` 来自 master 的 G29
`app/api/routes/pi_interactions.py:252`；本 PR 未改任何 `frontend/lib/map-commands/`
或 G29 文件。已在**同条件干净 `origin/master` 临时 worktree** 上最小复现同一断言失败
（`expected [ 'steer' ] to deeply equal []`），确认为合入主干既有的基线问题，
按任务约束不放宽该断言、不修改他人文件，如实列为未解决外部项。

Pi 全链旅程（固定 pin `b4afdddf…` = v1.5.2，真实子进程 + 生产扩展 + 生产 surface dump 零改写
+ `/pi-tools/execute` + `ToolDispatchService` + 本域服务）：
`tool_search → capabilities → plan → submit(pending) → status → result` 全部真实 dispatch 无错误；
签名 URL 真实 HTTP 下载后独立解码 3 帧；证据 ZIP 中 manifest 显示
`verification.passed=true`、缺期 3、总时长 3000ms。`deferred_discovery=true`、
`tools_in_production_superset/deferred_contract/have_schema=true`，
`default_active_count=7`（既有契约不变）。
24 个必须覆盖场景、故障/资源实测与独立评审修复项逐项见
`planning/G35-TEMPORAL-MEDIA-DELIVERY/{TEST_MATRIX,REVIEW}.md`。

分层如实记录：Pi 子进程 / 后端 bridge / 领域服务**同链**；浏览器 UI 由 vitest 单独验证，
未与 Pi 子进程同链联跑；该旅程中 durable Celery enqueue 以进程内 worker 替代。

### Review / Fix / Re-review

独立冷读 reviewer（未参与实现）R1 给出 2×P0 + 5×P1 + 7×P2 + 9×P3；逐项修复后
R2 由**同一 reviewer** 静态冷读最新 7752 行 diff 复审：2×P0 + 5×P1 全部清偿、
无新 P0/P1 回归，另列 8 项 U1–U8 残留与 6 条 PR 前必须动作；作者全部闭环
（子集勾选保留、GIF 折叠分支真实触发用例、运行时磁盘配额、oracle 命名与声明、
死常量清理、文档口径统一），完成后复跑上表全部套件。完整 findings / 修复 /
证据见 `planning/G35-TEMPORAL-MEDIA-DELIVERY/REVIEW.md`。R2 为静态复审，
上表数字是作者在其修复后真实复跑的输出，不是 R2 现场复跑。
关键修复：冻结地理窗口传给编译器、Pi 改走生产 `tool_search` 发现路径
（不改写 dump、不破坏 4 个既有契约测试）、GIF 帧折叠与 fps typed 拒绝、
派发失败不留孤儿、跨进程状态锁与终态整体保护、前端参数变更/子集勾选一致性、
held 窗口注记、独立时间 oracle、编码墙钟与磁盘预算、工具契约字段如实。

### 最新 master / 相关 PR 兼容

创建时 base = `ca7b3ec27`；提交前 `git fetch origin master` 得到最新 base
**`ba0c84b84`**，已普通合并（无 force-push）。创建与合流时 open PR 均为 0；
#2087–#2093、#2094–#2099 的 GitHub reviews / inline / conversation comments 均为空
（独立评审记录在各任务本地 `planning/Gx/REVIEW.md`）。G29–G34 已合入主干，本 PR
按并行边界只做加性接线并双侧保留同锚冲突，不依赖其未合并代码；合流后已重跑
受影响回归（Pi surface / 导出鉴权 120 例 + 固定 pin Pi 全链旅程）。

### 迁移 / 回退

无 DB 迁移、无 MapSpec schema 版本变更；`JobKind` 为加性枚举（`String(20)` 列）。
回退 revert 本 PR 即可，既有导出/下载/任务平台不受影响；Docker apt 包缺席时媒体能力
如实返回 503 而不是假装渲染成功。

### 未测项与外部阻塞（如实）

- 未与真实浏览器同链联跑 Pi 子进程；未跑 Playwright journey；未等待线上 CI。
- 合流主干带来的 1 例前端基线失败（G29 `steer` 幽灵命令）未在本次修复：已在干净
  `origin/master` 最小复现，属既存问题、超出本任务所有权边界；不放宽断言、不改他人文件。
- MP4/WebM 未开放（codec 存在但 runner 镜像未声明 ffmpeg）。
- 时变栅格/cube、外部底图、model3d、MVT 全图动画：typed 阻断或显式 partial 披露。
- **签名下载的实际授权是签名 URL**：沿用既有 export 路由，`.owner` 侧车在
  `session:<sid>` principal 口径下主要作存在性 fail-closed 检查；登录用户 uid 与之不等，
  因此不是「双闸门」。签名 TTL 3600s，泄漏即可下载，不宣称第二道 owner 闸门。
- 宿主未提供可查询的 Goal usage budget；「≥320,000,000 tokens 可用预算」未配置/未核验。
- Pi 子模块网络 clone 失败（HTTP/2 CANCEL），改用本地对象 + 同 pin 已构建 bundle 离线初始化，
  未改 gitlink / 构建锁定。
