# GIS 工具 / 算法 / 扩展包 Authoring 契约（G06）

> 面向：要给本仓库新增 GIS 工具、算法元数据或扩展包的开发者。
> 权威事实源：`app/tools/registry.py` + `app/lib/gis/*_registry.py` +
> `app/lib/gis/execution_catalog.py` + `app/extensions_platform/`。
> 本文件是**契约说明**，不替代上述权威源。

## 0. 一句话

> 工具必须真实注册进唯一 `ToolRegistry`；声明 ≠ 可执行；
> 科学语义必须机器可读；缺证据标 `not_evaluated` 而不是猜。

## 1. 新增工具的 7 步（最小可运行清单）

1. **查缺口**：先用能力矩阵确认没有同等实现，避免重复造轮子。
   ```bash
   python -m app.lib.gis.capability_availability --bootstrap --json > /tmp/matrix.json
   python examples/gis_capability_ecosystem/check_pack.py --json
   ```
   本方向审计实例：`network_od_matrix` 与插值 LOOCV 已有真实实现，
   于是改做「条件多分位数分区统计」与「线网服务覆盖一致性」两个真缺口。

2. **写真实实现**：函数名即工具名，做真实计算，**禁止 dummy / no-op**。
   参考 `app/tools/capability_ecosystem_tools.py`。

3. **声明参数契约**（唯一 `ParameterContractRegistry`）：
   在 `app/lib/gis/algorithms/<域>.py` 导出 `PARAMETER_CONTRACTS`，
   经 `iter_contract_packs()` 汇入；工具侧用 `apply_contract(id, params)`
   严格校验。参数词表是封闭的（`PARAM_UNIT_VOCABULARY`）。

4. **注册描述符**（`@tool` / `registry.register`）：必须声明
   `capabilities`（真实 capability id）、`side_effect`、`deterministic`、
   `output_semantic_type`、`produced_refs`、`unit_semantics`、
   `crs_semantics`、`tags`、`failure_modes`、资源三级
   （`latency_class` / `memory_class` / `scale_class`）。
   未知 kwarg 在注册期直接报错（`_KNOWN_REGISTER_KWARGS`），不会静默吞掉。

5. **挂进 `_TOOL_MODULES`**（`app/tools/__init__.py` 一行），
   让 `init_tools()` 真实可达。

6. **跑 8 面对账**：
   ```bash
   python - <<'PY'
   from app.lib.gis.tool_capability_conformance import collect_live_facts, audit_tool_conformance
   f = collect_live_facts(bootstrap=True)
   for n in ("conditional_quantile_zonal_stats", "network_service_coverage_consistency"):
       r = audit_tool_conformance(n, f)
       print(n, r.overall, [(v.face, v.status, v.reason) for v in r.faces])
   PY
   ```
   8 面 = registration / search / planner / pi_native / proxy / dispatch /
   result / science。`warning` 必须带显式理由，不许静默。

7. **重生成 catalog 产物**（新工具会改变目录代）：
   ```bash
   python -m app.lib.gis.execution_catalog_docs
   python -m app.lib.gis.execution_catalog_docs --check   # 必须 up to date
   ```
   **不要手工编辑** `docs/catalog/execution-catalog/*`，也不要另写生成器。

## 2. 结果封套（强制）

每个新工具必须返回可审计封套：

```python
{
  "data":     {...},   # 关键统计数值必须在这里，不能只剩「有 N 个区」
  "artifact": {"ref": "artifact://...", "content_fingerprint": "...",
               "pageable": True, "page_size": 50},
  "science":  {"method": "...", "effective_denominator": "...",
               "uncertainty": "...", "determinism": "...", "provenance": [...]},
  "budget":   {"max_*": ..., "elapsed_ms": ..., "truncated": False},
}
```

理由：压缩后的 slim payload 可能只保留 `summary`（Issue #2006）。
若 `data` 被丢，Agent 只能说「有 N 区」。因此**摘要必须含关键数值或指向
可分页 ref**。`parity_report()` 会对此做只读诊断。

## 3. 科学语义契约（硬门）

| 约束 | 规则 | 违反后果 |
|---|---|---|
| 量纲 | `seconds/s/min`（时间）与 `m/meters/km`（长度）绝不混用 | `ineligible` + `UNIT_DIMENSION_CONFLICT` |
| CRS | `crs_class=geographic` 不得按米算距离/面积 | `ineligible` + `GEOGRAPHIC_LINEAR_DISTANCE` + `transform_hint` |
| 前置条件 | 任一 `deferred=True` 的 PASS 不等于通过 | 降级 `not_evaluated`（`unknown != pass`） |
| fallback | `proxy / approximation / degraded` 绝不冒充 `equivalent` | 逐字取 `AlgorithmDescriptor.fallback_semantics` |
| 有效样本 | nodata / NaN / 条件筛选共同构成分母 | 必须输出 `valid_pixel_count` 与覆盖率 |
| 数据缺席 | 外部数据/算力缺席 | 返回 `not_evaluated` / `unavailable`，不得标「已验证科学方法」 |

统一入口：`preflight_method(algorithm_id, space=..., params=...)`。

## 4. 扩展包 / 认证（不造第二体系）

- manifest 的 `id` **必须**等于 `f"{namespace}.{name}"`
  （`app/extensions_platform/manifest.py:466`）。这是最常见的 authoring 错误。
- 包级认证：`certify_extension(host, extension_id)`。
- 能力级认证：`run_pack_certification(...)`（6 stage + `_report_hmac`）。
- 签名：`sign_pack(pack_dir, key_id, key_file)`（HMAC-SHA256 / Ed25519）。
- 激活：`host.discover()` → `certify_extension` → `host.activate(id)`；
  停用 `host.deactivate(id)` 后 namespace 必须立即不可选（fail-closed）。
- 权限收缩、版本钉（`version_pins`）、撤权都走 `ExtensionHost` **公开 API**，
  禁止直调私有 worker / 拒绝自动信任网络下载的未签名插件。
- 示范包：`examples/gis_capability_ecosystem/`，
  `check_pack.py` 只做静态契约检查，**静态检查 ≠ 认证通过**。

## 5. Pi 工具面（G01 边界）

- `pi_native_surface.py` 冻结 7 个 native；`webgis_execute` 是 host 侧解包代理，
  **不是** registry 工具。不要新增第二 native catalog。
- 需要可用性感知时，消费 `pi_availability_adapter.project_pi_tool_surface()`，
  不要改 `pi_native_surface.py`。
- 有界候选由调用方 `max_candidates` 决定（生产默认 8，硬上限 64/512 防御）。
- 服务端必须调 `validate_pi_call()`：拒绝 hallucinated 工具名、参数多余字段、
  宽松 pi-schema coercion；native 被包进 `webgis_execute` 必须拒绝。

## 6. 测试 golden（每个新工具至少）

1. **已知答案 oracle**：手算 / numpy 对拍数值（不是"返回非空"）。
2. **metamorphic**：条件或输入变化时，有效样本分母 / 结果必须变化。
3. **负例**：量纲冲突、缺失输入、不可用 provider、hallucinated 工具名。
4. **回归**：既有工具名 / required 参数集 / 历史 recipe / MapSpec 对外行为不变。
5. **资源**：大量工具、长结果、慢 provider 在有界时间/内存内正确降级。

本方向参考实现：
`tests/unit/gis/test_capability_ecosystem_oracles.py`（18 例）与
`tests/unit/tools/test_capability_ecosystem_primitives.py`（5 例）。

```bash
pytest tests/unit/gis/test_capability_ecosystem_oracles.py \
       tests/unit/tools/test_capability_ecosystem_primitives.py --no-cov -q
```

## 7. 兼容 / 版本门 / 回滚

- 快照 `schema_version = "capability-availability/v1"`；
  新字段一律 additive，`to_dict()` 保留旧字段。
- 能力矩阵查询：`query_availability(snapshot, status=..., limit=..., offset=...)`，
  带内容指纹与显式未验证原因；跨 owner / 撤权 / 空权限一律 fail-closed 且脱敏。
- 回滚：从 `_TOOL_MODULES` 移除注册行 + 关闭投影消费；
  不改动既有 dispatch / 权限 / 审计 / 取消语义。
