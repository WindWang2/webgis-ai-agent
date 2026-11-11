# GIS 能力生态：唯一权威目录 → 可用性资格 → 可信执行

> G06-CAPABILITY-ECOSYSTEM｜ADR-0287｜状态：已实现（本地验收）
> 适用基线：`origin/master = 585812ee4e2d75612f2b124e6fe6587af3deca5c`

## 1. 这条线解决什么

Pi GIS Agent 之前能"读到"能力目录，但**无法区分声明与真实可执行**：

| 之前 | 之后 |
|---|---|
| `CapabilityStatus = native/planned/unavailable` 只有声明态 | `CapabilityAvailabilitySnapshot` 给出 `native_verified / available_unverified / declared_only / temporarily_unavailable / forbidden` |
| `status="native"` 的历史通过被当成当前可执行 | `native_verified` 必须**同时**满足：真实 ToolRegistry 注册 + 8 面 conformance `overall=pass` + registration/dispatch/result 三面在场 + 无 provider 依赖缺席 |
| 向量相似度高的不可用工具仍可能进候选 | 检索重排硬过滤不可用工具并显式披露 |
| 秒/米、经纬度/米的混用靠各工具自律 | 统一三态科学预检（`eligible / ineligible / not_evaluated`）+ 稳定理由码 |
| provider 资格分散在 5 个模块 | 只读聚合五要素 + 扩展生命周期 fail-closed 视图 |
| 结果摘要压缩后丢数值 | 跨面 schema/摘要对拍 + `SUMMARY_VALUE_LOSS` 诊断 |

**本线不是**：第二 Planner（G02）、Pi 宿主生命周期（G01）、Data Fabric（G03）、
MapSpec/渲染器（G04）、前端情境账本（G05）。

## 2. 唯一事实源链（不新建第二注册中心）

```
CapabilityRegistry ─┐
AlgorithmRegistry  ─┼─→ ExecutionCatalog (generation_fingerprint)
ToolRegistry ───────┘         │
   │  descriptors()            │
   ├─→ tool_capability_conformance (8 面裁决 / FALLBACK_CODE_XWALK)
   │                           │
   └───────────────────────────┴──→ CapabilityAvailabilitySnapshot（只读投影）
                                        │
        ┌───────────────────────────────┼──────────────────────────────┐
        ↓                               ↓                              ↓
 availability_rerank          method_eligibility            provider_eligibility
 （有界候选/负例）             （三态预检/量纲门）             （五要素资格/生命周期）
        ↓
 pi_availability_adapter（稳定只读消费者；不改 pi_native_surface.py）
        ↓
 ToolRegistry.dispatch（唯一执行入口；tier/权限/取消语义不变）
```

新增文件都在 `app/lib/gis/` 与 `app/services/chat/pi_availability_adapter.py`，
**没有**新的 Registry / Catalog / 认证体系 / 指纹体系。

## 3. 模块与接口

| 模块 | 入口 | 用途 |
|---|---|---|
| `app/lib/gis/capability_availability.py` | `compile_availability_snapshot()` / `query_availability()` / CLI | CAP-01 跨层只读快照、资格矩阵、指纹、权限隔离 |
| `app/lib/gis/method_eligibility.py` | `preflight_method()` | CAP-03 三态科学预检、秒≠米、经纬度≠米、fallback 诚实分类 |
| `app/lib/gis/availability_rerank.py` | `rerank_candidates()` / `validate_pi_call()` | CAP-02 可用性感知重排、有界候选、负例抑制、服务端严格校验 |
| `app/lib/gis/provider_eligibility.py` | `qualify_provider()` / `extension_lifecycle_view()` | CAP-04 五要素资格、GPU/CPU fallback 披露、停用/升级 fail-closed |
| `app/lib/gis/capability_schema_parity.py` | `parity_report()` | CAP-06 跨面 schema 漂移与摘要失真只读诊断 |
| `app/tools/capability_ecosystem_tools.py` | `register_capability_ecosystem_tools()` | M5 两项真实原生原语 |
| `app/services/chat/pi_availability_adapter.py` | `project_pi_tool_surface()` | Pi 面稳定只读消费者契约 |
| `app/lib/data/capability_lexicon_golden.json` | — | 中英 GIS 术语黄金语料（离线） |

### 可信状态词表（封闭）

| 状态 | 含义 | 判定要求 |
|---|---|---|
| `native_verified` | 当前环境已验证可执行 | 8 面 `overall=pass` + 三面在场 + descriptor 可执行 + 无 provider 缺席 |
| `available_unverified` | 真实注册可执行，但证据不全 | conformance warning / 元数据缺口，带显式 `unverified_reasons` |
| `declared_only` | 仅声明或无真实工具 | planned / 无绑定工具的能力 |
| `temporarily_unavailable` | 当前不可用 | `EXTERNAL_UNAVAILABLE` / provider 缺席 / 扩展停用 |
| `forbidden` | 权限收缩 / 跨 owner | 查询面 fail-closed，**脱敏**不泄露能力细节 |

`not_evaluated` 是**理由**不是状态：证据缺席时降级到上表对应状态并在
`unverified_reasons` 里显式写 `not_evaluated`，绝不猜。

### 单一理由码

`reason_codes` 直接取 `ToolConformanceReport.faces[].reason`（8 面的既有码），
回退语义钉在既有 `FALLBACK_CODE_XWALK`。本线**没有**新造校验逻辑或空挂码位。

## 4. 两项真实原生原语（M5）

现场审计确认 `network_od_matrix`（OD 成本矩阵）与插值 LOOCV 已有真实实现，
**不重复造**；改做两个真实缺口：

| 工具 | geometry / 数据形态 | 真实计算 | 产物 |
|---|---|---|---|
| `conditional_quantile_zonal_stats` | 面分区 + 单波段栅格 | `validate_data_path` 路径闸 → 读 metadata **先判界**（`width*height` + `RasterResourceGuard.check_grid`）→ `rasterio.read(1)` 整幅单波段 → `geometry_mask(invert=True)` + nodata/finite/where 条件掩码 + `numpy.quantile(method="linear")` | 逐 zone 多分位数、`valid_pixel_count`、覆盖率、`ref:analysis-*`（dispatch seam 铸造/登记/复用，G13 起 v2 封套不再伪造 `artifact://` URI）、科学方法披露、预算 |
| `network_service_coverage_consistency` | 线网 + 设施点 | networkx 建图 + `single_source_dijkstra_path_length` 按 scheme 覆盖边集 + Jaccard / 差异段 | 一致性数值、差异边、`ref:analysis-*`（同上）、量纲契约、预算 |

两者都真实注册进 `ToolRegistry`（`app/tools/__init__.py:_TOOL_MODULES`），
经 `apply_contract` 严格校验，走 `registry.dispatch`。
`cost_unit=seconds` 却请求 `distance/meters` 输出时返回
`UNIT_DIMENSION_CONFLICT` typed 错误（Issue #2005 的科学回归输入）。

## 5. 5 分钟本地验证（无 API Key / 无 PostGIS / 无公网 / 不等线上 CI）

```bash
# 一键（推荐）：CLI + manifest 检查器 + 2 组离线演示 + Oracle pytest + catalog 漂移门
python scripts/verify_capability_ecosystem.py

# 或分步
python -m app.lib.gis.capability_availability --bootstrap --status native_verified --limit 10
python examples/gis_capability_ecosystem/check_pack.py --json
python examples/gis_capability_ecosystem/demo_school_poi.py
python examples/gis_capability_ecosystem/demo_nodata_science.py
pytest tests/unit/gis/test_capability_ecosystem_oracles.py \
       tests/unit/tools/test_capability_ecosystem_primitives.py --no-cov -q
python -m app.lib.gis.execution_catalog_docs --check
```

PostGIS 相关专项是 **opt-in**（`pytest -m real_services`），不在默认命令里。
线上 CI 有历史生成物债务（Issue #2007），本线不等待、不修改 Actions。

## 6. 兼容、flag、回滚

- 全部新增模块 additive；**未改动** `_dispatch_impl` / `pi_native_surface.py` /
  `agent_pi_bridge.py` / `llm_result_formatter.py` / `families.py`。
- `to_dict()` 带 `schema_version`，新字段 additive，旧消费者忽略即可。
- `query_availability(permissions=None)` = 内部诊断全可见；显式空集合 = fail-closed。
- 新增工具改变 `registry_fingerprint` 与 catalog generation，用**既有生成器**
  `python -m app.lib.gis.execution_catalog_docs` 重生成产物（不手工编辑）。
- 回滚：从 `_TOOL_MODULES` 移除一行 + 关闭投影消费即恢复旧目录，无 schema 破坏。

## 7. 已知边界（诚实披露）

- 本线**不修** Issue #2006 的压缩器本身（`slim_tool_result`），只做只读诊断；
  Issue #2005 的 `families.py` 聚合口径同样留给其独立修复线。
- `CapabilityAvailabilitySnapshot` 是**编译期只读快照**，不做实时 provider ping；
  慢 provider 走 `budget.deadline_s` 有界闸，返回 `not_evaluated`。
- 扩展 `lease` 语义本项目原生 `missing`；本线用 `version_pins` + 停用后
  fail-closed 覆盖「执行中工具不被悄悄替换」，不引入跨进程租约服务。
- 演示数据是本地 fixture，不是真实统计年鉴；外部数据缺席时标 `not_evaluated`。

详见 `docs/dev/GIS_TOOL_AUTHORING_CONTRACT.md`、`docs/adr/0287-*.md` 与
`planning/G06-CAPABILITY-ECOSYSTEM/`。
