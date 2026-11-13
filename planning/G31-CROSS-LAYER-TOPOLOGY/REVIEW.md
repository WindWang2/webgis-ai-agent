# G31-CROSS-LAYER-TOPOLOGY — REVIEW（独立评审记录）

独立 reviewer：未参与任何核心实现的 subagent（三角色槽位中的 reviewer 槽）。
方法：代码直读 + 仓库测试复跑 + 自构造对抗探针（TestClient / registry.dispatch /
进程内服务调用）。被评审方只在修复轮提供 diff 与验证请求，不提供"预期通过"提示。

## Round 1（HEAD fe2291941）

**结论：无 P0；P1×2 / P2×6 / P3 若干 → REQUEST-CHANGES**

核心判定（reviewer 原文要点）：
- 接线/科学语义/诚实契约/finding 身份/修复桥/兼容性全部核验通过（"the core is
  trustworthy and unusually well-disciplined"）；跨用户核心路径、五层 Pi 链、
  oracle 测试质量确认。
- P1-1 越权会话 stamp：用户 B 以 A 的会话 id 创建规则集 → B-owned 规则集落入 A
  的所有权域（A 可读写、改版本）。确认可复现利用。
- P1-2 `boundary_touch` 静默无效：词表/catalog 宣称"共边/共点是否违规"，四种
  参数组合均 0 finding —— 声明即无效的诚实性违约；原测试"papered over"。
- P2×6：工具路径 stale 静默 False；repair/preview 未验证会话；openapi 快照未刷；
  findings_list/repair_candidates 锚定未接线；工具回执内联全量 witness 几何；
  session_ref 解析重活在事件循环。
- P3×8（记录）：severity 字典序、复用键声明漂移、stale fail-open、早退 counts、
  反经线不可达分支、例外撤销归属、witness 8MB 硬失败、fixtures 陈旧注释。

## Round 2（HEAD 77485cb77，修复①②轮）

**结论：10 项中 8 项确认修复（含探针复证）；1 项声称与事实不符；1 项重做引入
新回归 → REQUEST-CHANGES**

- P1-1/P1-2/P2-1/2/3/5/6 + P3 批：全部 CONFIRMED FIXED（含 reviewer 自建
  6000 对碎屑探针、async stale 探针、跨租户 stamp 探针、field_compute 归属
  独立验证）。
- P2-4：`build_repair_candidates` 已接线，但 `topology_findings_list` 仍丢弃
  ctx_session —— 复审探针抓到"修复声明与代码不符"（作者侧根因：一次 no-op
  字符串替换被误当成功）。
- NEW-1（P1-2 重做引入）：`room_for_inding` 移到阈值判定之前 —— 低于阈值的
  碎屑消耗 finding 预算 → 清洁数据被虚假打 partial。与兄弟求值器顺序不一致。
- computation_crs 入键：只改了签名未接调用点（复用检查在 CRS 选择之前）——
  "声明了但接不进"。

## Round 3（HEAD d412e84b9，终审）

**结论：APPROVE**

- NEW-1 CONFIRMED FIXED：阈值先行恢复（与兄弟分支同序）；reviewer 自建
  6000 对探针 → `findings=0 / computation=full / no_violations /
  candidate_pairs=6000`（非空跑）；触顶路径单独复证（cap=5 → 5 findings +
  partial + 截断注记）——"修掉了假 partial 而没有削弱预算"。
- P2-4 CONFIRMED FIXED（这次是真的）：dispatch 探针全链成功；**负例对照**——
  切换到另一会话上下文重新列表 → NOT_OWNED（锚定绑定 turn 会话，不是通行证）。
- digest 口径：撤参移声明 → "无 declared-but-unwired 漂移残留"（剩一句陈旧
  注释，non-blocking，作者已在收尾提交修正）。
- 测试证据：topology 49 + 兼容批 31 全绿。
- 遗留（非阻断，记录在案）：两个 pre-existing 仓内失败（data_import auth
  allowlist / field_compute 短名快照，base 同败）；staleness 对不可解析 ref
  fail-open；witness >8MB 整跑失败（诚实但严苛）；`_verify_session_access` 在
  主循环路由用 thread_async_session（data_quality 同先例，性能性注记）。

## 过程中由评审促成的诚实性改进（记录）

- 作者侧教训：一次字符串替换的 no-op 被误报为"已修复"——复审探针机制抓住了它。
  修复后补了通过 dispatch 面的会话-only 全链回归，防止同类回归。
