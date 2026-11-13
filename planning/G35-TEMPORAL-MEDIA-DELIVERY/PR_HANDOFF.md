# PR_HANDOFF · G35

## 交付物

- branch：`zcode/g35-temporal-media-delivery-20261112T134717Z`
- worktree：`/home/kevin/projects/webgis/webgis-worktrees/g35-temporal-media-delivery-20261112T134717Z`
- base（创建时）：`ca7b3ec273f2b43336852951e2292a416b2238e6`（`master`）
- head / PR URL：待 push 后填写
- PR 正文文件：`planning/G35-TEMPORAL-MEDIA-DELIVERY/PR_BODY.md`

## 功能说明（可操作）

1. 在已绑定 temporal 的地图时间轴上点「时序动画媒体交付」按钮。
2. 面板显示真实能力（APNG 无损核心 / GIF 有损兼容 / 视频未部署）、真实观测清单、
   缺期与实际跨度、时长规则、固定窗口/分类披露、资源预估。
3. 选择格式、等时长或按真实间隔、停留、尺寸、fps、顺序、缺期策略、观测子集 → 预览计划
   （`plan_ref` / `plan_hash`）。
4. 提交后台任务；可离开页面，重开后从服务器恢复任务与成果；可取消。
5. 完成后下载：可播放 APNG（或 GIF）、PNG 数字帧 + SVG + manifest 证据 ZIP、manifest JSON。
6. Pi 侧用 6 个标准工具走同一条链：`capabilities → plan → submit(pending) → status → result`。

## 迁移 / 回退

- 无数据库迁移（`JobKind` 是加性枚举，`String(20)` 列）。
- 无 MapSpec schema 版本变更，存量 spec 行为不变。
- `Dockerfile` / `Dockerfile.prod` 运行阶段新增 `librsvg2-bin fontconfig fonts-noto-cjk`；
  缺席时能力探测返回 typed 503，不假装渲染成功（回退即移除媒体面板/工具调用）。
- 回退：revert 本 PR 即可；既有导出/下载/任务平台不受影响。

## 独立评审 / 修复 / 复审

R1 独立冷读发现 2×P0 + 5×P1 + 7×P2 + 9×P3；逐项修复并由同一 reviewer R2 复审。
详见 `REVIEW.md`。R2 静态冷读判定 2×P0 + 5×P1 全部清偿、无新 P0/P1，另列
U1–U8；作者全部闭环后本地复跑 61 后端 + 1 Pi 旅程 + 120 既有回归 + 35 前端 +
tsc/ruff/diff-check 全绿；Pi 旅程改用生产 `tool_search` deferred 发现路径，surface dump
零改写，既有 `default_active == NATIVE_TOOL_NAMES` 契约保持不变。

## 未测项 / 外部阻塞（如实）

- 未与真实浏览器同链联跑 Pi 子进程（UI 由 vitest 单独验证）；未跑 Playwright journey。
- 签名下载的实际授权是签名 URL（TTL 3600s）：沿用既有 export 路由，`.owner` 侧车在
  `session:<sid>` principal 口径下主要作存在性 fail-closed 检查，登录用户 uid 与之不等，
  因此不是「双闸门」；签名泄漏即可下载，不宣称第二道 owner 闸门。
- 未等待线上 CI（按任务要求）。
- MP4/WebM 未开放：主机 codec probe 通过，但 runner 部署镜像未声明 ffmpeg，
  因此只报告能力，不做编码、不按扩展名猜成功。
- 时变栅格/cube、外部底图、model3d、MVT 全图动画：typed 阻断或显式 partial 披露。
- Pi 旅程中 durable Celery enqueue 用进程内 worker 替代；durable job 平台本身由
  单测与框架既有语义保证。
- 宿主未提供可查询的 Goal usage budget 配置；「≥320,000,000 tokens 可用预算」未配置/未核验。
- 未在网络 clone Pi 子模块（HTTP/2 CANCEL），改用本地对象 + 同 pin 已构建 bundle 离线初始化；
  未改 gitlink / 构建锁定 / 部署契约。
