# PARALLEL_OWNERSHIP — 重复施工/热文件/接口依赖矩阵

## 本分支拥有（新增，全部为**新文件**）

- `app/lib/cartography/standards/**`（rule/graph/pack/profile/context/evaluate/qa/catalog/packs/）
- `app/services/cartography/standards_qa.py`（服务包装，纯库调用）
- `tests/cartography/test_standards_*.py` + `tests/cartography/fixtures/standards_cases/**`
- `docs/cartography/standards-catalog.md`（生成产物）+ `docs/adr/0200-*.md`
- `.agent-work/standards-rulegraph-v1/**`（planning + ledger，跟随 PR #1353 先例，不动根 ledger）

## 竞争文件（**禁止修改**，只读复用）

| 文件 | 竞争者 | 本分支策略 |
|---|---|---|
| `app/lib/cartography/mapspec_schema.py` | #1356（v1.4 scene） | 不改；依赖 extra=allow，standards 输入走显式参数 + additive 字段直读 |
| `app/lib/cartography/render_diagnostics.py` | #1356 | 不改 |
| `app/lib/cartography/semantic_checks.py` | #1356（契约 v2） | 不改；作为被委托引擎（import 现有公共函数） |
| `docs/dev/ac-v11-contracts/semantic-checks.v1.json`、`tests/cartography/test_semantic_checks_contract.py` | #1356 | 不碰；standards 契约独立成 `standards-qa.v1.json` |
| `app/services/mapspec/lifecycle_engine.py`、`coordinator.py` | #1356 | 不改；fix hints 只**引用** AUTO_SAFE 操作词表常量 |
| `CHANGELOG.md`、`UBIQUITOUS_LANGUAGE.md`、`app/main.py`、`app/tools/__init__.py`、`tests/conftest.py` | #1354/#1355/#1356/#1336 | CHANGELOG/UL 追加式小幅更新（接受 rebase 摩擦），其余不碰 |
| `tests/quality/snapshots/openapi.json` | #1353/#1355 | 不加路由，不碰 |
| `modelops/geoai/**`、hotpath/claim 面 | #1336/#1335 | 禁入 |

## 只读复用（接口依赖，单向 import）

- `component_composer.required_components_for`（必配组件测量源）
- `context_matrix.evaluate_cell/validate_new_palette`（CVD/print 测量源，同源常量）
- `semantic_checks.evaluate_cartography_semantics`（结构检查证据；standards 报告引用其 rule ids，不重复判定）
- `quality_loop.cartographic_projection`（credential-safe 上下文投影）
- `palettes/sample_ramp_colors`、`symbology._CONTEXTS` 经 context_matrix 间接使用
- AUTO_SAFE 操作词表（quality_loop）+ selfheal_actions operations + REPAIR_ACTIONS（fix_hint 路由目标词表；跨模块契约测试锁定）

## 概念邻接（命名避让）

- #1351 `data_quality/semantic_checks.py`（数据质量域）→ 本方向统一用 **Standards** 前缀（CartographicRule/StandardsPack/StandardsViolation），避免第三个 "quality/semantic" 词汇。
- #1352 `app/evaluation/cartography_axes_corpus.py`（评测轴）→ 本方向 fixture 是**规则回归夹具**非评测语料；PR body 互相引用。
