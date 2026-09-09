# 05 — Review Findings（两轮真实评审记录）

## Review Round 1 — Subagent-A（架构/正确性）

前置：Phase B 的架构挑战亦由 Subagent-A 承担（发现 C-1 引擎守卫绕过 / C-2 全量 doc CAS 旁路，
已先行并入设计修订 01-architecture.md「R1 修订」节）。

| 级别 | 项 | 修复 |
|---|---|---|
| CRITICAL | C-1 accept 不回显 subprotocol → 真实浏览器握手必败 | `accept(subprotocol=mode)`；ws.py 同款语义 |
| CRITICAL | C-2 降级对账缺失 + 重连循环触限流死循环 | visibilitychange/degraded 15s revision 探测（meta=1 单飞）；4029 → ≥60s 冷却退避 |
| MAJOR | M-1 presence 广播未裁剪（可冒充 clientId） | 广播路径走 `_sanitize_patch`，clientId 服务端权威 |
| MAJOR | M-2 delta create 忽略 patch.collapsed（前后端镜像分歧） | 后端 create 读 `patch.get("collapsed", False)` |
| MAJOR | M-3 delta 事件捎带全量 doc → 大 doc 退化为全量 refetch | delta 事件只带 delta；前端本地应用（绝对值语义幂等），漂移才 refetch |
| MAJOR | M-4 全量路径 409 静默丢本地编辑 | 先捕获本地脏态 → diff(服务端 doc, 本地) 作 rebase 候选，同一单次限定 |
| MAJOR | M-5 rebaseAttempts 被远端水合重置 → 单次限定失效 | 移除该重置点；只留提交成功/会话切换 |
| MAJOR | M-6 undo 删除已建组会级联吞他人并发子组 | 重放前把闭包内现存子组提升为根（cascade-safe inverse） |
| MAJOR | M-7 presentation 采纳的全局 revision 门丢合法事件 | 去门（单层绝对值幂等）；`setMapSpecRevision` 单调保护保留 |
| MINOR | m-1..m-7 | 全部修复：悬空键过滤对齐 / 64KB delta 预算执行 / 会话游标与重连计数重置 / 节流合并末态 / armed 门防未恢复水合 / op 中文标签词表 / Redis 首探立即放行 |
| NIT | ref task 引用 / 层次倒置 / 注释失准 / 无差分 harness / 固定 sid flake | task 引用集已修；其余披露于 PR follow-up |

## Review Round 2 — Subagent-B（性能/安全/并发/UX）

| 级别 | 项 | 修复 |
|---|---|---|
| CRITICAL | C-1 collab store 快照恒定引用 → useSyncExternalStore 永不重渲染（协作 UI 失明） | snapshot 改 version 号（getCollabSnapshot），渲染体读 getCollabState() |
| MAJOR | M-1 presence 节流空转（presence_pending 未接线 → 窗口内更新整体丢失） | 分支真正写入 pending；flush task 无条件清引用 |
| MAJOR | M-2 QueueFull 只标记不断开 → 僵尸连接 | 哨兵入队 + `_schedule_close`；send_loop 异常同关 |
| MAJOR | M-3 降级注册表空桶不回收 → 256 会话永久耗尽 | leave/release 后空桶 pop；join 前清扫复用名额 |
| MAJOR | M-4 前端重连竞态叠出多条并发 WS | connect 入口 readyState 守卫 + 回调 `ws===socket` 守卫 + visibility CONNECTING 不动作 |
| MAJOR | M-5 心跳失效整会话 L1（3.2 次/秒缓存颠簸） | `_fresh_revision`/meta 分支删除 invalidate（定向 HGET 天然新鲜） |
| MAJOR | M-6 sync 全量 doc 读放大（~100 次/秒/IP） | sync 独立预算 2/s（突发 4），超额静默丢 |
| MAJOR | M-7 死接线（artifact 对账无调用方；租约 UI 不可达） | 对账接入 onopen + poll（整体替换 stale 集合）；租约随图层选择 acquire/release |
| MINOR | m-1..m-8 | 全部修复：finally 取消 flush / batch 事件后台化（随 M-7 一并核查）/ scale 比值外推 / normalize O(n²)→O(n) / 非法 key 不广播 / close reason 统一 / 4001/4003 终态 / connecting 保留 degraded |
| NIT | 死常量已清；bus 双编码/引擎→collab 反向 import/leases cjson——记录 follow-up |

## 验证（修复后）

- 前端：288 文件 / 2762 例全绿；lint `--max-warnings 0` 0 问题；tsc 双 tsconfig 0 错误；`next build` 成功。
- 后端：tests/quality（333）+ tests/test_collab_v6.py（29）+ engine/ws/world-state/registry 邻接回归全绿；ruff 全部改动文件通过。
- 两轮评审的测试有效性抽核：引擎/门面路径为真实调用（非 mock-only）；Round 2 发现的 C-1（UI 失明）与 M-1（节流空转）恰为 Round 1 测试盲区，已分别以组件渲染路径与节流分支直测补强（collab-v6.test 的 presence join/update/leave 与 workspace.test 的 CollabBar 渲染断言）。
