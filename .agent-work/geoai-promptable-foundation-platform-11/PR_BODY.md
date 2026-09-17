# GeoAI Promptable Foundation Platform 11 — GeoPrompt artifact · 候选 · embedding cache · 多模态 seam · GeoAI 面板（ADR-0198）

**Baseline:** `origin/master` = `faa453a8935101378c23eb6694a42c3616d9c670`（启动时刷新；GOAL 模板内嵌的 `ebcafb4d`/PR #991/#992 快照已过时）。
**Branch:** `zcode/geoai-promptable-foundation-platform-11`。**Local evidence only; no online CI dependency.**
**Diffstat:** 94 files, +7885/−38；11 commits（Phase 0 审计 → 契约 → 执行能力 → surface → 硬化 → E2E → 独立 review remediation）。

## 交付内容（WP-A…H）

- **WP-A GeoPrompt artifact v1**（`app/lib/modelops/geo_prompt.py`，SCHEMA 见 `.agent-work/geoai-promptable-foundation-platform-11/SCHEMA.md`）：内容寻址 `artifact_id`、CRS 身份、polyline/polygon/reference-layer/mask-sidecar 提示、时间语义、目标模型/波段绑定、provenance；确定性编译（仿射往返容差 1e-6px、像元中心包含/触及栅格化、digest fail-closed、256M px 上限、旋转栅格下 box 拒收）。
- **WP-B/C 可提示 provider seam 深化**：`mask_candidates` 能力位 + `TileOutput.mask_candidates/candidate_scores/candidate_sources` 契约与双侧校验；参考实现确定性 3 候选（tight/relaxed/box-fit）+ 启发式排序分（显式 `heuristic` 标注；mask-only 覆盖项防"空候选满分"）；engine `best|index` 权威裁决 + 全候选 GeoJSON 发布 + 候选缺失 fail-closed。
- **WP-D embedding cache**（`app/services/modelops/embedding_cache.py`）：(model×provider_id×semver×software_env×asset×preprocess×grid×**绝对**window×owner) 键控；sidecar 提交标记；get 流式 digest 校验（篡改→驱逐+miss）；entries/bytes LRU 双上界 + 单条目上限；部分失效（模型/资产）；**resume**（命中跳过读取+推理）；原子发布 + tmp 残件清理；ROI 语义与主路径一致（P0 review fix）；`MODELOPS_EMBED_CACHE_*` 旋钮（.env.example + conftest parity）。
- **WP-E 文本/多模态 seam**（`app/lib/modelops/multimodal.py`）：TextEncoder 协议 + 显式 stub（semantic_version `text-stub/1.0.0`）+ 类别原型 zero-shot 余弦映射；**无 encoder 时语义面全部 typed 拒绝**（`MultimodalUnsupported`——平台不伪装文本理解）；`MODELOPS_TEXT_ENCODER` 旋钮（默认不接线）。
- **WP-F agent tools**（`app/tools/geoai_tools.py`，注册进 `_TOOL_MODULES`）：`geoai_prompt_artifact_inspect`（校验+身份+编译 dry-run）、`geoai_run_promptable`（artifact 或 points/boxes + 候选 + 裁决）、`geoai_prompt_refine`（候选→内容寻址先验→重跑；to_thread 卸载）、`geoai_embed`（+cache 观测）、`geoai_semantic_zero_shot`。
- **WP-G HTTP + UI**：`/api/v1/geoai/{models,status,preview,prompt-segment,prompt-refine,embed,artifact-geojson}`（source_uri 与 artifact 内嵌引用均经 DATA_DIR 门；preview ≤512px、nodata/NaN 安全拉伸）；`frontend/components/geoai/` 面板（预览画布、点/Shift-拖拽框提示、候选分色预览、接受→精化、撤销、批次队列；ADR-0144 键化 i18n zh/en；null-CRS 像素面正确映射）。
- **能力词汇**：`model_promptable_segmentation`（capability）+ `model.inference.promptable_segmentation`（algorithm）+ 任务映射更新——capability↔algorithm parity 契约保持（ADR-0198）。
- **顺带修复的既有缺陷**（被新路径暴露）：promptable GeoJSON 地理参考系数序（GDAL↔shapely，P1）；mask-only prompt 不可达（provider JSON 重建丢先验）；先验掩膜内容不进复用键（count-only 错结果复用）；`(1,H,W)` 波段维未归一化。

## 与当时 open PR 的 dedupe / ownership

启动审计（BASELINE.md）：唯一 open PR = **#1335**（harness claim/tenant fail-closed，11 文件）——与本 track 文件交集 = ∅，其文件全程 read-only；issues #1330–#1334 归 #1335，未重复实施。`frontend/components/map/**`（本地 codex 分支领域）未触碰。rescope：GOAL 模板的 C++ 路径映射到本 Python/Web repo（PARALLEL_OWNERSHIP.md §5）。

## 架构决定（DECISIONS.md 全文）

D-001 `.agent-work/` 规划目录（`.planning/` 被 gitignore）；D-002 rescope；D-003 在 ModelOps V2 之上深化不重建；D-004 issue dedupe；D-005 审计落盘时序；D-006 技能加载时序；**D-007 ADR 编号 0197→0198**（mission_runtime 已占 0197；Phase 0 目录盘点在旧检出上的教训）。

## 兼容性

- 指纹/复用键：新增字段全部**条件发射**（anchor/artifact/prior digest/text/labels/候选参数/mask_candidates 能力位）——不使用新特性的旧请求保持字节级同 key。
- `PromptSpec.anchor_box`、`InferenceRequest.prompt_artifact_id/prompt_audit/return_candidates/candidate_selection/selected_candidate`、`ProviderCapabilities.mask_candidates`、manifest `prompt_audit` 节：全部默认值 = 旧行为。
- 既有 master 测试变更 1 处：`test_capability_graph_v8::test_models_for_image_segmentation`——`tiny-promptable-seg` 随词表拆分改挂 `model_promptable_segmentation`（ADR-0198 显式变更，非跳过/删除，并新增反向断言）。

## 本地测试证据（Local evidence only）

连续两跑（Phase 8 gate，原样命令连跑两次结果一致）：

| Gate | 命令 | Run1 | Run2 |
|---|---|---|---|
| modelops 全量 | `pytest tests/unit/modelops tests/integration/modelops -o addopts= -q` | **341 passed, 10 skipped** | **341 passed, 10 skipped** |
| capability parity | `pytest tests/unit/gis_harness/test_capability_graph_v8.py tests/unit/gis_harness/test_component_composition.py -o addopts= -q` | **59 passed** | **59 passed** |
| geoai+i18n 前端 | `vitest run components/geoai test/i18n` | **32 passed** | **32 passed** |

另：前端全量 `vitest run`（remediation 前）**3709/3709**；`tests/unit/gis_harness` 全量（1707+）唯一失败即 parity（已修）；ruff（app+tests）clean；`git diff --check origin/master...HEAD` clean；冲突标记/secret 扫描 clean。
已知 flaky（pre-existing，与本 diff 无关，单跑稳定通过）：`tests/unit/modelops/test_registry_cache_preprocess.py::test_preprocess_batch_memory_guard`（负载敏感）、`frontend/.../chat-tab.render-scope.test.tsx`（全量负载下超时）。

## 独立 adversarial review（REVIEW_LOG.md 全文）

3 个只读 reviewer × 全 diff：**P0×1、P1×7、P2×13、P3×16 → 全部 disposition**。
- P0：embedding cache 忽略 roi_origin（错读+跨 ROI 键污染）→ 修复 + 回归测试。
- P1：cache 同键双写自毁；text/labels 指纹缺口；polygon+sidecar 静默丢几何；artifact 引用路径绕过 HTTP 数据门（+digest oracle）；`/geoai/models` 无 session 500；refine 工具阻塞事件循环；前端 null-CRS y 翻转；capability↔algorithm parity。
- 修复后复跑上述三 gate 全绿。

## Known limitations / follow-ups

1. `SemanticClassMap` 为进程级原型注册（`replace=True` 显式覆盖语义）；owner 维度隔离留作 follow-up。
2. `/geoai/artifact-geojson` 视 DATA_DIR 为同信任域（与既有 raster.png 路由同口径）；按产出 run owner 收窄留作 follow-up。
3. embed cache 正确性优先（get 双读 + 全锁内 IO）；吞吐优化（按 key 细粒度锁/内存 digest）留作 follow-up。
4. mask-only 启发式分数是排序代理（heuristic 显式标注），不是校准置信度。
5. stub text encoder 依赖 numpy Generator 流（semantic_version 已钉）；真实 encoder 经扩展平面接入。
6. UI 面板无 sidebar 导航入口（URL `/geoai` 直达）；避免改动共享导航文件。

## 资源/规模证据（PERFORMANCE.md / FAILURE_MATRIX.md）

内存界均有硬上限（256M px 先验、K≤4 候选、512px 预览、cache entries/bytes/单条目三上界、类别 512/top_k 16）；逻辑规模用纯函数 10k 窗网格 + 手算公式断言（无物化）；取消沿既有 checkpoint 纪律（新路径逐窗/逐批 checkpoint，预取消 typed）。FAILURE_MATRIX 覆盖全部 typed 拒绝路径。

## Planning artifacts

`.agent-work/geoai-promptable-foundation-platform-11/`：GOAL/BASELINE/PARALLEL_OWNERSHIP/CURRENT_ARCHITECTURE/CAPABILITY_MATRIX/PLAN/DECISIONS/EVIDENCE/REVIEW_LOG/TEST_MATRIX/PERFORMANCE/FAILURE_MATRIX/SCHEMA + `.goal-loop-ledger.md`（21 轮）。

**Local evidence only; no online CI dependency. 不 merge。**
