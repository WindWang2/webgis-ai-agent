# /goal — G02 · GeoAI Promptable Foundation Model Platform 11.0

> 本文件是启动本 track 的 GOAL 原文存档（2026-09-15）。执行时以
> `BASELINE.md` 记录的启动时事实为准（GOAL 内快照仅用于启动审计）。

/goal  target-agent=zcode  model=GLM-5.3  budget=400000000  subagents=unbounded  ci=none  autonomy=full  defaults=best

> **Mission:** 面向遥感/GIS的可提示基础模型与地理约束推理平台  
> **Target model:** GLM-5.3（强模型；适合跨层架构与高难科学/并发裁决）  
> **Branch:** `zcode/geoai-promptable-foundation-platform-11`  
> **Worktree:** `../exp-rs-geoai-promptable-foundation-platform-11`  
> **Terminal state:** 独立 PR 已创建；不 merge；不等待在线 CI。

## Prompt-generation snapshot（只用于启动审计，不是固定基线）

本 Prompt 生成时（2026-09-15）观测到：

- `origin/master` = `ebcafb4d02ec3522eaaa4b3b62b1082c36280ffb`。
- open PR #991 `grok/unified-mission-workbench-d18`：MissionContext / IR2 dock / workflow mounting；head `8dbd6bde1aa8b0538c2e7f74bed3ba776db35a1f`。
- open PR #992 `grok/dataset-foundry-benchmark-d19`：Dataset Foundry / Benchmark；head `08264801a079efff683cd2446a0acee4c2153448`。
- 当时无独立 open issues；`ISSUES.md` 是旧 D3 backlog，其中多数条目已被后续 10.0 PR 修复，**禁止把它当实时 backlog 直接实施**。
- 最近 master 已合入 D14 geometric、D15 classification/change、D16 temporal、D17 workflow，以及 Data Fabric / Verification / Spectral / Execution / EO Model / Workbench 等 10.0 平台能力。

**启动时必须完全刷新这些事实。任何 SHA/PR 状态发生变化，以启动时 GitHub/origin 事实为准。**

（注：启动审计确认上述快照全部过时——PR #991/#992 已不在 open 集内，
origin/master 已推进到 `faa453a8`；详见 BASELINE.md。）

## Why this track now

Model Platform 10.0 已有 manifest truth、classify/change/regress、TensorRT/OpenVINO 可选 provider 和 provenance，但仍缺“地理提示→模型→空间产品”的统一合约。下一阶段可建立点/框/掩膜/文本/参考图层等 prompt artifact、可提示分割 provider、embedding cache、地理坐标变换与空间输出质量控制。

本段只是生成 Prompt 时的方向判断。执行者必须以 Phase 0 重新读取的 master/PR/issues/reviews/code 为准；如果现状已经覆盖某个 package，不重复造轮子，而是在本领域内向下一个可验证缺口深化，并把 rescope 写入 `DECISIONS.md`。

## Phase 0 必做：在创建本 track worktree 前读取最新 master / PR / issue / review / branch

从主仓库（不是待创建 worktree）执行并把原始摘要写入 `BASELINE.md`（已完成，见该文件）。

建立 PARALLEL_OWNERSHIP.md：列出所有 open PR / remote branch 的 changed-file ownership、与本 track 的文件级交集、处理策略。规则：

1. 仍开放 PR 的 changed files 默认 **read-only**，不得复制/重做其功能；
2. 若本 track 依赖某 open PR 的新 API，优先依赖 master 已存在的稳定 seam；否则把相关 work package 改为 adapter/contract/test scaffold，并在 PR_BODY 标为 follow-up；
3. 若该 PR 已合并，立即以新的 `origin/master` 为事实源重新审计，不保留旧假设；
4. 新出现的并发 PR 同样适用；
5. open issue 必须逐条 dedupe，已被代码修复但 issue 未关的要记录证据，不重复实现。

**完成上述只读审计后才允许创建自己的独立 worktree。**（已遵守）

## Ownership / parallelism

**Primary write scope（启动审计后可收窄，不可无理由扩大）**：见
PARALLEL_OWNERSHIP.md 的 rescope 后版本（原模板中的 `src/**` 路径属于
C++ 形态仓库，本 repo 实际为 Python/Web——映射到 `app/lib/modelops/**`、
`app/services/modelops/**`、`app/agent/geoai/**`、`tests/**`、`docs/**`）。

并行开发原则：本 track 的业务主体必须落在上述 primary scope；对于共享 integration files 只做最小 append-only 接线。若发现另一个 open PR 已在相同业务主体开发，优先消费它在 master 上已有的稳定 seam、改成本 track 的互补能力；禁止“同功能换名字再写一遍”。

## Operating envelope（不可协商）

- 全自动：不向用户提澄清问题，不弹选项；有歧义时选择最保守、最兼容、最少重复实现的方案，并写入 `DECISIONS.md`。
- `master` 永远只读；所有编辑只发生在本 track 独立 worktree/branch。
- 不 merge 其他开发分支，不改别人的 worktree，不 force-push。
- 不等待、不重跑、不引用线上 CI 作为完成证据；PR 创建后即使自动触发 Actions 也不等待。所有 capability claim 只允许来自本地可复现 evidence。
- 测试默认 `QT_QPA_PLATFORM=offscreen`；先 targeted suite，再按必要性扩大；测试 `-j1`。（本 repo 无 Qt；等价约束 = 本地 pytest targeted-first）
- 不新增重量级依赖，除非现有技术栈确实无法完成且有明确跨平台/许可/离线方案；默认复用现有 Python/numpy/pydantic/rasterio/shapely 及既有 modelops runtime。
- 所有输出/缓存/sidecar/数据库写入必须考虑 atomicity、cancel、失败清理、Unicode path、read-only source、NoData/CRS/provenance。
- 不把 wall-clock benchmark 当 correctness gate；规模证据优先使用内存上限、操作数/队列上限、逻辑规模和可复现 invariant。

### Model / subagent policy

**Subagents：不设数量上限。** 可按架构、数值科学、并发/生命周期、测试可信度、UX/协议等拆分多个只读 reviewer；禁止 subagent 再嵌套 subagent，主 agent 对实现与最终裁决负责。

## Skills / method

启动时逐个确认存在再加载（不存在则跳过并记录，禁止伪称已用）：

- `.agents/skills/codebase-design/SKILL.md`：架构与边界；
- `.agents/skills/code-review/SKILL.md`：独立 review；
- `.agents/skills/diagnosing-bugs/SKILL.md`：失败根因；
- `.agents/skills/domain-modeling/SKILL.md`：authority / aggregate / schema；
- `.agents/skills/ask-matt/SKILL.md`：复杂 API/设计复核；
- `.agents/skills/implement-spec/SKILL.md` 或 `.agents/skills/implement/SKILL.md`：按 repo 实际存在情况选择（本 repo 无 implement-spec，用 implement）；
- 冲突发生时仅在存在后加载 `.agents/skills/resolving-merge-conflicts/SKILL.md`。

同时使用 `bravesd/goal-loop` 的 `skills/goal-loop/SKILL.md` 思路：

1. 在 worktree 根创建并持续维护 `.goal-loop-ledger.md`；
2. 每轮：读账本 → 找到离 Oracle 最近的真实差距 → 做一个可归因的聚焦改动 → 亲自运行验证 → 记账 → 判定；
3. 未满足 Oracle 禁止宣告 done；关键 Oracle 最终连续验证两遍；
4. 连续失败 3–4 轮必须列 3 个不同根因假设；5–7 轮换工具/层级；8+ 轮重新检查前提；
5. 只有缺外部权限/硬件等不可自动化条件才允许把某项标 `not-executed`，并继续完成其余可执行工作，不用它冒充通过。

## Mission definition

最终产品不是“写了一批代码”，而是：**面向遥感/GIS的可提示基础模型与地理约束推理平台**，并且其 authority、failure semantics、resource bounds、provenance、tests、docs、agent/CLI/GUI surface（适用时）相互一致。先证明已有能力和真实缺口，再实现；对所有科学公式/坐标/单位/时间/NoData 语义采用独立真值，不允许测试复用被测实现来制造绿灯。

## Work packages

| ID | Package | Required deliverables |
|---|---|---|
| A | GeoPrompt artifact 与坐标契约 | 定义版本化 prompt artifact：point/box/polyline/polygon/mask/text/reference-layer；明确 CRS、pixel/grid、time、band/model identity、provenance；所有转换可追踪且 fail-closed。 |
| B | Promptable provider seam | 在现有 Model Runtime provider 之上增加可提示分割/embedding provider interface；支持无 SDK 时 typed refusal；不捆绑不可再分发大模型权重。 |
| C | SAM-like geospatial adapter | 实现通用 SAM-like manifest/task adapter：encoder/decoder feed mapping、prompt batching、mask quality、多 mask candidate、nodata/ROI、tile seam、vectorization；具体权重由 catalog 外部提供。 |
| D | Embedding/feature cache | 按模型 fingerprint + asset identity + grid + window 建立有界 embedding cache，支持局部失效、resume、GPU/CPU memory ladder、磁盘 cache digest。 |
| E | 多模态与文本提示 | 建立 image/text embedding 可选 seam、类别原型/检索/zero-shot 语义映射的诚实能力边界；没有 provider 时拒绝，不伪装模型能力。 |
| F | GeoAI operators 与 Agent tools | 规划并实现 rs:prompt_segment / rs:geo_embedding / rs:prompt_refine 等最小稳定算子和只读 inspect/explain tools；schema 与 capability knowledge 同源。 |
| G | 独立 GeoAI UI surface | 新建独立 geoai 组件/面板：地图点框提示、候选掩膜预览、接受/撤销、批次队列；若 #991 仍开放，不修改其 main/workbench 文件，只通过现有扩展 seam 接入。 |
| H | 科学/性能验证 | 合成几何 known-answer、CRS/grid roundtrip、tile seam、cancel/OOM、100k prompt logical scale、provider absence、provenance replay；双重验证 Oracle。 |

每个 package 都必须包含：现状证据 → 设计选择（至少两个候选时写 DECISIONS）→ 最小 vertical slice → known-answer/negative test → failure/cancel/resource handling → 文档/contract/surface 同步 → commit。不要先堆几万行再统一测试。

## Token budget / execution phases

总预算 **400,000,000 tokens**（上限/规划包线，不是 KPI）。

| Phase | 内容 | Budget |
|---:|---|---:|
| 0 | 最新态审计、ownership、architecture map、GOAL/PLAN 落盘 | 24M |
| 1 | 基础契约/数据模型/authority 层 | 64M |
| 2 | 核心算法/执行能力第一大块 | 72M |
| 3 | 核心算法/执行能力第二大块 | 60M |
| 4 | surface/integration/compatibility | 52M |
| 5 | 规模、故障、并发、性能硬化 | 44M |
| 6 | E2E、known-answer、drift/生成物验证 | 36M |
| 7 | 独立 adversarial review + 全部 P0/P1 remediation | 28M |
| 8 | 最终双验证、rebase、PR evidence/提交 | 20M |

## Autonomy defaults

1. **格式/权威来源**：优先使用 master 已有 registry/schema/domain authority；禁止建立第二份真值数据库/第二 scheduler/第二 model catalog。  
2. **失败项**：先根因分析；环境缺失才标 not-executed。单测真实失败不得跳过或删 test 换绿。  
3. **命名/编号**：沿用现有 namespace/operator/error/ADR 规则；冲突时选最小 additive 命名并记录。  
4. **资源/超时**：单个 scale 测试必须可通过 env/label opt-in，日常 gate 用 bounded logical scale。  
5. **对外动作**：允许 `git fetch`、读取 GitHub PR/issue/review、push 自己分支、创建自己 PR；禁止 merge/close/修改他人 PR/issue，除非本 track 明确产生并拥有的新 issue（默认不创建 issue）。  
6. **范围外发现**：写 EVIDENCE `OUT_OF_SCOPE`；P0 同时写 PR_BODY 顶部；不要跨到其他并行 track 大修。  
7. **新依赖**：默认不用；优先现有 Python/numpy/pydantic/rasterio/shapely/optional provider seam，确保离线/Windows/Linux degradation。  
8. **并发冲突**：业务代码冲突优先 rebase + 重新审计；不得为了避免冲突复制一套实现。  
9. **文档与旧 backlog**：`ISSUES.md`/CHANGELOG/历史 GOAL 只做线索，所有缺口必须对当前 code 重新验证。

## GOAL Loop Oracle（未满足不得结束）

1. prompt artifact 在 map CRS↔pixel/grid 往返误差有明确容差并有 known-answer 测试
2. 不存在无 provider 时的假成功；所有 unsupported path typed-refuse
3. 同模型/输入/prompt identity 的结果可复现并携带完整 provenance fingerprint
4. 大图/大量 prompt 路径内存有界、取消有界、tile seam 有回归测试
5. 最终 model/agent/operator targeted suites 连续两次通过，独立 review P0/P1 清零
6. `git diff --check origin/master...HEAD` clean；无冲突标记/secret；所有新增生成物/manifest drift gate clean。  
7. Phase 8 完成后把关键 targeted validation **原样连续运行两遍**，两次都通过（或同一明确、与本 diff 无关的 pre-existing/host limitation 被对照证明）。  
8. 独立 review 完成：P0=0、P1=0；所有 finding 有 disposition；PR 已创建且未 merge。

`.goal-loop-ledger.md` 每轮格式：

```text
Round N | 当前 Oracle 差距 | 单一聚焦改动 | 验证命令 | 实际结果/exit | PASS/FAIL | 下一步
```

严禁用“看起来完成”“其余很简单”“CI 应该会过”作为结束条件。

## Required planning/evidence artifacts

除 repo `/goal` 模板要求外，本 track 至少维护：

- `CURRENT_ARCHITECTURE.md`：现状 authority/seam 图；
- `CAPABILITY_MATRIX.md`：before/after、implemented/not-supported/degraded；
- `PARALLEL_OWNERSHIP.md`：open PR/branch/file overlap；
- `TEST_MATRIX.md`：每项能力→独立 oracle→命令→exit→evidence；
- `PERFORMANCE.md`：资源模型、逻辑规模、实际 RSS/队列上限；
- `REVIEW_LOG.md`：reviewer/findings/disposition/commit/test；
- 必要时 `MIGRATION.md` / `SCHEMA.md` / `FAILURE_MATRIX.md`。

## Worktree / commit / rebase / PR runbook

1. 每个 Phase 至少一个原子 commit；每次 commit 后记录 `git status --porcelain` 和验证结果。
2. 每个 Phase commit 后：`git fetch origin && git rebase origin/master`。冲突时先判断是否并发 track ownership；不得用 ours/theirs 粗暴覆盖科学代码。
3. 对共享注册文件（registry、capability index、CHANGELOG）尽可能把修改推迟到独立 integration commit，并保持 append-only/minimal diff。
4. 完成实现后先由主 agent 全 diff review，再执行独立 reviewer；所有 P0/P1 修复后重跑 targeted gate。P2 能修则修，不能修必须逐条 disposition；P3 可记录。
5. 最后执行：`git diff --check origin/master...HEAD`、冲突标记扫描、secret 扫描、文件存在性/生成物 zero-diff 检查、targeted tests **连续两次**。
6. `git push -u origin zcode/geoai-promptable-foundation-platform-11`；禁止 force push。
7. 创建独立 PR（base master）。不要 merge，不等待线上 checks。
8. PR_BODY 必须写：baseline SHA、与当时 open PR 的 dedupe/ownership、架构决定、实际交付、兼容性、local tests、资源证据、review findings、known limitations、follow-ups、`Local evidence only; no online CI dependency`。

## Final review instructions

Review 必须从 `origin/master...HEAD` 完整 diff 开始，而不是只看最近 commit；至少覆盖：

- architecture/authority/duplication；
- science/math/CRS/units/time/NoData/provenance；
- concurrency/cancel/lifetime/atomicity/resource bounds；
- API/schema/backward compatibility/platform portability；
- test oracle independence、negative/failure/scale credibility；
- security/secret/path/remote/offline；
- UI lifecycle/accessibility（若有 UI）；
- docs/help/capability/contract drift。

修完 review finding 后必须再次 rebase `origin/master`，重跑与改动相关的 targeted gate 两遍，再创建/更新 PR。最终只报告实际完成与证据，不报告尚未执行的验证为“通过”。
