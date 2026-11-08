# G06 — PR 交付清单

> M9 已完成。以下均为实时取值，**PR URL 为真实返回值**。

## 交付事实

| 项 | 值 |
|---|---|
| 实时 `origin/master`（M0 与 M9 均刷新） | `585812ee4e2d75612f2b124e6fe6587af3deca5c`（2026-11-07T17:54:14+08:00，PR #2010 合并）；M9 再刷新未变动 |
| 目标 branch | `zcode/goal-gis-capability-ecosystem-20261107` |
| worktree | `/home/kevin/projects/webgis/webgis-wt-gis-capability-ecosystem-20261107` |
| head SHA（推送时） | `781022d65`（R2 修复提交；amend 后） |
| 与 master 关系 | ahead 8 / behind 0（无需 rebase） |
| diff 规模 | 35 files changed, +6277 / −8 |
| **PR URL** | **https://github.com/WindWang2/webgis-ai-agent/pull/2016** |

## commit 列表（origin/master..HEAD）

| # | SHA | 内容 |
|---|---|---|
| 1 | `73bd4d6a8` | docs(g06): M0 实时基线审计与冲突避让账本（SHA、PR/Review/Issue、能力真值、接口契约） |
| 2 | `57765f86a` | feat(g06/cap-01): 跨层可用性只读快照、可信状态词表与权限隔离查询（ADR-0287） |
| 3 | `214f24063` | feat(g06/m5): 两项真实原语（条件多分位数分区统计、线网覆盖一致性）+ 参数契约 + 接线 + 既有生成器产物 |
| 4 | `3c5ba48f7` | feat(g06): 三态预检、可用性重排、provider 只读资格、schema 对拍、Pi adapter、离线黄金语料 |
| 5 | `64c29dab2` | test(g06): 14 项 Oracle（M1 红灯 23 failed → 24 passed） |
| 6 | `cf301de84` | docs(g06): authoring 契约、示范包检查器、2 组离线演示、5 分钟验收脚本、gitignore 白名单 |
| 7 | `391e28efb` | fix(g06/review-r1): 冷审 P1 清零（路径闸/SSRF、时间量纲不伪称、读取前判界、慢 provider 有效验证）+ 6 项 P2 |
| 8 | `781022d65` | fix(g06/review-r2): 账本失真清零（R-01）+ 多段时间分摊、预算闸异常不吞、断言精确化 |

## 验收证据（全部本地）

| 检查 | 命令 | 结果 |
|---|---|---|
| 14 项 Oracle（28 用例） | `pytest tests/unit/gis/test_capability_ecosystem_oracles.py tests/unit/tools/test_capability_ecosystem_primitives.py --no-cov -q` | **28 passed in 26.19s** |
| 回归（gis + tools） | `pytest tests/unit/gis/ tests/unit/tools/ --no-cov -q` | **859 passed, 3 skipped in 108.28s** |
| gis 单套（独立复跑） | `pytest tests/unit/gis/ --no-cov -q` | **798 passed, 3 skipped in 96.67s** |
| catalog/元数据/Pi 面 | 5 个定向测试文件 | **78 passed, 1 skipped** |
| 一键 5 分钟验收 | `python scripts/verify_capability_ecosystem.py` | **6/6 passed** |
| Ruff | changed py files | **All checks passed!** |
| Mypy | 6 个新模块 | **Success: no issues found** |
| 生成物漂移门 | `python -m app.lib.gis.execution_catalog_docs --check` | **up to date** |
| 真实可用性分层 | `compile_availability_snapshot(bootstrap=True)` | **213 native_verified / 186 available_unverified / 18 declared_only（417）** |
| 算法计数兼容 | `get_algorithm_registry().count` | **253（未变，既有硬断言保持）** |
| 工具总数 | `ToolRegistry().tool_names()` | **399（基线 397 + 2 原语）** |

## 文件清单（35）

**新增源码（7）**
`app/lib/gis/capability_availability.py`、`method_eligibility.py`、`availability_rerank.py`、
`provider_eligibility.py`、`capability_schema_parity.py`、`app/lib/gis/algorithms/capability_ecosystem.py`、
`app/services/chat/pi_availability_adapter.py`

**新增工具与数据（2）**
`app/tools/capability_ecosystem_tools.py`、`app/lib/data/capability_lexicon_golden.json`（置于 `.gitignore` 白名单目录）

**接线改动（2，各 1–2 行）**
`app/tools/__init__.py`、`app/lib/gis/algorithms/__init__.py`

**测试（2）**
`tests/unit/gis/test_capability_ecosystem_oracles.py`、`tests/unit/tools/test_capability_ecosystem_primitives.py`

**文档 / ADR（4）**
`docs/dev/GIS_CAPABILITY_ECOSYSTEM.md`、`docs/dev/GIS_TOOL_AUTHORING_CONTRACT.md`、
`docs/adr/0287-capability-availability-projection-and-trust-vocabulary.md`、`.gitignore`（1 行白名单）

**示例 / 脚本（4）**
`examples/gis_capability_ecosystem/{capability_pack_manifest.json,check_pack.py,demo_school_poi.py,demo_nodata_science.py}`、
`scripts/verify_capability_ecosystem.py`

**生成物（既有生成器重生成）**
`docs/catalog/execution-catalog/*`、`docs/science/ALGORITHM_CATALOG.md`

**账本（8）**
`planning/G06-CAPABILITY-ECOSYSTEM/{BASELINE,PLAN,DECISIONS,LOOP_LEDGER,TEST_MATRIX,REVIEW,PR_HANDOFF,INTERFACE_CONTRACTS,RECON}.md`

## 提交前门禁（逐条确认）

- [x] `git diff --check` clean（已修 REVIEW.md EOF 空行）
- [x] `git status --short` 洁净，无敏感凭据 / 缓存 / 巨型中间产物
- [x] 实时 master / PR / Reviews / Issues 已复核；8 个 open PR 全是 dependabot；G01/G02/G03 并行分支文件面无冲突
- [x] 无需 rebase（behind 0），未改写他人提交
- [x] 独立冷审 R1 4 条 P1 → 修 → R2 P0=0/P1=1（纯文档）→ 修 → 本轮清零
- [x] 本地验收全部完成；**不等待线上 CI，不宣称 CI 通过**
- [x] `gh pr create` 返回真实 URL：https://github.com/WindWang2/webgis-ai-agent/pull/2016

## 尚存风险 / 未验证

1. `native_verified` 仅表示本地可执行 + 8 面对账 pass，不代表科学结论正确。
2. 未接真实 PostGIS / 在线数据源；PostGIS 专项为 `pytest -m real_services` opt-in，本 PR 未跑。
3. `probe` 是协作式 deadline，非线程级硬超时；生产默认不发慢 I/O。
4. 扩展 pin/lease 未引入（F-10 后续）。
5. `docs/quality/*` stale 为基线历史债务（Issue #2007 同类），未 `--update` 掩盖。
6. R2 遗留 P2：Jaccard 矩阵缺独立方案数上界、rerank 输入截断无诊断码、CRS 子串匹配误报风险、`./tmp` 测试文件不清理、primitives 侧少数宽松断言。
