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

- 新增：`app/lib/modelops/geo_prompt.py`（artifact 契约/编译/审计）、`errors.PromptArtifactError`、`service.compile_geo_prompt`、ADR-0197、SCHEMA.md。
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
