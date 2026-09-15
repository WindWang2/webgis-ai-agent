# EVIDENCE — 验证与审计证据账本（倒序追加）

## 2026-09-15 · Phase 0 启动审计

- `git fetch origin --prune` → OK；`origin/master` = `faa453a8935101378c23eb6694a42c3616d9c670`（GOAL 快照 `ebcafb4d` 过时）。
- `gh pr list --state open` → 仅 #1335（清单见 BASELINE §2）。#991/#992 不存在。
- `gh issue list` → #1330–#1334，全部归 #1335（dedupe 记录在 PARALLEL_OWNERSHIP §4）。
- `gh pr view 1335` / `gh pr diff 1335 --name-only` → 11 文件 read-only 集。
- ISSUES.md / docs/agents/goal-template.md → master 上不存在（absent，记录）。
- `git check-ignore -v .agent-work/geoai-promptable-foundation-platform-11/GOAL.md` → exit 1（可跟踪）。
- `git worktree add ../exp-rs-geoai-promptable-foundation-platform-11 -b zcode/geoai-promptable-foundation-platform-11 origin/master` → HEAD=faa453a8。
- codex/stale-code-cleanup（本地）与 modelops 交集 = ∅；触碰 `frontend/components/map/**` → 本 track UI 避开。
- 环境自检：Python 3.13.9 / pytest 8.4.2 / numpy 2.4.4 / pydantic 2.12.4（主 repo .venv）。
- Skills：goal-loop 已加载（启动时）。其余按相位加载（DECISIONS D-006）。

## OUT_OF_SCOPE 登记

-（暂无；执行中发现即登记于此并在 PR_BODY 顶部标注 P0 项。）

## 2026-09-15 · Phase 0 收口

- baseline：`tests/unit/modelops -o addopts= -q` → **138 passed, 7 skipped, 9.06s**（主 repo .venv Python 3.13.9）。
- Phase 0 commit：`f57bbe78`（planning docs 10 文件）。
- EOL 注意：本 checkout autocrlf 会 LF→CRLF（与 master 017d1d41 的 EOL-invariant fingerprint 教训一致）——新增生成物/sidecar 一律显式 encoding + bytes 口径，不依赖 shell 文本管道。

## 2026-09-15 · Phase 1（WP-A 契约 + engine 集成）

- 新增：`app/lib/modelops/geo_prompt.py`（artifact 契约/编译/审计）、`errors.PromptArtifactError`、`service.compile_geo_prompt`、ADR-0198、SCHEMA.md。
- 修改：`PromptSpec.anchor_box`（指纹条件字段）、`foundation.prompt_span/prompt_windows`（anchor + 网格分窗）、`engine`（artifact 身份/审计/先验 digest/目标绑定门）、`manifest.prompt_audit` 节、`promptable_reference`（payload 直消费 + mask-only 种子语义）。
- **顺带修复的既有缺陷**（被新路径暴露，同一 diff）：
  1. [P1] `foundation.georeference_polygon` 系数序错（GDAL↔shapely）——promptable GeoJSON 对非平凡仿射系统性错位（既有测试只验栅格产物 transform，未覆盖 GeoJSON 坐标）；
  2. mask-only prompt 不可达（provider `from_payload` 重建丢先验 → 崩溃）；
  3. 先验内容不进复用键（count-only → 同数不同内容错结果复用）；
  4. sidecar/reference `(1,H,W)` 未归一化（破坏网格校验/窗口切片）。
- 验证：`tests/unit/modelops/test_geo_prompt.py` 33 passed；`tests/integration/modelops/test_geo_prompt_platform.py` 6 passed；全量 `tests/unit/modelops + tests/integration/modelops` **270 passed, 9 skipped**；ruff clean。

## 2026-09-15 · Phase 2a（WP-D embedding cache）

- 新增：`app/services/modelops/embedding_cache.py`（build_embed_cache_key + EmbeddingCache）、engine `_embedding_batch_via_cache`（逐窗命中跳过读取+推理、miss 子批推理+回填、OOM 逐张降级）、service `embedding_cache_stats`/`invalidate_embedding_cache`、PerfCounters `embed_cache_hits/misses`、config 三旋钮（MODELOPS_EMBED_CACHE_*，.env.example + tests/conftest.py _ENV_BASELINE parity）。
- 纪律：sidecar=提交标记（半写不可见）；get 时 .npy 重流式 sha256（篡改→驱逐+miss）；entries/bytes 双上界 LRU；单条目上限；tmp+os.replace 原子（失败无残件）；owner 隔离（白名单 scope）；确定性门（seed policy）；禁用路径（entries=0）零行为差异。
- 验证：unit 14 passed（往返/键全轴敏感性/digest 篡改/LRU/字节界/单条目界/失效/无残件/owner/重启扫描/提交标记/并发/stats 形状）；integration 4 passed（resume 零推理+产物逐字段一致、资产失效重算、参数门、禁用）；全量 288 passed 9 skipped。

## 2026-09-15 · Phase 2b（WP-C 多 mask 候选）

- 新增：`app/lib/modelops/candidates.py`（MaskCandidate/MaskCandidateSet/candidate_set_from_arrays；封闭选择词表 best|index；K≤4；分数∈[0,1] 有限；来源 model|heuristic）。
- TileOutput：`mask_candidates/candidate_scores/candidate_sources` 字段 + validate_for 契约分支（形状/数量/分数界/来源界；非 promptable 任务携带候选 = ProviderError）。
- ProviderCapabilities：`mask_candidates` 能力位（as_dict 同步；promptable_reference 声明 True，semantic_version → 1.1.0）。
- promptable_reference：确定性 3 候选（tight=tight 容差同语义 / relaxed=1.6× / box-fit=prompt 包围盒∩tight）+ 启发式分数（点命中×紧凑度、框 IoU 平均；argmax 平分取小）；非候选路径字节级不变。
- engine：能力门（PROMPT_CANDIDATES typed 拒绝）+ 选择词表校验 + best|index 裁决（engine 权威）+ 全候选 GeoJSON 发布（分数/来源/窗口）+ 逐窗裁决摘要 + 指纹条件字段（候选参数影响输出语义）。
- 验证：unit 5 函数 10 用例 + integration 5 用例（发布完整性/裁决几何一致/typed 拒绝两路/默认路径兼容）；全量 298 passed 9 skipped；ruff clean。

## 2026-09-15 · Phase 4b/4c（WP-G UI + WP-F 收尾 + 词汇/文档）

- 后端：service.run_prompt_refine（refine 单一实现，tool 委托）；路由补 prompt-refine / preview（≤512px 分位拉伸 PNG）/ artifact-geojson（DATA_DIR 门）。
- 前端：`frontend/components/geoai/`（geo-prompt-math 纯函数 + GeoAiPanel：SVG 覆盖层提示绘制/候选分色/接受精化/撤销/批次队列）+ `/geoai` 路由 + geoai 词包（zh-CN/en-US）+ messages.ts namespace 登记（ADR-0144 键化纪律；no-raw-cjk 守卫通过）。
- 能力词汇 append-only：`model_promptable_segmentation`（第 9 个 model_* id）。
- 文档：docs/geoai/prompt-artifact.md（用户/agent 契约）+ CHANGELOG 条目 + PERFORMANCE.md + FAILURE_MATRIX.md。
- 验证：geoai+routes 后端 14 passed；模型能力相关 14 passed 2 skipped；前端 geoai 6 passed、i18n 套件 25 passed、**前端全量 3709/3709 passed**；eslint 新文件 clean。
- 已知：chat-tab.render-scope 全量负载下偶发超时（单跑通过；与本 diff 无关，登记为 pre-existing flaky）。

## 2026-09-15 · Phase 7（独立 adversarial review + remediation）

- 3 个只读 reviewer（架构/数值/并发安全）× 全 diff：P0×1、P1×7、P2×13、P3×16 → 全部 disposition（详见 REVIEW_LOG.md）。
- 关键修复：ROI embedding cache（P0）；cache 同键双写自毁；text/labels 指纹；polygon+sidecar 互斥；artifact 引用路径门（HTTP）；/models 无 session 500；refine 事件循环阻塞；前端 null-CRS 翻转；capability↔algorithm parity（修既有 gis_harness 失败）。
- **回归范围教训**：此前"全量"仅 modelops 两目录——parity 破坏只能被 `tests/unit/gis_harness` 捕获。Phase 8 起将 gis_harness + 前端全量纳入收口 gate。
- 验证：modelops 341 passed；gis_harness capability/composition 59 passed；前端 geoai 7 passed；ruff clean。
- 既有 master 测试更新 1 处（capability_graph_v8，随 ADR-0198 词表拆分显式改断言——非跳过/删除）。

## 2026-09-15 · Phase 8（最终双验证 + 卫生）

- 双跑（原样连续两次，结果一致）：modelops 341 passed/10 skipped ×2；capability parity 59 passed ×2；前端 geoai+i18n 32 passed ×2。
- 前端全量（remediation 后）：3709 passed / 1 failed——唯一失败为既有 flaky `components/sidebar/chat-tab.render-scope.test.tsx::streaming N token batches...`（单跑通过；diff 与该文件/目录零交集，证据：`git diff --name-only` 无 chat/sidebar 路径）。
- 卫生：`git diff --check origin/master...HEAD` clean；冲突标记扫描 none；secret 扫描 clean；ruff（app+tests）clean。
- 既有 flaky 清单（pre-existing，与本 diff 无关）：`tests/unit/modelops/test_registry_cache_preprocess.py::test_preprocess_batch_memory_guard`（负载敏感）、`frontend chat-tab.render-scope`（全量负载超时）。
