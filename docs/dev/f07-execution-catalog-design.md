# F07 Execution Catalog — Design(2026-09-26)

依据 `docs/dev/f07-execution-catalog-recon.md` 的勘察结论。本设计不引入任何
第二真相:catalog 是全部权威 registry 之上的**只读派生投影**。

## 模块布局

```
app/lib/gis/execution_catalog.py            # 投影 + 指纹 + 对账 + provider 归属
app/lib/gis/execution_catalog_conformance.py # catalog 级纯函数 conformance
app/lib/gis/execution_catalog_discovery.py   # 有界 capability-first discovery
app/lib/gis/execution_catalog_staleness.py   # per-entry 指纹 diff + stale 解释
app/lib/gis/execution_catalog_docs.py        # 生成 docs/catalog/execution-catalog/
app/tools/catalog_discovery_tools.py         # agent 面只读 discovery 工具
```

## 数据契约

### CatalogEntry(frozen dataclass)

字段(kind, id, name, version, contract_version, provider, status,
capabilities, input_semantic_types, output_semantic_types,
geometry_requirements, crs_class, crs_semantics, unit_requirements,
unit_semantics, temporal_constraints, deterministic, random_seed_policy,
side_effect, resource_class{latency/memory/scale}, cancellation_profile,
resource_envelope(有界投影), fallback_targets, superseded_by,
deprecated(bool), certification(dict), priority, source_fingerprint,
detail(有界 dict))。

- `entry_fingerprint` = sha256(canonical_json(projection))[:32],投影含
  catalog_version 盐 —— 词表演进必然换指纹。
- `generation_fingerprint` = sha256(catalog_version + sorted(entry fps))。
- 认证面 `certification`:core 条目 `{"provider_kind": "core"}`;扩展条目
  `{"provider_kind": "extension", "namespace": ns, "status": valid|stale|
  missing|invalid|unknown, "certified": bool}`;host 缺席 → `unknown`
  (fail-open 披露,绝不虚构 certified=True)。

### ConformanceIssue(code, severity, kind, id, peer, detail)

复用 capability_conformance 的分级哲学:悬空引用=fatal;完整度/部署态/
等价性分歧=warning。码表(前缀 `catalog_`):

| code | severity | 语义 |
|---|---|---|
| catalog_capability_dangling | fatal | 任意层引用未知 capability id |
| catalog_algorithm_dangling | fatal | tool.algorithms / 算法引用未知算法 id |
| catalog_fallback_dangling | fatal | fallback_algorithms / fallback_capabilities / FallbackLink.to / DEFAULT_FALLBACK_CHAIN 悬空 |
| catalog_deprecation_target_dangling | fatal | deprecation_of / superseded_by 指向不存在条目 |
| catalog_algorithm_tool_missing | warning | native 算法候选工具未注册(全缺=部署退化披露) |
| catalog_recipe_capability_unreachable | warning | recipe 引用的 capability 无 native 算法链 |
| catalog_output_contract_mismatch | warning | 算法 output_artifact_type 与候选工具 output_semantic_type 双方已声明且互斥 |
| catalog_unit_geometry_mismatch | warning | 算法 unit_requirements/crs_class 与工具 unit_semantics/crs_semantics 双方已声明且矛盾 |
| catalog_deprecated_no_successor | warning | deprecated 条目无替代(工具 deprecation_of / 算法 DEPRECATED 无 fallback) |
| catalog_extension_contract_incomplete | warning | 扩展条目 7 facet 最小契约缺口 |
| (链入)capability_id_dangling 等 | 同源 | `validate_capability_conformance` 原样链入 |

### DiscoveryQuery / DiscoveryCandidate

Query:capabilities(必填 ≤8)、geometry(点/线/面/栅格/unknown)、
approx_features、crs_class、offline_required、allow_destructive(默认 False)、
max_latency_class、max_memory_class、limit(默认 5,硬上限 16)。

Candidate:{kind, id, label, capability, algorithm, tool, score, reasons[],
evidence{}};排序 (score asc, kind, id) 稳定;硬过滤产生
`excluded_*` 计数披露而非条目。罚分全部显式入 reasons
(deprecated +2.0、unclassified side_effect +0.5、unknown class +0.25、
resource envelope 超 data descriptor 硬上限 → 排除 reason)。

### Staleness

- `catalog_snapshot_ref(catalog, entry_keys=None)` → {catalog_version,
  generation_fingerprint, manifest_fingerprint, entry_fingerprints{}}:
  plan 可携带的最小 stale 凭证(entry_keys 为空 = 全量快照,有界截断披露)。
- `diff_snapshots(old, new)` → CatalogDiff{added/removed/changed(有界),
  reasons[]};码:entry_added / entry_removed / entry_content_changed /
  catalog_version_changed / manifest_generation_changed。
- `explain_staleness(stored_ref, current_catalog)` → {stale, reasons[],
  changed_entries(≤32), summary};空/损坏存储指纹 → 不判 stale(与
  manifest.is_stale_plan 同诚实规则);带 per-entry 指纹时输出**精确**
  变更清单(provider/version 变化只标记受影响条目及其 capability/recipe
  消费者,不全局作废)。

### Certification facets(扩展包最小契约)

`CERTIFICATION_CONTRACT_FACETS = (metadata, schema, cancellation,
side_effects, security, resource_estimate, tests)`;`facet_gaps(entry)`
纯函数从 catalog 投影推导缺口(如 schema = output_semantic_type 已声明;
cancellation = 工具 timeout 或算法 cancellation_profile 已声明;tests =
算法 conformance_tests 非空;core 条目不适用)。

## 缓存与刷新

`get_execution_catalog(refresh=False)` compile-once + threading.Lock 单例,
与 runtime_manifest 同纪律;测试/扩展注册后 refresh=True。编译只读
registry 事实,任何 registry 变化经 refresh 生效。

## 边界(不做)

- 不改 recipes/algorithm/tool registry 任何行为;
- 不接 main.py lifespan(catalog 惰性编译;strict 启动闸仍归 manifest);
- 不动 capability_graph.py 主体(f09 热区),对账只读其公开产物;
- 不新增 ADR 编号;不做 plan 存储格式变更(快照凭证由 f12 plan compiler
  后续消费)。
