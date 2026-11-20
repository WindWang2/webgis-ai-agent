# G15 BASELINE — 启动基线记录

- 任务: G15-MULTIFRAME-CARTOGRAPHY (persistent multiframe / small multiples cartography products)
- Branch: `zcode/goal-g15-multiframe-cartography-20261110T153244Z`
- Worktree: `/home/kevin/projects/webgis/webgis-worktrees/goal-g15-multiframe-cartography-20261110T153244Z`
- BASE_BRANCH: `master`；BASE_SHA: `816705218a5aa6202bf15d745cb31cddb3437f44` (= origin/master at start, also research snapshot)
- Python: main workspace `.venv` (3.13.17) — run pytest from worktree cwd (cwd `app` wins over editable install). Verified.
- Frontend: pnpm + vitest (`pnpm test` → vitest run).

## 环境验证（WP01 真实旅程）

- `pytest tests/cartography/test_publication_ir_golden.py tests/test_export_frames_v5.py` → **23 passed** (87s) on clean baseline.

## 已核实的现有能力（file:line 基于 BASE_SHA）

| 能力 | 状态 | 证据 |
|---|---|---|
| MapSpecFrame (id/title/view/extent/layerOverrides/pageSize/enabled), 写入面 layout.frames, cap 50 | present | `app/lib/cartography/mapspec_schema.py:780-801,846-852` |
| SetLayoutIntent 无 frames 字段（frames 目前由 temporal/simulation 服务直接写） | present | `app/services/mapspec/intents.py:212-226`; temporal 经 `SetTemporalViewIntent` |
| `_apply_frame_overrides` 逐键 merge | present | `app/services/publication_export.py:436-462` |
| PublicationIR: PageLayoutIR.frame_index 单帧/页；time/category/feature/frames drivers | present | `app/lib/cartography/publication_ir.py:115-147,376-519` |
| 导出帧通道（请求级）: export_thematic_map frames → export_map 命令 → 前端 ExportRequest.frames | present | `tests/test_export_frames_v5.py` |
| 前端 specFramesToExportFrames 只投影 title/extent/view（override/pageSize 不可映射 → 后端 publication PDF 承接） | present | `frontend/lib/map-kit/spec-frames.ts:1-79` |
| small_multiple_map runtime_status="planned" | present | `app/lib/cartography/model_packs/presentation_views.py:14-33` |
| 双视图 side-by-side/swipe（同 MapSpec layer family；workbench comparison state） | present | `frontend/components/map/comparison/comparison-view.tsx`; sync 纯函数 `comparison-sync.ts` (resolveSyncPair/readCamera/applyCameraPatch) |
| TemporalViewContract.v1（真实时间桶 frames、TemporalClassification scope=shared、compare、playback） | present | `app/lib/cartography/mapspec_schema.py:869-1010`; service `app/services/gis_harness/temporal_products/service.py` |
| 组件图/组件模板/组合契约（composition_contract） | present | `app/lib/cartography/component_*.py`, `composition_contract.py` |
| atlas_batch (planner/executor/page_compiler) | present | `app/services/atlas_batch/` |

## 差异定位（本任务新增语义，不重复已有）

1. **产品内多画幅**：现有 layout.frames 是"atlas 逐帧分页"语义（每帧一页）；ExportRequest.frames 是请求级临时导出。都不提供：同一产品内 N 个有身份画幅的并置/联动/编辑/保存。
2. **frame 数据绑定**：MapSpecFrame 无字段/时间窗/数据绑定；temporal 契约有真实时间桶（可复用为 frame 数据来源）。
3. **共享表达资格**：TemporalClassification 有 scope=shared 的先例（两期对比）；通用跨画幅共享 domain/breaks + 资格判定缺失。
4. **同页多画幅出版**：PageLayoutIR 单 frame_index/页；无 page 内多帧 content rect。
5. **画幅联动相机**：ComparisonView 双图；无 N 画幅 sync groups。
6. **逐画幅验收**：现有 ACK/终验面向整份 spec；无 frame_id+revision 对账。

## 开放/已合并 PR 排重基线

- Merged: #1973 (Pi v1.5.0), #1873 (feature editing), #1875 (linked views/labels base), #1975 (publication cartography), #2011-#2017 (compiler/Pi runtime/data qualification/workbench/composer/capability/reliability), #2033-#2036 (command catalog / Studio single-source / agent commit undo / Pi-only). All re-verified merged at BASE_SHA.
- Open: #2021 (G11 briefs — 不做故事/简报), #2022 (G10 refresh — 不做数据刷新), #2032 (UI redesign — 最小接入), #2073 (G13 capability — 只消费既有可用性投影，不改其管线)。
- G12/G14/G21 worktrees exist without PRs; boundaries per §C/§D of task doc.
