# LOOP_LEDGER · G35

宿主预算说明：本会话未提供可查询的 Goal usage budget 配置接口；任务要求「至少
320,000,000 tokens 可用预算」无法由本环境设置或核验，**如实记录为未配置/未核验**。
该数字不是消耗下限；按「达到验收即结束」执行，未为消耗预算制造无关工作。
使用 3 个 subagent 槽位：#1 后端只读侦察、#2 前端/Pi 只读侦察（均未参与实现）、
#3 独立 reviewer。任一时刻存活 ≤3，未递归派生。

| 轮 | 目标（一个用户闭环） | 追生产链 / 实现增量 | 验证 | 修正 | 状态 |
|---|---|---|---|---|---|
| L0 | 确认 base / PR / review / 源码 | `origin/master=ca7b3ec…`；open PR 0；25 个相关 PR reviews/inline=0；7 份 `planning/G2x/REVIEW.md`；`zcode/goal-temporal-map-products-v1` 已合并；无 G35/媒体实现 | git/gh 实读 | 主工作区旧 HEAD 不动，改用独立 worktree | ✅ |
| L1 | 固定 pin 的 Pi 环境 | `.gitmodules` → earendil-works/pi，gitlink `b4afdddf`=v1.5.2；网络 clone 失败（HTTP/2 CANCEL）→ 用本地对象 + G22 同 pin 已构建 bundle 离线初始化，隔离 gitdir；RPC `get_state` 实测成功 | node + vendor identity | 不改 gitlink/.gitmodules/lock | ✅ |
| L2 | 计划层闭环 | `contracts/capabilities/planning/freeze/store` | `test_planning.py` 14 例 | 阻断原因细化（外部瓦片 vs 时变栅格）；`MAX_PROPORTIONAL_GAP_MS` 从 30s 改为 10 年（年度资料比例不能被压平） | ✅ |
| L3 | 单帧真实渲染 + 编码 + 独立解码 | `render/encode/verify/manifest`；实读 `apply_time_window_to_doc`、`_temporal_note`、`compile_mapspec_to_svg_detailed`、G26 rsvg 口径 | `test_pipeline.py` 15 例 | 合并判据由 `digital_frame_id` 改为 **PNG 字节相同**；只跨段合并（保留 fps 拆帧）；`segment_durations_ms` 逐段精确映射；GIF magic 由 8 字节误判改为 6 字节；`segment_mapping_complete` 允许同段重复覆盖 | ✅ |
| L4 | 持久任务 / 取消 / 恢复 / 授权下载 | `runner/tasks/service/tools/routes` + `JobKind` 加性接线 | `test_jobs.py` 9 例 | **迟到成功不仅能保持 status，还必须不能登记成果**（`update_state` 终态字段一并保护） | ✅ |
| L5 | UI 与 i18n | `temporal-media.ts/panel.tsx` + 时间轴按钮 + 双 locale | vitest 18 例、tsc 0 错 | `useState` 字面量窄化 → 显式 `MediaSettings`；刷新按钮同时刷新任务状态 | ✅ |
| L6 | 固定 pin 真实 Pi 全链 | `tests/integration/test_pi_g35_…py` | 第 1 轮红：`plan_hash` 被生产 dispatch 上下文预算截断 → submit 校验失败；第 2 轮红：stub 从转义 JSON 提取动态值失败 | **生产修复** 16 位 `plan_ref` + owner 作用域预览计划解析；旅程改为 driver 用同一生产函数预计算脚本化值；JS 文件由测试直接落盘避免嵌套转义 | ✅ `verdict.fail=[]` |
| L7 | 故障面 / 资源 / 路由 | `test_faults/test_routes` | 51+ 后端例 | `build_frozen` 深拷（live 改图不污染冻结）；MVT 样本 typed 原因；`temp_est` 区分工作量与常驻内存；全局 HTTPException 处理器把领域 payload 放在 `data` | ✅ |
| L8 | 幂等边界（review 前自查） | `service.submit` | 新增 1 例 | 重复提交复用同一 job，避免 durable job 只入队一次却留下第二个永不执行的 pending 域任务 | ✅ |
| L9 | 独立 review R1 | 独立 reviewer 冷读 7079 行 diff | 2×P0 + 5×P1 + 7×P2 + 9×P3 | 见 REVIEW.md | ✅ |
| L10 | R1 修复（逐项） | `freeze/render/encode/verify/store/service/planning/tools/前端/测试` | 60 后端 + 1 Pi 旅程 + 120 既有回归 + 34 前端 + tsc/ruff 全绿 | P0-2 先试改共享 surface，实测破坏 4 个既有契约 → **完整回退**，改走生产 `tool_search` deferred 发现路径且零改写 dump | ✅ |
| L11 | R2 复审（同一独立 reviewer，静态冷读 7752 行） | 见 REVIEW.md | 2×P0 + 5×P1 全部清偿、无新 P0/P1；列 U1–U8 与 6 条必须动作 | U1 子集勾选保留、U2 GIF 折叠真实触发用例、U3 运行时磁盘配额 + GIF ×3、U4 oracle 命名、U5 死常量、U6 文档口径、U7 终态字段、U8 证据清单 | ✅ |
| L12 | R2 后复跑 | 全套 | 61 后端 + 1 Pi 旅程 + 120 既有回归 + 35 前端 + tsc 0 error + ruff/diff-check 全绿 | — | ✅ |
| L13 | 合流最新 master / commit / push / PR | 见 PR_HANDOFF.md | — | 待执行 | ⏳ |

## 当前 head / base

- base（创建时）`ca7b3ec273f2b43336852951e2292a416b2238e6`
- feature branch `zcode/g35-temporal-media-delivery-20261112T134717Z`
- worktree `/home/kevin/projects/webgis/webgis-worktrees/g35-temporal-media-delivery-20261112T134717Z`
- 合流前将重新 `git fetch origin master` 并复核新 PR/review。

## 未完成 / 明确边界（不宣称完成）

- MP4/WebM：codec probe 真实，但 runner 部署面未落实 → 本版不开放（`available=false` + reason）。
- 时变栅格/cube、外部底图、model3d、MVT 全图动画：typed 阻断或显式 partial 披露，不做插值/循环旧图。
- Pi 旅程中 durable Celery enqueue 以进程内 worker 替代；浏览器 UI 由 vitest 单独验证，
  **未与 Pi 子进程同链联跑**（如实分层，不写「同等覆盖」）。
- 未等待线上 CI；本地关键验证全部真实执行。
