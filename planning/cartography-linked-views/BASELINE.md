# BASELINE — Track B（carto-linked-views-labels）

- 基准：origin/master `4b3629c90ee977e287f7b1fb4fd827b0fad7428c`（2026-10-31 fetch）
- worktree：`/home/kevin/projects/webgis/webgis-carto-linked-views-labels-vnext`，分支 `zcode/carto-linked-views-labels-vnext`
- 环境：uv venv (Python 3.13) + requirements-dev.txt（pytest 9.2.0）；frontend pnpm install OK（vitest 4.1.11）
- 基线测试：
  - `pytest tests/unit/test_feature_pages.py tests/test_layer_features_page.py` → 21 passed（2026-10-31）
  - `pnpm exec vitest run lib/data-plane/paged-features.test.ts components/map/map-components/table-panel.test.tsx` → 11 passed

## GitHub 现状（2026-10-31 实测）

Open：#1868 数据准备 / #1869 分析工作流 / #1870 制图设计候选 / #1871 atlas durable job / #1872 async LLM / #1873 要素编辑。
最近 merged：#1867/#1836/#1835（3D 系列）、#1763 模板资产。

## 关键现状事实（三路勘察，file:line 证据见各代理报告，本文件记结论）

1. 分页契约已有：cursor(base64 v1 索引)/409 revision guard/bbox 相交粗滤/fields 白名单/limit≤1000/WINDOW_SCAN_BUDGET=10k（app/services/feature_pages.py, app/api/routes/layer.py:127-230）。
2. 缺：排序、typed filter 谓词、total_matching/truncated/reason_codes、会话 ref 聚合端点（目录侧有 aggregate_dataset 但不服务会话 ref）。
3. MVT 不编码 feature.id（app/services/mvt.py:217-226 `_encode_feature` 不写 field 1）；图层名固定 "data"（mvt.py:45）；单要素查询 O(n) 线性扫（layer.py:244-259）。
4. 重复 feature id：无任何处理（摄入仅整包指纹去重）。
5. 前端：`frontend/lib/data-plane/paged-features.ts` 客户端已建成但**零生产消费**；table-panel 在 MVT 层短路（table-panel.tsx:141,167-169）；SelectionContext 六向联动 inline 路径 production yes。
6. chart 视口统计仅 inline ≤5000 采样（chart-panel.tsx:119-138）；MVT 层无服务端聚合通道。
7. 标注：live=MapLibre GPU 贪心（text-allow-overlap:false），export=确定性单遍（MAX_LABELS_PER_EXPORT=400）；label-grid.ts 已写未接线。专业错误级差异：导出 priority=收集序非重要度（mapspec_to_svg.py:1961）、线 repeat 不对称、CJK 折行导出缺失、晕圈/降级不对称。
8. #1873 身份契约：FEATURE_ID_KEYS 链 + `_wfid`（wf1-sha256[:16]，fingerprint:index）物化；Track B 的 MVT id 方案与其对齐（见 INTERFACE_CONTRACTS.md §3）。

## 交叠矩阵（摘要，全文见 PR_OVERLAP_MATRIX.md）

禁区：`design_candidates*/component_registry/semantic_checks*`（#1870）、`publication_export.py/atlas_batch/*`（#1871）、`map-panel.tsx/use-feature-selection.ts/feature_edit/*`（#1873）、`catalogue.ts/sceneCommands.ts`（5 PR 共改，最小化）、`messages/*/map.json`（#1870）。
安全区：feature_pages.py、mvt.py、layer.py、table-panel.tsx、chart-panel.tsx、selection-store.ts、viewport-context.ts、paged-features.ts、label_plan/engine/collision/typography.py、mapspec_to_svg.py、label-*.ts、mapspec-to-svg.ts、exporter.ts。
生成物（最后一次性提交）：tests/quality/snapshots/openapi.json、app/tools/__init__.py、endpoint-scope-matrix.csv。
