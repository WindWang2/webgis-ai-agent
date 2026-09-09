# 03-progress — 实施记录（真实结果）

全部 wave 已实现并独立 commit（截至 HEAD 的实际 commit 顺序）：

| Wave | Commit | 结果 |
|---|---|---|
| W2-W4 | 9a941f31 协调面核心（ownership/migrations/ADR/preflight） | 30+4 测试绿；preflight 0.5s |
| W2-fix | gate 口径修正 | 磁盘存在即须 tracked（事故模式） |
| W5 | 7a281451 生成物权威（content fingerprint/scope/手改检测） | 41 绿 |
| W6 | manifest + gen CLI | 合成 git 仓正/负例；确定性双跑 |
| W7 | impact（AST import 图 + 完备性护栏） | 冷建 5.4s/暖 0.1s；护栏实景锁 |
| W8 | merge_sim + simulate_merge CLI | 五类冲突检出 + 线性链正例；三段契约 |
| W9 | runner impact/integration profile | impact profile 实测 130.7s 全绿 |
| W10 | W3C trace（ASGI 中间件 http+ws） | 21 项测试；app 挂载锁 |
| W10-fix | reproducible_gis_runtime 夹具密闭性 | 新 worktree 单跑必红 → checkfirst 补缺 |
| W11 | SRE status + sre_metrics + alerts + grafana | B-1 鉴权实测（AUTH_DISABLED=false → 401）；一致性 8/8 |
| W11-产物 | API 快照 additive +25 行 + 全产物再生成 | quality 全绿 459 |
| W12 | migration up/down/up + real lanes + 多进程 harness | SQLite 全链 up→down→up 实测通过；harness 2 workers + kill -9 chaos PASS |
| W13 | chaos V3 四 fault + 注册表再生成 | 7 项行为测试；命名闸对齐 |
| W14 | frontend 行为证据 + @behavior 聚合清单 | 7 用例绿（对齐 reconciler 真实契约 recompile 语义） |
| W15 | release readiness + 政策断言 | not-run 不可合法化；waiver 须 reason |
| W16 | 完成证明 + README + CHANGELOG | §17 五类冲突全 PASS |

## 过程中的关键发现（均已修/披露）

1. **反向闭包漏边**：`from pkg import sub` 只记包名 → 选择器漏测（修：双记边）。
2. **工作树劫持**：conftest 的 load_dotenv 让 env.py 优先用 .env 的
   DATABASE_URL —— migration 生命周期测试曾跑到 dev DB（修：显式钉 env）。
3. **reproducible_gis_runtime 隐式依赖兄弟套件建表**（修：checkfirst 补缺）。
4. **g1109 空壳 downgrade 是蓄意 no-op**（安全论证）→ 闸放行显式声明。
5. **READYNESS 自指回归**：产物内嵌 HEAD 会导致字节闸永不自洽（修：
   字段显式为"生成时 HEAD"）。
6. **B-1 安全验证**：AUTH_DISABLED=false 生产姿态下匿名访问
   /api/v1/status/detailed = 401（harness 实测）。
