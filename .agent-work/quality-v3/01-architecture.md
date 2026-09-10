# 01-architecture — Quality/Integration Platform V3（Epic 10）

## 定位

**并发研发 integration authority + 生产 SRE 扩展**。不篡夺业务域事实源：
registries（tools/algorithms/capabilities）、`app/lib/quality/manifest.py`、
`artifact_graph.DECLARED` 仍是唯一事实源；V3 全部是**元契约层**（谁负责什么、
冲突如何在合并前暴露、发布证据如何诚实聚合）。

## 包结构（全部新增，additive）

```
app/lib/integration/            # 开发者侧协调面（纯静态分析 + git 只读，零生产 import 成本）
  ownership.py                  # A: shared-file ownership 规则模型 + 校验
  migrations_coord.py           # B: migration 图扫描 / multi-head / revision 分配 / 分支碰撞
  adr.py                        # C: ADR 扫描 / next 分配 / 撞号 / 引用存在性
  artifact_authority.py         # D: 生成物 generator-version + 分支作用域 + rebase 再生验证（复用 artifact_graph）
  manifest.py                   # E: semantic integration manifest（分支 → touched surfaces）
  impact.py                     # F: import-graph 影响分析 + 测试选择（保守 + 防漏报）
  merge_sim.py                  # G: 跨分支语义合并模拟（manifest 对比，无真实 merge）
  release.py                    # M: release readiness 聚合（诚实披露 lane 状态）
scripts/
  gen_integration_manifest.py   # E CLI：--branch → .agent-work/integration/manifests/
  simulate_merge.py             # G CLI：--branches a,b → 语义冲突报告
  gen_release_readiness.py      # M CLI：聚合闸 + lane 证据 → docs/integration/
  allocate_migration.py         # B CLI：安全分配下一个 migration revision
  allocate_adr.py               # C CLI：安全分配下一个 ADR 编号
app/lib/observability/trace_context.py  # H: W3C traceparent 解析/生成（纯 stdlib 接缝）
app/api/routes/health.py        # I: 扩展 /health/detailed（有界组件分类学）
app/core/sre_metrics.py         # I: 有界标签 SRE 指标（对齐 auth_metrics 模式）
tests/integration/              # 全部新测试
docs/integration/
  ownership.json                # A: 机器可读 ownership 规则（手工权威，非生成物）
  RELEASE_READINESS.md/.json    # M 生成物（字节闸）
```

## 关键设计决策

### A. Ownership（元契约，非第二 registry）
`docs/integration/ownership.json`：`[{pattern, owner, policy}]`。policy 词表：
`additive-only`（registries）、`append-only`（CHANGELOG）、`allocator`（migrations/ADR——
必须经 allocate CLI）、`regenerate-dont-edit`（生成物，链接到 artifact_graph 的
generator 声明）、`single-writer`（快照类）。校验 = 结构测试（pattern 可编译、
owner 属于已知 domain 词表、生成物条目与 artifact_graph.DECLARED 无矛盾）。
**权威性**：本文件是手工维护的规则本体；与 artifact_graph 的关系是"投影一致性"
（生成物条目必须双向对齐，parity 测试锁）。

### B. Migration 协调
- 图扫描：解析 `revision:` / `down_revision:`（fingerprint 缓存，进程内 dict 即可——
  CLI 短生命周期，无跨进程缓存需求）。
- multi-head 检测：children 集合差 → heads；>1 即红。
- revision 分配：约定 `NNNN_slug`；`allocate_migration.py` 扫 max(NNNN)+1，
  显式打印将创建的文件名，写 `down_revision=<当前唯一 head>`；head 不唯一时拒绝。
- 分支碰撞：给定两个分支的 changed migration 文件集（git diff vs 各自 merge-base），
  报告：同 revision id / 同 NNNN 序号 / 双方都改同一 down_revision 链（将产生 fork）/
  同表 DDL（表名从 upgrade() 内 op.create_table/add_column 提取，尽力而为、
  未能提取时降级为 `unknown` 不谎报）。

### C. ADR 协调
- 扫描 `docs/adr/`：文件名编号 + 文内 `# ADR NNNN` 标题一致性。
- `allocate_adr.py`：max(NNNN)+1；若该编号已被占用（并发窗口）→ 明确报错并列出
  占用者。审计发现 master 已有 6×0118 撞号（历史现状，**不在本 Epic 重写历史编号**
  ——会破坏全部引用；只对新分配生效，并将存量撞号记录为 known limitation）。
- 引用校验：文内 `docs/...` 相对链接存在性 + supersedes/related 指向的 ADR 文件存在。

### D. 生成物权威（V2 扩展）
- `artifact_authority.py`：在 `artifact_graph.DECLARED` 之上 additive 提供：
  - `content_fingerprint(artifact)`：生成物本体 sha256（重生成 diff 归因：输入指纹变 vs 内容变 vs 生成器变）；
  - `GENERATOR_VERSION`：declared 侧版本号，生成器语义变更时 bump（测试锁：manifest 消费方兼容）；
  - `scope`：`global`（全分支共享，merge 后必须再生成）vs `branch-local`（仅本分支产物）。
- 存量 `docs/quality/generated-artifacts.json` schema **不变**（只加可选字段，旧消费方忽略未知键）。

### E. Semantic Integration Manifest
输入 = `git diff --name-only origin/master...<branch>`（+工作区），输出 JSON：
```
{branch, base, generated_at_from_git_commit(无时钟), domains[], tools[], algorithms[],
 capabilities[], api_routes[], migrations[], frontend_contracts[], events[], shared_files[],
 risk: {score, reasons[]}, required_suites[]}
```
- domain 归属：ownership.json pattern 匹配（第一命中）。
- risk 规则（纯函数、可测试）：碰 migration/ownership`single-writer`/registry 非 additive → high；
  碰共享生成物输入 → medium；否则 low。
- required_suites：domain→lane 映射表（静态、在 ownership.json 中声明 `suites` 字段）。
- 输出目录 `.agent-work/integration/manifests/`（gitignored，分支工作产物，不入库）。

### F. 影响分析 + 测试选择
- import graph：AST 扫 `app/**/*.py` 的 `import app.*` 边 + `tests/**/*.py` → app 模块边；
  fingerprint（源内容哈希集）缓存于 `.agent-work/integration/cache/`（TTL=指纹失配即失效，
  无时间 TTL——纯内容寻址）。
- `select_tests(changed_files)`：changed app 模块 → 反向闭包 → 引用它们的测试文件；
  **防漏报护栏**（false-negative guard）：(a) 结果恒并入 V2 目录映射选择器结果与
  tests/quality 红线；(b) 抽样一致性测试锁：随机构造改动集，selector 结果 ⊇ 目录映射结果；
  (c) 无 import 边的 changed app 文件 → 保守并入对应 tests 目录。
- 定位：高效集成复测，**不替代** domain full tests（文档+CLI help 显式声明）。

### G. 合并模拟
`simulate_merge.py --branches a,b[,c]`：各分支生成 manifest → 对比：
- migration：revision/NNNN/down_revision 冲突；
- registry ID：从各分支 diff 中解析新增 tool/algo/capability ID（调用 register/dispatch
  声明的 AST 提取，尽力而为 + `unknown` 降级）；同 ID 即冲突；
- 生成物：双方都改同一 `regenerate-dont-edit` 产物的输入 → 双写冲突提示（再生成一次即可，风险=medium）；
- API 语义：双方改同一 route 文件 → 冲突面提示（语义 breaking 判定复用 V2 api_compat 分类）；
- shared files：双方都碰 `single-writer`/`append-only` 文件 → 提示。
输出 JSON+MD 到 `.agent-work/integration/merge-sim/`。**完成证明**将构造两个模拟分支
演示全部冲突类别被捕获。

### H. 观测关联
现状：RuntimeContext（request/session/turn/run/project）+ EVENT_CATALOG 已覆盖
chat turn/workflow/geocompute 主干。V3 增量：
- `app/lib/observability/trace_context.py`：W3C `traceparent` 解析/生成（stdlib 纯函数，
  有严格格式校验；非法输入显式拒绝并回退新生成——不静默吞）。HTTP 中间件在
  `app/main.py` 挂一个有界中间件：入站解析 traceparent → 注入 RuntimeContext 衍生字段；
  出站（httpx 客户端封装处）附带生成头。**不引入 OTel SDK**（依赖纪律 + vendor-neutral
  Sink 接缝已在 events.py 预留）；跨进程相关性以 traceparent 头 + 统一关联字段达成，
  OTel SDK 接入留为 follow-up（诚实披露）。

### I. SRE 健康分类学 + 有界指标
- `app/api/routes/health.py` 增 `GET /api/v1/health/detailed`：
  `{status, components: {db, redis, object_store, llm, worker, queue, export}}`，
  组件词表封闭；每组件 `{status: ok|degraded|down, latency_ms?, detail?}`；
  整体缓存 TTL 10s（与 _llm 同模式）；任何组件 down → 整体 503 语义保持与现有
  /health 一致。worker/queue 检查从 jobs store 读有界聚合（stuck job 数 = progress
  长时间无推进的有界计数），绝不枚举全量 job。
- `app/core/sre_metrics.py`（对齐 auth_metrics.py 模式）：`sre_stuck_jobs`（Gauge）、
  `sre_health_component_status`（Gauge, label=component, 封闭词表）、
  `sre_export_duration_seconds`（Histogram, 无高基数 label）。**同步更新
  deploy/alerts-rules.json 并保持 test_alerts_metrics_consistency.py 绿**。
- deploy/grafana：新增一个 dashboard JSON（静态、有界面板）。

### J. Real-services lane（opt-in）
- runner 增 `real` profile：`REAL_SERVICES=1` + docker compose 服务可达时运行
  `-m real_services`（既有 marker）+ 新增：
  - `tests/integration/test_real_postgres_lane.py`（TEST_POSTGRES_URL，含 PostGIS 可用性探测，缺省 skip）
  - `tests/integration/test_real_redis_lane.py`（REDIS_URL transient 故障恢复——chaos V3 复用点）
  - 多进程 harness：`scripts/integration_harness.py --workers 2`（uvicorn 多进程 +
    traceparent 贯穿 + /health/detailed 轮询，有界请求数后退出）。
- 默认 quick lane **不**启动任何服务（资源纪律）。

### K. Frontend 行为证据
- 新 `frontend/tests/behavioral/`：map layer lifecycle（add/update/remove/reconcile）、
  MapSpec reconcile、export 触发的组件级行为测试（vitest，无浏览器依赖）。
- `docs/integration/frontend-behavior.json`：行为→测试映射清单（生成物，gen 脚本扫描
  describe/it 标记 `@behavior:map-layer-add` 等标签），字节闸锁。
- Playwright 浏览器级 E2E 列为 known limitation（nightly lane 已有 REQUIRE_BROWSER
  机制，本 Epic 不新增浏览器基础设施）。

### L. Chaos V3（全部测试侧，生产零改动）
新 FAULTS（沿用既有纪律：只打既有接缝）：`REDIS_TRANSIENT`（fakeredis 断连序列——若
V2 已有等价则登记 alias）、`DB_TRANSIENT_SEQUENCE`、`DUPLICATE_EVENT_DELIVERY`、
`WS_RECONNECT`、`STALE_REVISION_CAS`、`WORKER_LOSS`、`CANCEL_STORM`。
每个 fault：journal 证据 + 恢复断言；`gen_chaos_registry.py` 再生成注册表 MD（字节闸同步）。

### M. Release readiness
`gen_release_readiness.py` 聚合（全部 --check 化、字节闸）：
```
{generated_at: <git commit>, lanes: {quick: run|pass|fail|not-run, ...real: not-run(原因)},
 contract_drift: {...}, migrations: {heads: [...], ok}, generated_artifacts: {stale: []},
 security: {...}, findings_ratchet: {...}, perf: {baselines_fresh: bool},
 known_gaps: [...], rollback: {...notes}, compatibility: {...}}
```
诚实规则：lane 未跑 = `not-run` 显式状态，不伪造 pass；`real` lane 默认 not-run（原因：
资源纪律），跑过则记录 runner 报告路径。

## 不变量与闸
1. 生产零开关：`app/lib/integration/` 不被 `app/main.py` 运行时 import（结构测试锁：
   integration 包只允许被 scripts/tests 引用；唯一例外 H 的中间件与 I 的 health/metrics——
   这两处是小面生产代码，放 `app/lib/observability/`、`app/core/`、`app/api/`）。
2. 资源有界：AST 扫描 fingerprint 缓存；目录展开硬上限（沿用 _MAX_DIR_FILES 模式）；
   manifest/merge-sim 输出写 gitignored .agent-work。
3. 兼容：`generated-artifacts.json` 旧键不变；/health 现语义不变（新增 detailed 为加法）；
   EVENT_CATALOG 不动（无生产 emitter 变更则不动词表）。
4. 测试 oracle：冲突检测全部有正/负/边界三例；两个模拟分支端到端演示（完成证明）。

## 性能预算
- quick preflight（ownership 校验 + ADR/migration head 扫描 + staleness）< 10s；
- import graph 首建 < 60s，命中缓存 < 2s；
- merge sim（2 分支）< 30s（不含 git）。

## 回滚
纯 additive；回滚 = 删分支。生产面仅 health/detailed + 3 个指标 + traceparent 中间件，
均可独立 revert。

---

## 修订（Subagent-A 架构挑战后，verdict: PASS-WITH-CHANGES）

### R1（B-1 安全回退）
`/health/detailed` 取消。改为 `GET /api/v1/status/detailed`：
- **路径不在 `RATE_LIMIT_EXEMPT_PREFIXES`**（app/main.py:413-417 只豁免 `/api/v1/health`），继承全局限流；
- **挂既有鉴权依赖**（与内部管理面一致），未鉴权 401；
- 响应含组件 status/latency/detail（拓扑细节只在鉴权面暴露）；
- 现有 `/health`、`/health/live`、`/ready` 语义零改动；k8s probe 不改指向。
- down 判定 → 整体 503 仅存在于鉴权面；`/ready` 语义不动（M-3 修正：`/health` 恒 200，503 语义属于 `/ready`）。

### R2（C-1/C-2 强制点 + watermark）
- `scripts/check_integration_preflight.py`：ADR watermark 扫描 + migration multi-head + ownership parity + 生成物 staleness，一条命令全跑；**挂进 `LANES["quick"]`**（quality_runner.py quick lane 已在 CI release DAG 覆盖面内）。
- ADR **watermark**：`docs/integration/ownership.json` 增 `adr_watermark: 118`（存量 max）；编号 > watermark 的重复 → 红；≤ watermark 的存量重复 → 报告列 known limitation 不红。分配器输出的编号即新 watermark（分配后必须提交推进）。
- migration 高水位同理：`migration_watermark: 33`；allocate 输出打印 base SHA 与 rebase 提醒；allocator 降频不消除，与 quick lane 头扫描组合。

### R3（C-3 WS 覆盖）
traceparent 用**纯 ASGI 中间件**（universal scope：http + websocket），从 `scope["headers"]` 解析；WS accept 前即生效。响应头回 `X-Trace-ID`；`expose_headers` 追加 `X-Trace-ID`（m-6）。出站 httpx 三处独立客户端不逐点接（follow-up）。

### R4（M-1 选择器完备性）
`impact.select_tests` 改完备性断言：任何 changed app 文件必须产出 ≥1 测试目标，产不出 → 显式 `UNCOVERED` 清单且 CLI 退出非零（`--allow-uncovered` 豁免需打印理由）；自建完整目录映射（含 V2 缺口：app/lib/runtime、observability、harness、data、extensions_platform 等）；有界上限改为显式警告 + 可配置。V2 `_changed_py_targets` 保持不动（向后兼容），V3 selector 作为新 profile。

### R5（M-2 chaos 重靶）
- `WS_RECONNECT` → 重靶为 `STALE_EVENT_RESUME`（SSE Last-Event-ID 断线重放幂等，接缝 app/utils/sse.py:23-26）；
- `REDIS_TRANSIENT` 圈定 lock 域（V2 fakeredis 接缝），health 内联 redis 无接缝 → 不谎报；
- 保留 `DB_TRANSIENT_SEQUENCE` / `DUPLICATE_EVENT_DELIVERY` / `STALE_REVISION_CAS` / `WORKER_LOSS`（store.py:805 + worker.py:236 接缝）/ `CANCEL_STORM`；
- API/ coordinator 重启类 → 归入 J 的多进程 harness（真实进程 kill/重启，opt-in lane），不伪装成进程内 fault；registry 如实披露。

### R6（M-6 gitignore + 脚本入库）
- `.gitignore` 追加 `.agent-work/integration/`（quality-v3 审计文档按既有惯例**入库**，不整目录忽略——会破坏其他 epic 已提交审计文档的惯例）；
- 新脚本逐一加 `!/scripts/<name>.py` 白名单；
- 结构测试：所有 preflight 引用的脚本必须 `git ls-files` 可见（check_tool_descriptor_coverage 漏入库事故变闸）。

### R7（M-7 诚实 readiness）
- `gen_release_readiness.py` 生成时**现场执行**廉价闸（drift/staleness/preflight）→ pass 即真实证据；
- 重车道（backend/frontend/real）仅在提供有效 runner 报告路径（校验存在+解析+sha256）时记 pass，否则 `not-run`；
- 发布政策断言：quick/backend 非 pass 且无显式 known_gaps 豁免条目 → 退出非零（字节闸之外的独立断言，不可绕过）。

### R8（其余 MAJOR/MINOR 收口）
- m-1：组件健康缓存 = 单不可变快照对象 + Lock 原子换入（不照抄 _llm 裸全局）；
- m-2：content_fingerprint/scope/generator_version 直接扩展 `artifact_graph.build_graph_state()`（additive 可选键，--update 全量重写不丢）；`find_hand_edits()` 检测 regenerate-dont-edit 产物被手改；
- m-3：ownership.json 仅 `pattern/owner/policy/suites` 五字段，禁复制 generator/inputs；parity 双向；pattern 两两不相交校验（NIT）；
- m-4：migration 解析用 alembic ScriptDirectory（requirements 已有 alembic≥1.13），文件名≠revision id（0024 反例）；
- m-5/m-8、NIT：allocator 打印 base SHA；ADR 解析派生自实际语料（`# ADR NNNN` 标题、`ADR NNNN`/`ADR-NNNN` 引用、前置/关联/supersedes 标签），解析不到 → unknown 不谎报；owner 词表补 infra/docs 域；manifest events 字段不动 EVENT_CATALOG（只读词表映射）。
