# F07 Execution Catalog — Review 记录(2026-09-26)

独立 Reviewer(Subagent C,未参与实现)对 `origin/master...HEAD` 全量
diff 完成深审;结论 **APPROVE-WITH-FIXES**(无 P0)。本记录列发现与处置;
全部 P1 已修复,低风险 P2 尽修,余项记录为后续方向。

## Review 发现与处置

### P1(4/4 已修复)

| # | 发现 | 修复 |
|---|---|---|
| P1-1 | `reconcile_with_manifest` 在真实 registry 上饱和 128 条 `catalog_manifest_field_divergence` 误报:catalog 工具投影用生效面 capabilities(含算法派生回填,179 个 derived-only 工具),manifest v4 投影是声明面 —— 同一事实两种口径被直接比对 | 工具条目补 `detail.declared_capabilities`(capability_source=="declared" 时才非空),reconcile 对工具按声明面对账;新增真实 registry 上 field divergence == 0 的基线锁测试 |
| P1-2 | `explain_staleness` 对损坏/未知版本快照判 stale=True,违背自身 docstring/设计文档承诺的「与 is_stale_plan 同一诚实规则」 | 对齐 `runtime_manifest.is_stale_plan`:损坏 → stale=False + 披露码 + summary.note;docstring/设计文档/测试三方同步 |
| P1-3 | discovery `query.geometry` 是死参数;`geometry_requirements_matched` 无比较即发射(agent 面虚假证据);`REASON_GEOMETRY_UNKNOWN` 全库未用 | 实现几何三态判定 `_geometry_verdict`:matched(无罚)/ mismatch(软罚 0.5 不排除)/ undeclared(披露无罚);查询无几何时不产生几何理由;evidence 携带 capability_geometry |
| P1-4 | 新工具未声明功能分类,把基线已红的 `test_tool_registry_rationalization` 未归类集合从 12 扩到 14 | `app/tools/categories.py` `_MODULE_DEFAULTS` 增 `app.tools.catalog_discovery_tools: inspection`(同 data_discovery 先例);验证未归类集合回到基线的 12 个(geoai/rs/scene 波次,pre-existing) |

### P2(6 修复 / 3 记录)

已修复:
1. **认证诚实三态**:缺省编译(无证据注入)时 core 条目 `certified=None`
   (不虚构 certified=True);注入证据后 core 才为 True;`cert_state`
   进入条目指纹(证据吊销可被快照感知)。
2. **单例抖动**:`get_execution_catalog` 显式传 `tool_registry` /
   `certification_index` 时绕过进程单例,不写全局缓存。
3. **离线未知披露**:`offline_required` 下 `network=None` 工具放行但
   reason 披露 `offline_network_unknown`,evidence 携带 network 原值。
4. **栅格资源包络**:raster 查询按 `hard_max_cells` 判(缺声明回退
   features 口径)。
5. **截断披露**:快照 `entry_keys` 超 512 披露 `entry_keys_truncated`;
   `reconcile_summary()` 提供 truncated 标志;capabilities 投影上限
   16→64 并带 `capabilities_truncated` 披露。
6. **docs 生成器**:6 个 generator 共享单次编译 + 单次 conformance
   (`_BUILD` cache,generate() 入口清空)。
7. **dynamic provider 面**:induced.* capability 条目 provider 字段与
   certification.provider_kind 一致(dynamic)。
8. **设计文档同步**:码表(output_contract 语义/弃用独占/单元几何)、
   快照 scope 语义、认证三态、双口径口径全部与实现对齐。

记录为后续(不阻塞):
- certification_index 生产接线(lifespan 注入 host 证据)——main.py 是
  兄弟 PR 热区,接线留给扩展平台侧顺路 PR;当前 core-only 部署为真。
- DEFAULT_FALLBACK_CHAIN 悬空腿在 catalog conformance 中未单列(由
  registry_validation 启动闸 + recipes.py load_builtins fail-loud 兜住)。
- 预算断言宽松校准(compile <60s vs 实测 1-3s)——留给 perf lane 收紧。

## 已检查无发现(Reviewer 独立确认)

单一真相(无第二 registry/指纹原语)、字段投影忠实(抽样对照)、
conformance fatal/warning 分级与链入等价性(与 manifest 侧 234 条一致)、
discovery 确定性(tie-break 全路径)、staleness 归因方向、有界性
(MAX_* 全落实)、兼容性(单行接线,自指固定点稳定)、测试有效性
(负例可杀变异)、工具面契约(args_model/kwargs 一致、错误走
{"error":...} 约定)。

## 基线红测试(本分支验证为 pre-existing,独立 commit 修复其中两项)

干净 origin/master(临时 worktree 复现)即红的失败集,与本分支无关:
- `test_foundation_v2_infra.py::test_catalog_doc_matches_registry_projection`
  ——ALGORITHM_CATALOG.md 漂移(**已修**,commit 00abe091);
- `test_workflow_guards.py::test_catalog_matches_registry`
  ——workflow-catalog.md 漂移(**已修**,独立 commit);
- `test_tool_registry_rationalization.py::test_every_tool_is_classified`
  ——12 个 geoai/rs/scene 工具未归类(pre-existing,不在 F07 范围);
- `test_workflow_guards.py::TestManifestStaleEndToEnd::*`、
  `test_workflow_v4_budget.py::test_corpus_evaluation_budget`
  ——基线同红(定时/预算敏感,与本分支无文件交集);
- `test_budgets.py::test_graph_build_under_budget`
  ——150ms 墙钟 flake(基线通过/失败随负载抖动)。
