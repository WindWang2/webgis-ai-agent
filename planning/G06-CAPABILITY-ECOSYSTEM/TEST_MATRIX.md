# G06 — 验收 Oracle 与测试矩阵

本地验收环境：主工作树 `.venv`（Python 3.13.17 / pytest 9.1.1 /
pydantic 2.13.4 / numpy 2.4.6 / rasterio 1.5.0 / networkx 3.6.1 / ruff / mypy）。
从本 worktree cwd 运行，`app` 解析到本 worktree（已验证 `app.__file__`）。

## 1. 14 项 Oracle 检查表

| ID | 检查点 | 硬验收要求 | 证据 / 测试 | 结果 |
|---|---|---|---|---|
| CAP-O01 | 单一事实源 | 无第二 ToolRegistry/AlgorithmRegistry/ExecutionCatalog | `test_cap_o01_no_second_registry_class_in_availability_module`（inspect 模块内类 + 源码禁用字符串）；`test_cap_o01_record_ids_all_come_from_real_registries`（417 条 record 的 tool/cap/algo id 全部可查） | ✅ |
| CAP-O02 | 可用性真实 | disabled/缺 provider 不得 `native_verified` | `test_cap_o02_native_verified_requires_conformance_pass`（213 条逐条对拍 8 面 `overall=pass` + descriptor 可执行 + 无未验证原因）；`test_cap_o02_external_unavailable_downgrades_verified`（monkeypatch EXTERNAL_UNAVAILABLE 后降级） | ✅ |
| CAP-O03 | registry 漂移 | 改 schema/扩展状态后指纹与可见候选按代更新 | `test_cap_o03_content_fingerprint_is_stable`（两次 bootstrap 完全相等）；`test_cap_o03_registry_drift_changes_generation_and_visible_candidates`（register 临时工具后代/指纹/counts 变化）；catalog 生成物用既有生成器重生成，`--check` up to date | ✅ |
| CAP-O04 | 权限隔离 | 跨 owner / 撤权 / 停用 fail-closed | `test_cap_o04_permission_isolation_fail_closed`（scoped/full/空权限三档 + forbidden 脱敏 11 字段）；`test_cap_o04_extension_lifecycle_view_fail_closed`（宿主缺席 not_evaluated、不泄露 worker/secret） | ✅ |
| CAP-O05 | 中英术语选择 | 黄金语料正确工具可检索、错误近似被披露 | `test_cap_o05_golden_lexicon_cases`（10 例：缓冲/空间连接/分区统计/路网时间/等时圈/密度/通视/插值/地理综合/秒≠米）；`test_cap_o05_supplemental_cases`（中英混合 2 例）；`test_cap_o05_seconds_never_hits_metric_distance_tools` | ✅ |
| CAP-O06 | 参数/CRS/量纲 | 经纬度不按米算；秒/米绝不混合 | `test_cap_o06_unit_dimension_conflict_seconds_vs_meters`（60 算法 × 2 组合，全部 ineligible）；`test_cap_o06_geographic_linear_distance_ineligible`（GEOGRAPHIC_LINEAR_DISTANCE + transform_hint）；`test_cap_o06_network_seconds_distance_returns_typed_error`（dispatch 段 3 变体） | ✅ |
| CAP-O07 | fallback 诚实性 | equivalent/approximation/proxy/degraded 严格区分 | `test_cap_o07_fallback_semantics_never_upgraded_to_equivalent`（遍历 AlgorithmRegistry 全部非等价声明，逐条断言不抬升） | ✅ |
| CAP-O08 | 原生算法 #1 | 真实 dispatch + artifact/ref + 科学字段 | `test_cap_o08_registration_function_registers_both_primitives`；`test_cap_o08_conditional_quantile_zonal_stats`（真实 10×10 GeoTIFF、numpy 已知答案分位数、`where` metamorphic） | ✅ |
| CAP-O09 | 原生算法 #2 | 同上，不同 geometry/数据形态 | `test_cap_o09_network_service_coverage_consistency`（线网 + 设施点、Jaccard、两方案相同 → 1.0、Polygon vs LineString 断言） | ✅ |
| CAP-O10 | 扩展包全链 | 认证/激活/发现/调用/停用/失效一致 | `test_cap_o10_extension_full_lifecycle_marks_namespace_unavailable`（真实 ExtensionHost：discover→certify_extension→activate→deactivate，status_report 状态变化 + catalog generation 变化） | ✅ |
| CAP-O11 | Pi 工具投影 | native/execute 归属、负例拒绝、schema 对拍 | `test_cap_o11_pi_projection_and_rejects`（`resolve_pi_tool_call` 只读对拍：native/execute/reject/native 包裹拒绝 + hallucinated + 多余字段） | ✅ |
| CAP-O12 | 资源纪律 | 大量工具/长结果/慢 provider 有界降级 | `test_cap_o12_query_and_rerank_are_bounded`（limit=5、200→8 候选、100k 长串压缩到 612 字符）；`test_cap_o12_slow_provider_returns_not_evaluated_within_budget`（sleep(30) 被 deadline 闸短路，<5s 返回） | ✅ |
| CAP-O13 | 文档可执行 | 示范包完整可本地跑的命令与日志 | `test_cap_o13_docs_and_examples_are_locally_executable`；`scripts/verify_capability_ecosystem.py` **6/6 passed**（实际日志见 LOOP_LEDGER L5） | ✅ |
| CAP-O14 | 回归保护 | 既有工具名/签名/历史 recipe/MapSpec 行为兼容 | `test_cap_o14_parity_regression_guard`（5 个 legacy 工具 required 参数集对拍）；gis 全套 **798 passed / 3 skipped**；catalog/元数据/Pi 面 **78 passed / 1 skipped** | ✅ |

## 2. 本地验收命令（可复现）

```bash
VENV=/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python
WT=/home/kevin/projects/webgis/webgis-wt-gis-capability-ecosystem-20261107
cd $WT

# 一键（推荐）
$VENV scripts/verify_capability_ecosystem.py                      # 6/6 passed

# 14 Oracle
$VENV -m pytest tests/unit/gis/test_capability_ecosystem_oracles.py \
  tests/unit/tools/test_capability_ecosystem_primitives.py --no-cov -q   # 24 passed

# 回归（分批，无 -n）
$VENV -m pytest tests/unit/gis/ --no-cov -q                      # 798 passed / 3 skipped
$VENV -m pytest tests/unit/test_audit4_tools_meta.py \
  tests/unit/test_component_catalog_parity.py \
  tests/unit/test_pi_extension_hardening.py \
  tests/unit/test_scope_disclosure_payload.py \
  tests/unit/gis_harness/test_capability_resolution.py --no-cov -q  # 78 passed / 1 skipped

# 质量门
$VENV -m ruff check <changed files>                              # All checks passed!
$VENV -m mypy app/lib/gis/capability_availability.py \
  --ignore-missing-imports --follow-imports=silent              # Success

# 生成物漂移门
$VENV -m app.lib.gis.execution_catalog_docs --check             # up to date
```

## 3. 性能指标与预算证据（M7）

| 项 | 实测 | 上界 |
|---|---|---|
| 完整 ToolRegistry | 399 工具（基线 397 + 2 原语） | 8 面报告 `MAX_REPORTS=1024` |
| 能力快照 records | 417（213/186/18） | `MAX_RECORDS=1024`，超出显式 truncated |
| 快照 content_fingerprint | 64 hex 字符 | ≤128 |
| 查询返回 | `limit=5` → 5 条 + `truncated=True` | `MAX_QUERY_LIMIT=256` |
| 检索候选 | 200 → 8，`bounded=True` + 截断诊断 | `_MAX_CANDIDATES=512` 防御上限 |
| 长结果 | 100,000 字符 → 612 字符（既有 slim） | 有界 |
| 慢 provider | `deadline_s=0.05` 立即 `not_evaluated`，实测 <5s | `_MIN_FULL_PROBE_BUDGET_S=0.25` |
| 栅格原语 | 10×10 = 100 像元 | `MAX_RASTER_CELLS=40_000_000` |
| 线网原语 | 4 边 / 2 设施 | `MAX_NETWORK_EDGES=20_000` / `MAX_FACILITIES=2_000` |
| Oracle 套件 | 24 passed / ~30s | pytest `timeout=180` |
| gis 全套 | 798 passed / 109s | 分批执行，无 `-n` |

## 4. 失败归因表（禁止掩盖）

| 失败 | 归因 | 处置 |
|---|---|---|
| M1 23 failed | 预期红灯（实现未落地） | 实现后 23→24 passed（新增 1 例生命周期断言） |
| 首次 Oracle 2 failed | O13 文档待写 + O10 fixture 数据错误 | 写文档；修 fixture `manifest.name`（断言未动） |
| ruff 23 / mypy 3 | 改动引入 | 全部修复，复查 clean |
| 一键验收 5/6 | `check_pack.py` 缺 sys.path 引导 | 补引导，6/6 |
| `test_catalog_doc_matches_registry_projection` | **改动引入**：+2 参数契约 → ALGORITHM_CATALOG 127→129 | 既有生成器重生成，1 passed |
| `CAPABILITY_CATALOG.md` 重生成噪声 | 环境差异（model 10→0、orphan 66→75），与 G06 无关且无 parity 测试依赖 | 回退到 master，不带进 PR |
| quality gate 30 failed / 44 errors | **基线历史债务 + 测试间隔离**，非 G06 引入：`app/lib/quality/` 对 master diff 为空；单独跑 ERROR 项 2 passed；Issue #2007 已记录 #1993 后 CI 失败 | 如实记录；不修改 Actions、不等待线上 CI、不宣称 CI 通过 |
| `check_generated_staleness.py` 8 项 stale | **基线债务**：`git show origin/master` 重算的 17 项输入指纹全部 ≠ 账本（master 自己就不匹配）；`content_fingerprint` 与账本一致仅 input 变 | 不 `--update` 掩盖；PR body 显式披露 |
| 本方向引入的 2 个 `TOOL_UNTESTED` | 新测试文件 untracked，`git ls-files` 扫不到 | 暂存后 `discover_test_references` 命中，findings 回到基线 8 |
| `TOOL_DESCRIPTOR_INCOMPLETE: nearest_facility` | 既有工具、本线未改 | 基线债务，不抢独立 issue |

## 5. 未验证边界（诚实披露）

- **PostGIS 专项**：opt-in（`pytest -m real_services`），不在默认本地验收内。
- **真实 LLM / 公网**：无 API Key、无网络；Pi 面只对拍 `resolve_pi_tool_call`
  与有界投影，未跑真实 LLM 端到端旅程（属 G01/G05 范围）。
- **线上 CI**：不等待、不宣称通过；Issue #2007 的历史生成物债务仍在。
- **演示数据**：本地 fixture，不是真实统计年鉴；外部数据缺席标 `not_evaluated`。
- **扩展 lease**：项目原生缺失，本线用 version_pins + 停用 fail-closed 覆盖，
  未引入跨进程租约服务。
