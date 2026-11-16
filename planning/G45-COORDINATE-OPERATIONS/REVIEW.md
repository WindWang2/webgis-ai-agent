# G45 REVIEW — 独立评审记录

## Round 1（2026-11-16，未参与实现的 reviewer，只读）

- 结论：**GO-WITH-FIXES**（无 P0；2×P1 + 3×P2 + 8×P3）
- 评审方式：`git diff 655f769ca..HEAD` 逐文件 + /tmp 探针实证（幂等世代
  重跑端到端复现、progress 协程线程内未 await 实证）+ 复跑域测试 52 项核实
- 抽查确认的正面项：candidate_id 内容确定性签发无注入面（C11 实测）、迁移
  DDL 与 model CHECK 逐字一致、trusted_session_id 回退与 G38 同语义、
  strict gate 三态 fail-closed 有真实测试、all-or-nothing 主链成立、
  oracle 独立性成立（Redfearn 教书级数非 pyproj 搬运）、Pi 旅程读权威事实、
  OpenAPI/i18n/前端类型自洽、C35 NOT RUN 如实。

### 缺陷与处置

| 级别 | 项 | 处置（commit `fix(g45): Round-1 …`） |
|---|---|---|
| P1 | 幂等只查 base_key 精确匹配，世代键 completed/active 不可复用 | LIKE base_key% 前缀匹配 + 2 项回归 |
| P1 | worker 线程 progress 协程 100% 静默丢弃（ensure_future 语义误用） | run_coroutine_threadsafe + owner loop 传入 + Event 断言回归 |
| P2 | z_policy=reject / out_of_area_policy=fail 接受但未实现 | reject 实现（源 Z 证据 typed 拒绝）；fail 词表摘除 + 双拒 + 2 项回归 |
| P2 | 成果整文件 json.loads 每次分页重复 | load_output_fc LRU=2 mtime 缓存 |
| P2 | 崩溃恢复无 lifespan 接线 | recover_all()（全局收敛 + >1h staging 清理）接入 main.py |
| P3 | gate 对 worker/attempt=None fail-open | None=无法确认 → 阻断（回归覆盖） |
| P3 | network_enabled 仅入指纹不强制 | listing/lock typed 拒绝 |
| P3 | 取消命中发布门显示 failed | → cancelled 终态（C39 期望同步） |
| P3 | 预览非有限不区分输入/输出 | 错误 details phase + findings 分键（回归覆盖） |
| P3 | display 变换阻塞事件循环 | 入 _EXEC_POOL |
| P3 | 幂等键 [:255] 截断 | sha256 摘要 |
| P3 | 台账失败伪造未登记 ref | output_ref=None + ledger_registration 披露 |
| P3 | 面板不恢复 preview | 水合 plan.preview（!preview_stale） |
| P3 | staging 复用注释失真等小项 | 注释修正（随相关修改） |

## Round 2（复审，原 reviewer，fix commit 71b2a4526 → 本分支最新）

- 结论：**GO（0 阻塞）**——两个 P1 实证修复成立（reviewer 在 /tmp 复刻
  Round-1 失败场景：exec3 复用同 run、`idempotent_reuse=True`；进度协程按
  生产同构 wiring 真实送达 33/66/100 三值）；P2/P3 批次全部通过（含取消
  终态实测确定性地为 cancelled）。
- Round-2 提出 5 项非阻断后续建议，其中 3 项当场收敛（本分支后续 commit）：
  ① C39 断言钉死确定性 `cancelled` + gate 结构化 `cancel_fact`（去中文文案
  子串耦合）；② `_enforce_z_policy` 死代码分支删除 + fabric 采样未覆盖
  全量时按「不可确认即拒绝」保守阻断；③ `_report_progress` 改
  `add_done_callback` 真 fire-and-forget（不阻塞变换线程）。
  未收敛（记录为已知边界，均与 G38 precedent 同款局限）：
  ④ FC 缓存常驻 ≤2 份全解析文档（建议后续加字节上限）；⑤ `recover_all`
  一次性启动窗口对「崩溃→立即重启、job 尚未 stale」场景 no-op（建议后续
  挂 sweep 周期低频重跑）。
- 验证声明复核（reviewer 亲测）：域 60 passed ✓；面板目录 77 tests 全绿 ✓；
  tsc 触及面 0 错（全仓 3 错均在未触碰基线文件）✓；OpenAPI pattern 同步 ✓。

## 合流检查

- PR 前 fetch：master 仍为 655f769ca（base 未漂移，无冲突面）。
