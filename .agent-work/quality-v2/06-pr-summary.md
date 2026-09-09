# 06-pr-summary — Quality, Reliability & Security Platform V2

## Problem / Motivation

Quality Platform V1 把质量体系建成 derivation-first 投影 + 字节闸后，残余
缺口是"债务不收敛"：241 条 findings 只是滚动报告（无 gate、无 owner、无
waiver）；"测试提到工具名"被当成覆盖证据；SEC-KG-01/02 安全缺口零回归；
WS/SSE 是契约盲区；SQLite/PG 无差异契约；无 property/fuzz/顺序卫生；~26
处固定墙钟断言对负载敏感；生成物再生成靠合并后手工纪律。本 Epic 把质量
体系升级为 **behavioral / differential / generative / chaos / security-aware
的 V2**：findings 从报告变成只降不升的棘轮债务，并且全部真实修复归零。

## Current-state audit

`.agent-work/quality-v2/00-baseline.md`（241 findings 明细、4 xfail、
SEC-KG-01/02、存储/性能/chaos/生成物基线，全部来自 origin/master 实测）。

## Architecture

`docs/adr/0118-quality-reliability-security-platform-v2.md`（D1–D9 决策 +
已知边界）；`01-architecture.md` 组件图与边界。

## Implementation waves（12）

| wave | commit | 内容 |
|---|---|---|
| W1 | `89d5bb7d` | QualityManifest v2：findings surface/domain、behavioral AST dispatch 发现、findings 棘轮 + waiver 过期闸 |
| W2 | `4a009b13` | descriptor 富化 152→92（逐工具语义判断） |
| W2b | `f576798d` | capability 词表扩展（16 planned 平台能力）→ 93 工具显式声明 → 0 |
| W3 | `c798a3a1` | 30 个 TOOL_UNTESTED 真实 dispatch 行为测试（validation/happy/error 三段式） |
| W4 | `77312b92` | 20 算法 conformance oracle（AST 校验节点）+ 23 honest backend_variants |
| W5 | `f473ab24` | SEC-KG-01/02 收口（路由直调禁令 + session 签名钉扎 + 11+1 案 owner 矩阵） |
| W6 | `7cb6262e` | WS/SSE realtime 契约快照 + breaking/additive 分类器 |
| W7 | `956af3bb` | 生成式 property/fuzz harness（零新依赖）+ crasher 语料闸 |
| W8 | `a427e718` | SQLite↔PG 差异化存储 harness（FK/NULL 排序/布尔/事务/单 head） |
| W9 | `1a7a67ef` | chaos STORAGE_TRANSIENT_FAIL + cancellation 检查点抬升 |
| W10 | `0d71cf50` | runner V2（changed/full-local profile、flake 统计）+ seeded 顺序轮换 |
| W11 | `04362822` | perf_budget median/floor/factor 助手 + 6 处最脆断言迁移 |
| W12 | `73e62d16` | 生成物依赖图 + 前置 staleness 检查 + ADR-0118 |
| 收敛 | `606e18ce` | 棘轮锁零（findings_max 全 0）、dispatch 下限 151、闸上调 95% |

## Key code paths

- `app/lib/quality/{manifest,behavioral,api_compat,artifact_graph}.py`
- `app/lib/gis/capabilities/platform.py`、`app/lib/gis/algorithms/platform.py`、
  `app/lib/gis/algorithm_registry.py`（非 native 派生过滤）
- `app/tools/*.py`（descriptor 富化 + capability 声明）
- `app/lib/geo_analysis/{terrain,density,rs_v3}.py`（cancellation checkpoint）
- `tests/fixtures/{generative,perf_budget,chaos}.py`、`scripts/{quality_runner,
  check_generated_staleness}.py`

## Data/persistence changes

无 schema/migration 变更（alembic head 不变）；新增 3 个账本/快照文件
（findings-baseline.json、waivers.json、generated-artifacts.json、
realtime-contract.json——前两个是策略账本，后两个是纯投影）。

## API / contract changes

- HTTP/WS/SSE 外部契约**零破坏**（realtime 快照 + 分类器为新增保护面）；
- 新工具能力声明不改变工具签名与行为（quadrat_analysis 修复除外：此前
  任何合法输入都 TypeError，现返回正常结果契约）。

## UI changes

无（diff 无 frontend 文件）。

## Security implications

- SEC-KG-01：路由层直调 artifact_registry 被静态契约禁止（含
  `from X import`/别名/裸名/动态 import 字符串 5 种形态）；公共 API
  session 首参签名钉扎；owner 仍为 session 级（诚实披露）。
- SEC-KG-02：templates/knowledge delete 的 creator-only 语义由 12 案
  矩阵钉扎（含 versioned 认证依赖钉扎、org 不放宽、无身份 fail-closed、
  拒绝路径不触向量清理）。
- capability 词表扩展用 planned status：不触发 producer/conformance 责任，
  resolver 拒绝派发，findings/drift 只查 native（有测试）。

## Performance baseline/results

- gen_quality_manifest --check：5.9s（含全 tests AST 扫描）；staleness 检查
  0.075s；quick lane 总时长 ~40s（与 V1 相当）。
- 行为化 AST 扫描 lru_cache 按 root 键控，一次编译单遍扫描。
- 迁移后的 6 处性能断言：median-of-N（N≤5）+ floor，另加结构比值
  （cached < first/4、并发 < 0.8×串行）——负载同胀不误红，缓存退化必红。

## Local test matrix and exact result summary

（见 `04-test-matrix.md`；要点）
- tests/quality 全量：**332 passed** / 7 skip / 4 xfail（既有 KNOWN-GAP），
  含 `QUALITY_ORDER_SEED=777` 随机序复跑通过；
- tests/unit 全量：**8520+ passed**（后台全量 16m42s）；修复暴露的 2 处
  顺序污染（见下）；
- tests/unit/gis 279、unit/tools+data 686、lib+tools 786 各分组全绿；
- property/fuzz 40、行为工具 30、安全矩阵 14、realtime 11、棘轮 13、
  生成物账本 5、chaos 套件 41、迁移 perf 33 —— 各 targeted 全绿；
- ruff app/ + tests/ 全绿；全量产物 `--check` 一致；无 frontend 改动。

## Review Round 1 findings/fixes

1 MAJOR（SEC-KG-01 AST 旁路：`from X import` 模块名 + 裸名引用 +
动态 import 字符串，5 形态自验全抓）+ 8 MINOR（tool_to_algorithms 对称
过滤、runtime_manifest 视图披露注释、platform 绑定 determinism 一致、
realtime 分类器 3 漏类、auth 锚 AST 化、畸形账本 JSON 显式报错、
behavioral 文档诚实化、typing）+ 6 NIT 全部修复。

## Review Round 2 findings/fixes

交叉确认同一 MAJOR；4 MINOR（knowledge delete versioned 依赖钉扎、
MD waived_current 披露、ADR 引用/数字一致性 ×2）+ 2 NIT 全部修复。
另：全量 unit 套件顺序暴露 2 处测试污染（idw variants 后默认路径证据
断言更新；search_datasets 空会话断言不再假设全局 fabric 注册表为空）
—— 已修复并注明。

## Rebase/integration verification

origin/master 在本 Epic 期间未前移（rebase = no-op，`git fetch` 复核于
PR 前）；分支基于 `445ad30e` 创建，产物一致性在最终树上复验
（`gen --check` + staleness 全绿）。

## Backward compatibility

manifest_version 1→2 为纯扩展（消费方 gen_quality_report/drift 全兼容，
有测试）；TOOL_UNTESTED 语义收紧（新工具零测试直接红）已在 ADR/基线
note/阈值注释三处披露；无 API 破坏。

## Known limitations（保留）

- 4 个科学/制图 xfail KNOWN-GAP 保留（CRS 类型化收口、bowtie 静默统计、
  legend 突变丢弃、SVG 标注截断）——归属 science-v4/cartography 域，
  本 Epic 不跨界修复；
- artifact ownership 仍为 session 级（用户级归属由会话守卫 token_version
  承担）；
- WS/SSE 快照锁词表与信封形状，字段级类型由对抗测试承担；
- behavioral discovery 不校验接收者类型（个别非 registry 的同名 dispatch
  可能计入，棘轮下限取保守值）；
- rs_v3 sklearn 内部迭代不可协作取消（检查点在分量物化边界）；
- PG 差异用例默认 skip（`TEST_POSTGRES_URL` 显式开启）；
- 根级 data_fabric 测试注册全局连接后不清理（本次以测试侧鲁棒化吸收，
  彻底治理=连接注册表会话化，列为 follow-up）。

## Follow-up candidates

- data_fabric 连接注册表的会话/owner 作用域化（消除全局残留）；
- runtime_manifest 反查图与 AlgorithmRegistry 过滤语义统一；
- WS auth 锚全 AST 化（subprotocol 字面量仍是源码检查）；
- capability platform 域从 planned 升级 native（补 producer 算法与
  conformance）；
- frontend 行为证据纳入 behavioral 索引。
