# feat(g27): 多尺度表达策略的生产接线与可解释控制

> **Draft PR** —— 关键验证（真实 Pi 子进程全链 / 本地真实浏览器 E2E）被宿主环境阻断，见「未测项」。不合并、不等线上 CI、不改 master、不改门禁。

- 分支：`zcode/g27-scale-aware-portrayal-20261111T144000Z`
- BASE：`8a9a79f76875eb95d31bd448baad971edb2e61d3`（`origin/master`，PR #2083 合并点）
- worktree：`webgis-wt-g27-scale-aware-20261111T144000Z`
- 规划/证据：`planning/G27-SCALE-AWARE-PORTRAYAL/`（RECON / PLAN / INTERFACES / DUPLICATION_MATRIX / ACCEPTANCE / LOOP_LEDGER / TEST_EVIDENCE / REVIEW / perf_probe.py）
- ADR：`docs/adr/G27-SCALE-AWARE-PORTRAYAL.md`（方向命名，不抢四位编号）

## 功能增量（真实生产路径，非离线类）

1. **P0 侦察**：重取最新 master/PR/review；确认 MapSpec 当前 v2.1（ADR-0218 的 v1.5 是历史快照）；四类尺度优先级与真实消费者清单；`DUPLICATION_MATRIX.md` 登记 #1880/#2076/#2077/#2078/#2081 等已合并能力，只回归不重建。
2. **P1 单一解析入口**：`app/lib/cartography/scale_portrayal.py` 只读复用 scene_lod / scale_rules / representation / visibility 后端孪生，产出有版本 `EffectiveScalePortrayal` + typed 诊断 + 有界解释。前端 `frontend/lib/carto-ir/scale-portrayal.ts` 是**只读投影**（tier 无后端回执时标 `not_evaluated`，不自称权威）。
3. **修复真实漂移**：`build_label_zoom_bands()` 的 [3,19) 投影到 spec 时钳为 [0,24)，消除 z<3/z≥19 死带及前后端 z=19 的 sizeRatio 不一致；四档数值一字未改，共享 corpus 双向漂移闸锁定。
4. **P2 生产接线**：`analysis_cartography_converter.py` opt-in 落笔既有 `label.zoomBands`（前端 `normalizeLabelStrategy` → `buildTextSizeExpr`/`bandTopRatio`/`selectTopLabels` 真实消费）；runtime 既有跨档重算链 + 新增 `webgis:zoom` 只读广播（zoom 手势不写 desired）。
5. **P3 组件语义**：`component_portrayal_state` 让 legend/colorbar/scale/labels 消费同一解析结果，保持完整数据域/分类/单位，不发明 variant、不改布局；四条固定 disclosures 覆盖 display-only / px vs 米 / DPI / 非统计样本。
6. **P4 可解释交互**：`ScalePortrayalPanel` 挂进 `LayerStylePanel`（当前档位、隐藏原因、覆盖、恢复自动、键盘可达）；`webgis_inspect_scale_portrayal` / `webgis_apply_scale_portrayal` 两个真实 ToolRegistry 工具；service + 受验权 REST（GET/POST `/chat/sessions/{sid}/scale-portrayal`），局部写走既有 `UpsertLayerIntent` + `apply_gis_mutation` CAS/undo，只改一个 layer。
7. **P5 出版等价视窗**：`plan_scale_contexts()` 加性解析逐页 frame extent/CRS/逻辑 viewport/DPI；DPI 只影响采样与尺寸；非 Web Mercator 不套固定公式；IR 模型与 fingerprint 不变。
8. **P6/P7**：有界 explanation（≤4096B、数组/字符串/诊断优先保留）、性能探针、backend pytest 与 frontend vitest 消费**同一份** 17-case corpus。

## 明确不做 / 排除项

- 不重做 G15/G16/G20/G24/G26；不升级 Pi vendor/gitlink/compat matrix（G22 独占）；不改数据传输 LOD 为分析语义；不扩展 3D terrain/LiDAR/SAR；不新增 mutation intent（避免破坏 registry 一致性 gate）；不新增 MapSpec schema 字段/版本；不接管 R1 issues；**不包含**开放 PR #2084/#2085/#2086 的修复。
- 起始工作区 `M vendor/pi`、`.claude/skills/*`、`_tmp_47_*` 全程只读保留。

## 证据（本地实证）

| 层 | 命令 / 结果 |
|---|---|
| 后端 unit/contract | 6 个测试文件 `68 passed in 0.73s`（隔离 venv 单 worker，两轮自复跑一致） |
| 前端契约 | `lib/carto-ir/scale-portrayal.test.ts` 已入库；本地因 wasm 阻断改用 tsc 纯 JS 转译 + Node 执行**同一模块/同一 corpus**：`23 checks passed` |
| 生产接线 | converter 落笔测试（A01/A02/user-wins/无效策略）、PublicationIR 多 frame + DPI + CRS 测试、service CAS/局部写/superseded 测试 |
| 类型检查 | `tsc --noEmit -p tsconfig.json`：唯一错误 `toolSlice.ts:32 TS1109`（BASE 既有、正是开放 PR #2085 在途修复项），**G27 相关错误 0** |
| 性能 | 5000/20000/8000 规模，5 次重复：每档 ~0.33 ms、峰值 18–34 KB、5 zoom 仅 4 次状态更新、0 次 frame mutation、feature_count 全程不变 |
| 独立 review | 见 `REVIEW.md`（findings → 修复 commit → 复验） |

## 兼容 / 回退

- **全链路 opt-in**：spec 无 `scale_profile.g27_scale_portrayal` → `applied=false`、spec 投影 `{}`、旧图逐位不变（A01）。
- 未知/旧 schema、无效 policy、幽灵字段引用 → typed 诊断 + 不落库 + 不显示「已生效」（A08/A26/A27/A28）。
- 人工隐藏在所有 zoom 保持；人工 pin/锁优先；authored `zoomBands` 不被覆盖；`恢复自动` 只删自己投影的 bands。
- 回退 = 移除该 layer 的命名空间子键即可，无迁移、无数据删除、无 schema 版本变更。

## 外部依赖 / 未测项（诚实标注，不给 Ready 结论）

- **真实 Pi 子进程 + 本地 stub LLM + 真实工具/服务全链未跑**：后端服务栈依赖（`pydantic_settings` 等）与 Pi vendor 依赖未在本方向最小 venv 安装；工具/service/API 有隔离 fixture 测试，但不声称 RPC 全链实证。
- **本地真实浏览器 E2E 未跑**：宿主 `ulimit -Hv = 4 GiB` 硬限（无法提高）使 pnpm 多 worker、Vitest/Vite、Node amaro 的所有 wasm 工具链 `WebAssembly.instantiate(): Out of memory`。前端依赖已用单进程 npm 装好，vitest 用例与最小配置已入库供 CI；本地以 tsc+Node 等价执行替代，但**不等价于 Playwright 真实旅程**。
- A02/A10/A23/A25 因此为 partial / blocked；A17/A18 为静态实现验证，未接真实 renderer 变体渲染。详见 `ACCEPTANCE.md` / `TEST_EVIDENCE.md`。
- 未等待线上 CI；未关闭/修改任何门禁；未降低安全阈值；未合并。
