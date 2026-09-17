# DECISIONS — 本 track 的裁决记录（倒序追加）

## D-001 planning 目录：`.agent-work/` 而非 `.planning/`

- **背景**：GOAL 模板要求 `.planning/geoai-promptable-foundation-platform-11/`，但 `.gitignore:220` 忽略 `.planning/`；repo 既有惯例（6+ 前序 track）是 `.agent-work/<track>/`（被跟踪）。
- **候选**：(a) 改 .gitignore 放行 `.planning/`；(b) 沿用 `.agent-work/geoai-promptable-foundation-platform-11/`。
- **裁决**：(b)。GOAL 自己规定"按 repo 现有模式/不要大改 .gitignore"，`.agent-work/` 是 repo 现有模式，且 `git check-ignore` 验证可跟踪。
- **影响**：GOAL.md 已存档于 `.agent-work/geoai-promptable-foundation-platform-11/GOAL.md`。

## D-002 rescope：Python/Web repo，非 C++/Qt/CMake

- **背景**：GOAL 模板的路径/构建约束（`src/operators`、CMakePresets、`CMAKE_BUILD_PARALLEL_LEVEL`、QT_QPA_PLATFORM）来自 C++ 形态仓库；本 repo 为 Python/FastAPI + Next.js。
- **裁决**：primary scope 映射到 `app/lib/modelops/**`、`app/services/modelops/**`、`app/agent/geoai/**`（新）、`frontend/components/geoai/**`（新）、tests/docs（详见 PARALLEL_OWNERSHIP §5）。构建并行约束不适用（无编译产物 gate）；测试约束保留（targeted-first、顺序执行、scale 测试 env opt-in）。"不新增重量级依赖"映射为：仅用 numpy/pydantic/rasterio/shapely 等已有栈。
- **依据**：GOAL 明示"执行者必须以 Phase 0 重新读取的 code 为准…把 rescope 写入 DECISIONS.md"。

## D-003 与 ModelOps V2/V3 的关系：深化，不重建

- **背景**：master 已有 ADR-0119 ModelOps 平台（descriptor/registry/providers/planner/stitching/fingerprint/promptable/foundation/evaluation + 多轮 hardening）。GOAL 的 B/C 包在现有平台上已大部分存在。
- **裁决**：本 track = **Model Platform 11.0**：在既有 seam 上补齐 GOAL 列出的真实缺口（详见 CURRENT_ARCHITECTURE.md 缺口清单与 PLAN.md），不复制 descriptor/registry/provider/planner/stitching/fingerprint 任何既有真值。新增命名挂靠既有命名族（`modelops.` 前缀 / `model_*` 能力词汇 / ADR-0198 起）。

## D-004 open issue/PR 处理

- PR #1335 的 11 文件 read-only；issues #1330–#1334 归 #1335，本 track 不实施（BASELINE.md §3）。

## D-005 Phase 0 审计文件落盘位置

- GOAL 字面要求"worktree 创建前"在主仓库写 BASELINE/PARALLEL_OWNERSHIP；实际执行为：审计数据先收集于会话（fetch/gh/git 输出已在上文），worktree 创建后立即落盘到 `.agent-work/<track>/`。主仓库工作树（他人 track 检出）未被写入任何文件。语义等价：worktree 创建前未做任何写动作。

## D-006 技能加载时序

- 启动时确认存在性（ask-matt/codebase-design/code-review/diagnosing-bugs/domain-modeling/implement/goal-loop 存在；implement-spec、docs/agents/goal-template.md 不存在→跳过并记录）。goal-loop 已在启动时加载；codebase-design/domain-modeling/implement 在进入设计/实现时加载；code-review/diagnosing-bugs/ask-matt 在 review/diagnosis 相位加载。每次实际加载记入 EVIDENCE.md，禁止伪称已用。

## D-007 ADR 编号冲突 → 本 track ADR 定为 0198

- **背景**：Phase 0 在主 repo 旧检出（storymap 分支基底）上 `ls docs/adr` 误判最新编号为 0196，初版用了 0197；worktree（faa453a8）上 master 已有 `0197-durable-gis-mission-runtime.md`（Direction 01 #1320）。
- **裁决**：本 track ADR 改号为 **0198**（`docs/adr/0198-geoai-promptable-foundation-platform-v11.md`），全部自有引用同步；mission_runtime 的 ADR-0197 引用不动。BASELINE §4 的"最新 ADR 0196"更正为"0197 已被 mission runtime 占用，本 track 从 0198 起"。
- **教训**：目录盘点必须在 worktree（= origin/master 事实源）上做，不在旧检出上做。
