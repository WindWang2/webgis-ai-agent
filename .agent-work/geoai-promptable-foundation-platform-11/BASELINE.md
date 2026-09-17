# BASELINE — 启动审计原始摘要（2026-09-15，Phase 0）

本文件记录 GOAL 快照刷新后的**启动时事实**。GOAL 内嵌快照（`ebcafb4d`、
PR #991/#992）已全部过时，以下列事实为准。

## 1. Git 事实（主仓库执行 `git fetch origin --prune` 后）

- `origin/master` = **`faa453a8935101378c23eb6694a42c3616d9c670`**
  - `faa453a8` feat(harness): Hot-path Convergence — Mission × SkillPolicy × Evidence (Direction 04) (#1329)
  - `b44c1c9b` feat(harness): Spatial Evidence / Claim / Provenance Graph (Direction 03) (#1328)
  - `14a47cc6` feat(harness): Production GIS Skill Policy & self-evolving procedure runtime (Direction 02) (#1327)
  - `e21314a5` fix(mission/cartography): fail-closed durability for #1322–#1325 (#1326)
  - `d4480ca6` feat(harness): cartography feedback evaluation (#1321)
  - `87829572` feat(harness): Durable GIS Mission Runtime & Distributed Harness Control Plane (Direction 01) (#1320)
  - `c8c7a902` fix(audit2): address issues #1304–#1309 (#1319)
  - `7f658c95` fix(storymap): adversarial-review hardening (#1318)
  - 之下是 19-PR merge train（#1294–#1298 dependabot、#1317 counterfactual 等）与更早 master。
- Remote branches（prune 后仅 3 个）：
  - `origin/fix/harness-claim-mission-failclosed`（= PR #1335 head）
  - `origin/HEAD -> origin/master`
  - `origin/master`
- 本地 worktrees：
  - `webgis-ai-agent`（主检出，branch `fix/storymap-review-hardening`，他人/前序 track，**只读**）
  - `.worktrees/stale-code-cleanup`（branch `codex/stale-code-cleanup`，本地未推送；78 files，以删除 stale code 为主）
- fetch 期间 `origin/fix/storymap-review-hardening` 被远端删除（本地主检出的分支仍在）。

## 2. Open PR（gh pr list --state open --limit 100）

仅 **1 个**：

- **PR #1335** `fix(harness): fail-closed claim verify, tenant scope, mission ownership (#1330–#1334)`
  - head `fix/harness-claim-mission-failclosed` → base `master`；MERGEABLE，非 draft；
  - head 提交 `92145507`/`81b40a1f`/`a22a0c24`/`5b97b281`（2026-09-15T13:01Z）；
  - changed files（11，**对本 track read-only**）：
    - `app/services/gis_harness/completion/pipeline.py`
    - `app/services/gis_harness/evidence_claim/census.py`
    - `app/services/gis_harness/evidence_claim/grounding.py`
    - `app/services/gis_harness/evidence_claim/verify.py`
    - `app/services/gis_harness/hotpath_convergence/claim_ingest.py`
    - `app/services/gis_harness/hotpath_convergence/pi_card.py`
    - `app/services/gis_harness/hotpath_convergence/session_ctx.py`
    - `app/services/mission_runtime/service.py`
    - `app/services/mission_runtime/store.py`
    - `tests/unit/gis_harness/test_evidence_claim_graph_v1.py`
    - `tests/unit/gis_harness/test_hotpath_convergence_v1.py`
  - 其 test plan：`pytest tests/unit/gis_harness/... tests/unit/mission_runtime/ -o addopts=` → 79 passed。

## 3. Open issues（gh issue list --state open --limit 200）

5 个，全部是 audit2-harness 安全/正确性发现，**全部由 PR #1335 修复**（PR body 明确 Fixes #1330–#1334）：

- #1330 [P0] evidence_claim verify invents SUPPORTED（fail-open）
- #1331 [P1] ClaimStore keyed by session only（tenant 隔离）
- #1332 [P1] GIS_MISSION_RUNTIME kill-switch ignores off/no；swarm run 无 ownership 检查
- #1333 [P2] claim_ingest invents ClaimType.DENSITY
- #1334 [P3] hotpath 测试断言不足

**Dedupe 结论**：以上归 PR #1335 所有；本 track 不实施、不重复、不跨界修复。

## 4. 旧 backlog 核验（只读）

- `ISSUES.md`：master 与工作树均**不存在**（已被移除）——无 backlog 可误实施。
- `docs/agents/goal-template.md`：master 上 `docs/agents/` 仅含 dispatch-protocol/domain/issue-tracker/triage-labels 四文件，**不存在**——跳过并记录。
- `CHANGELOG.md` [Unreleased] 2026-09-13：adaptive-data-supply/v1（DS2–DS9，ADR-0172~0179）、visual-self-healing mapspec（ADR-0186）等——与 modelops 无 ownership 交集。
- ADR 目录最新编号 **0196**（autonomous-storymap-orchestrator）；本 track 新 ADR 从 **0197** 起。

## 5. Repo 实际形态（与 GOAL 模板 C++ 假设的差异 → rescope）

本 repo 是 **Python/FastAPI + Next.js 前端**的 webgis-ai-agent，不是 C++/Qt/CMake 仓库：

- 无 `CMakePresets.json`/`src/operators`/QT。等价约束映射：build 并行限制→不适用（无编译）；测试 targeted-first、`-j1`（pytest 顺序）、scale 测试 env opt-in 照常执行。
- "Model Platform 10.0" 的真身 = **ModelOps V2/V3**（ADR-0119，`.agent-work/spatial-modelops-v2/` 规划、多轮 hardening 已合入 master）：
  - `app/lib/modelops/`（纯契约层，~4.0k 行）：errors/descriptor/capabilities/resources/compatibility/planning/preprocess/stitching/fingerprint/**promptable**/temporal/package_security/evaluation/metrics/**foundation**；
  - `app/services/modelops/`（运行时层）：engine(1969 行)/service/registry/seeds/providers（onnx/torch/subprocess/remote/extension/**promptable_reference**/tiny_* /mock_gpu 等 17 个）/loaded_cache/reuse/scheduling/manifest/lineage/artifacts/geo_output/layer_delivery/package_store/config；
  - 能力词汇：`app/lib/gis/capabilities/modelops.py`（model_* 家族，ADR-0137）；
  - 测试：`tests/unit/modelops/` + `tests/integration/modelops/`（含 v3 foundation/provider/task_types/memmap_determinism、slice_a_segmentation 等）。
- 前端：Next.js（`frontend/app`、`frontend/components`、playwright e2e）。本地 codex 分支改动过 `frontend/components/map/*` 与 `gis_harness`——本 track UI 避开这些文件。
- Python 环境：Python 3.13.9，`.venv`（主 repo），pytest 8.4.2，numpy 2.4.4，pydantic 2.12.4。pytest.ini：`testpaths=tests`，addopts 含 `--cov=app`（targeted 运行用 `-o addopts=` 关闭，沿用 PR #1335 模式）。

## 6. GOAL 快照 vs 事实 对照

| GOAL 快照 | 启动时事实 |
|---|---|
| origin/master = `ebcafb4d` | `faa453a8`（快照后合入 D18+/harness Directions 01–04/#1318–#1329 等） |
| open PR #991/#992 | 均不在 open 集（已合并/关闭）；唯一 open PR = #1335 |
| 无 open issues | #1330–#1334（归 PR #1335） |
| ISSUES.md 为旧 D3 backlog | 文件已不存在 |
| TensorRT/OpenVINO provider | 本 repo 形态为 onnx/torch/subprocess/remote/extension provider |
| `src/operators/runtime` 等 C++ scope | rescope 到 `app/lib/modelops`、`app/services/modelops`、agent tools、frontend geoai panel |

## 7. Worktree 创建记录

- 命令：`git worktree add ../exp-rs-geoai-promptable-foundation-platform-11 -b zcode/geoai-promptable-foundation-platform-11 origin/master`
- HEAD = `faa453a8935101378c23eb6694a42c3616d9c670`（= 启动基线 SHA）
- `.agent-work/geoai-promptable-foundation-platform-11/` 经 `git check-ignore -v` 验证可跟踪（exit 1 = 不被忽略）；`.planning/` 在 `.gitignore:220` 被忽略，故按 repo 惯例使用 `.agent-work/`。
