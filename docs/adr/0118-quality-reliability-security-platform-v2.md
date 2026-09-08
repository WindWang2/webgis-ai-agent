# ADR 0118 — Quality, Reliability & Security Platform V2

日期：2026-09-09
状态：Proposed（随 feat/quality-v2-reliability-security 分支交付）
前置：ADR 0104（Quality & Reliability Platform V1）、ADR 0103（描述符
覆盖率闸）、ADR 0100（session 身份）、审计 S41/#1109/#485（安全矩阵）

## 背景

Quality V1 把质量体系建成了 derivation-first 的投影 + 字节闸（20 个
wave）。Phase 0 审计（`.agent-work/quality-v2/00-baseline.md`）确认的
残余缺口：

- findings（241 条）只是**滚动报告**：无 gate、无 owner 定位、无 waiver
  机制——债务不收敛；
- "静态测试引用"是 token 匹配：**测试文件提到工具名 ≠ 行为覆盖**，30 个
  工具零任何证据、20 个算法零 conformance；
- SEC-KG-01/02 显式 KNOWN-GAP（artifact ownership 隐形调用顺序；templates
  /knowledge delete authZ 零回归）；
- OpenAPI 快照自认不覆盖 WS/SSE（契约盲区）；
- SQLite/Postgres 双存储只有驱动级分支，无差异契约（006be00c 实录 SQLite
  无 FK 误绿）；
- 零 property/fuzz 设施；无顺序轮换；~26 处固定墙钟断言对负载敏感
  （f2124e68/d2232d5f 事后人工校准）；
- 生成物再生成 = 合并后手工纪律，无前置 staleness 检查。

## 决策

### D1 — findings 从报告升级为**棘轮债务**（W1）

- QualityManifest v2（manifest_version=2）：finding 增加 `surface`/
  `domain`（可路由到 owner）；工具行增加 `behavior`
  （dispatch/mention/none）——AST 级**真实 dispatch 发现**
  （`app/lib/quality/behavioral.py`），只可能漏报不可能误报；
- `docs/quality/findings-baseline.json`：每 code 数量上限，只降不升；
  新 code 未登记视作上限 0（防静默新增债务类别）；
- `docs/quality/waivers.json`：豁免必须 (code, subject) 精确匹配且带
  ISO 过期日，过期即闸红；
- `min_behavioral_dispatch`：真实 dispatch 覆盖工具数下限，只升不降。

### D2 — findings 修复走真实语义，不 suppress（W2/W3/W4）

- TOOL_DESCRIPTOR_INCOMPLETE 152→0：60 个工具补齐
  side_effect/tags/capabilities（逐工具语义判断）；92 个词表外工具通过
  **扩展 capability 分类**（platform 域 planned-status 包）收口——
  planned 诚实表达"工具面真实、算法级 producer/conformance 契约未建"；
- TOOL_UNTESTED 30→0：真实 dispatch 行为测试（validation/happy/error
  三段式）；期间发现并修复 quadrat_analysis 生产 bug
  （GeoAnalysisResult 按 dict 取值 → TypeError）；
- ALGO_NO_CONFORMANCE 20→0 / ALGO_HEAVY_NO_VARIANTS 23→0 /
  CAPABILITY_NO_CONFORMANCE 16→0：真实 conformance oracle 测试 +
  AST 校验的节点声明 + 诚实的规模窗口；参数 parity 闸揭示
  analyze_vegetation_index 与 spectral_index 契约不符 → 撤回派生链接
  而非放宽闸。

### D3 — 安全收口：把隐形顺序变成受检契约（W5）

- SEC-KG-01：AST 契约扫描禁止 `app/api/routes/**` 直调
  artifact_registry + 公共 API session 首参签名钉扎 + 跨会话行为回归；
  owner 语义仍为 session 级（诚实披露残余边界）；
- SEC-KG-02：templates/knowledge delete 的 #1109 同款 owner 矩阵
  （owner 放行 / 他人拒绝且行原样 / admin 显式白名单 / 匿名 authN 拒绝
  / 内置只读 / 缺失 404 / NULL creator fail-closed / org 不放宽
  creator-only / 无身份不触存储）；实现本带校验，V2 补的是回归保护。

### D4 — WS/SSE 契约纳入 compatibility（W6）

`snapshot_realtime_contract()`：WS 入站事件词表 + 必需字段（AST 守卫
派生）+ 出站信封/词表 + auth 语义锚；SSE 词表 + wire 格式 + resume 锚。
快照 `tests/quality/snapshots/realtime-contract.json` + 分类器
（词表收缩/必需字段扩张/语义锚丢失 = breaking；新增 = additive）。
刷新 = `REALTIME_SNAPSHOT_UPDATE=1`。

### D5 — 双存储差异显式契约（W8）

`tests/quality/test_storage_differential.py`：SQLite 恒跑（含 graceful
skip 的 PG 侧，`TEST_POSTGRES_URL` 开启）。钉住：SQLite 默认接受孤儿行
（006be00c 教训的显式契约）、NULL 排序差异（SQLite first / PG last）、
布尔严格性（ORM bind 层拦截，双后端一致）、唯一约束/JSON 往返/事务回滚
一致要求、alembic 单 head。

### D6 — 生成式 property/fuzz，零新依赖（W7）

`tests/fixtures/generative.py`（seeded、有界、可重放；不用 hypothesis）：
parse_bbox 异常类型边界、safe_parse 永不 raise + 幂等、MapSpec 良性
intent 序列不变量（revision 单调/可序列化）+ 畸形 intent 类型化拒绝、
make_cache_key 确定性/ref 禁缓存/owner 域隔离。fuzz crasher 治理走
`tests/fixtures/fuzz_corpus/` 语料闸（14 条边界样本逐条回归）。

### D7 — chaos STORAGE 类 + cancellation 抬升（W9）

- STORAGE_TRANSIENT_FAIL 注入 store/overwrite：钉住 register_artifact
  「异常→返回 None 显式拒绝、账本不半截提交、恢复后重试成功」；
- cancellation checkpoint 落进重计算外循环（terrain viewshed 扇区分块/
  flow_accumulation、density KDE 分块、rs_v3 逐分量物化）——数值路径
  不变。

### D8 — 顺序卫生 + runner V2 + 性能门稳定化（W10/W11）

- `QUALITY_ORDER_SEED` 确定性 shuffle（默认关；runner changed/full-local
  每轮轮换 seed 暴露顺序污染；同 seed 可复现）；
- runner profiles：quick / changed（git diff 驱动 + 红线恒跑）/
  full-local；flake 统计（--retry-failed 时 --lf 重跑 recovered 计数）；
- `tests/fixtures/perf_budget.py`：median-of-N + floor/factor 语义；
  迁移 6 处最脆固定墙钟断言（cached retrieval 加结构比值
  `cached < first/4`——缓存退化必红，负载同胀不误红）。

### D9 — 生成物依赖图 + 前置 staleness 检查（W12）

`app/lib/quality/artifact_graph.py` 声明 source→generated 输入指纹；
`scripts/check_generated_staleness.py [--update]` 在合并前给出 stale
清单（替代合并后手工"产物再生成"纪律）；账本
`docs/quality/generated-artifacts.json` 由闸测试保护。

## 后果

- findings 241 → 0（真实修复；词表扩展以 planned-status 诚实登记），
  棘轮锁零后新债务直接红；
- 行为化证据成为一等公民：dispatch 覆盖 120→150+，且只升不降；
- WS/SSE、双存储、顺序、性能门、生成物 staleness 全部进入
  tests/quality 红线集（quick lane 一键可验，~1 分钟）；
- 成本：新增 2 个账本文件（findings-baseline/waivers）+ 1 个生成物账本
  + 1 个 realtime 快照；全部有确定性闸保护，无时间戳。

## 已知边界（诚实披露）

- behavioral discovery 是静态 AST 下界：变量中转的 dispatch 调用漏报
  （诚实方向）；
- artifact ownership 仍是 session 级而非 user 级（用户级归属由会话守卫
  token_version 承担）；
- WS/SSE 快照锁词表与信封形状，字段级类型由对抗测试承担；
- rs_v3 的 sklearn 内部迭代不可协作取消（检查点在分量物化边界）；
- PG 侧差异用例默认 skip，需 `TEST_POSTGRES_URL` 显式开启。
