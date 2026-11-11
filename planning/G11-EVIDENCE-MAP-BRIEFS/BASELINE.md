# G11-EVIDENCE-MAP-BRIEFS — BASELINE（M0 启态审计）

日期：2026-11-09（UTC 2026-11-08T16:08 worktree 创建）
审计基线：origin/master = `8eb8aa188c1464ecb71bee79f40192e8045285b5`（与任务快照 SHA 一致）

## 1. 远端状态核查结论（gh 实测）

- **master**：`8eb8aa1`（= 任务参考快照；任务书"master 已不同于 585812ee"已核实——585812ee 早已被超越）
- **Open PR**：仅 1 个 — #1979 dependabot tailwindcss 4.3.4（与本任务无关，不触碰）
- **G01–G07（PR #2011–#2017）**：**全部已合并**（2026-11-08）。不存在"七个 PR 尚未合入"的旧状态。
- **G08 review #2018**、**#2019（post-dispatch lock）**、**#2020（pnpm lock overrides）**：均已合并。
- **G09/G10**：本地 worktree 存在（`webgis-wt-g09-workflow-batches-20261108T153411Z`、`webgis-wt-g10-project-data-refresh-20261108T153716Z`，均基于 8eb8aa1），**远端无 PR、未合并 → 属在途保护区，不依赖其接口、不 cherry-pick**。
- **Open Issues（9 个）**：#1999–#2007，全部为 G04/G05 audit 遗留 P1（矢量 PDF/出版约束/框选截断/换城旧图层等），不在 G11 主修改域，作风险线索保留（尤其 #2002 出版约束族锁、#2000 PDF 版面标记——简报静态图渲染若触及 publication 链需注意）。
- 结论：**G11 基于 8eb8aa1 开发，无合并冲突风险；与 G09/G10 通过当前 master 已存在接口协作。**

## 2. 已有基础（按任务 B 节逐项判定）

| 能力 | 状态 | 证据（文件:行） |
|---|---|---|
| StoryMapSpec 章节模型（章节/镜头/widget/音频） | **已有完整实现** | `app/lib/storymap/spec.py:16-240`（v1.1 additive，extra=allow） |
| trace→章节弧编译（GisTraceChain/ReplayTrace） | **已有完整实现** | `app/lib/storymap/story_compiler.py:419-513` |
| 离线 StoryBundle JSON/HTML（脱敏/转义/深度门） | **已有完整实现** | `app/lib/storymap/export_packager.py:93-244` |
| 会话 Story 资产保存/装载（revision 单调） | **已有完整实现** | `app/services/storymap/assets.py:23-84`；表 `story_map_assets`（mig 0099） |
| StoryMap REST 端点 | **已有完整实现** | `app/api/routes/storymap.py:36-142` |
| 报告分享 token（创建/过期/撤销/限流/公共投影） | **已有完整实现（复用）** | `app/api/routes/report.py:281-311,142-201,264-278`；`app/api/report_sharing.py:9-24` |
| evidence_claim 契约/ClaimStore/verify（positive-proof） | **已有完整实现（复用）** | `app/services/gis_harness/evidence_claim/{contracts,store,verify,census,claims}.py` |
| ArtifactRegistry（ref 即身份）+ revisions + pin | **已有完整实现（复用）** | `app/services/artifact_registry.py:88`；`app/services/artifact_revisions.py:384` |
| CAS BlobStore（sha256 内容寻址不可变） | **已有完整实现（复用）** | `app/services/durable_blob_store.py:128` |
| dataset 版本（content-addressed DAG/tag 不可变） | **已有完整实现** | `app/models/lakehouse_datasets.py:107,171` |
| project_runtime freeze/inspect/resume（publish 理由） | **已有完整实现（复用模式）** | `app/api/routes/project_runtime.py:75-267`；`app/services/project_runtime/{contract,freeze,store}.py` |
| MapSpec 存储（mutation_revision 乐观并发） | **已有完整实现** | `app/services/mapspec_store.py:95` |
| MapSpec→SVG 渲染（静态图件） | **已有完整实现（复用）** | `app/services/mapspec_to_svg.py:1030-1044` |
| publication ref 水合（离线导出内联先例） | **已有完整实现（复用）** | `app/services/publication_export.py:656` |
| Agent 工具注册链（ToolRegistry→Dispatch→Pi） | **已有完整实现（挂载点在）** | `app/tools/registry.py:475-523`；`app/tools/__init__.py:_TOOL_MODULES`；先例 `app/tools/report.py:104` |
| Story 前端查看/播放/导出/保存资产 | **已有完整实现** | `frontend/app/story/story-view.tsx`（959 行）；`frontend/lib/api/storymap.ts` |
| **块级证据绑定（数值/单位/口径/版本）** | **待开发** | 章节只有 `source_stage_ids` 阶段号；无 artifact/claim/dataset 版本绑定结构 |
| **简报草稿自动生成（含事实检查）** | **待开发**（trace 编译可作起点） | 需在 story_compiler 之上补 facts/blocks/证据可达性 |
| **简报编辑（章节 CRUD/正文/锁定/重排/重生成保留）** | **待开发，且已有但缺用户入口**（本地编排面板仅 `!specView` 模式可编辑，spec 模式只读） | `frontend/app/story/story-view.tsx:772` 门控 |
| **发布预检（preflight verdict）** | **待开发** | frontend/backend 均无 preflight 概念 |
| **不可变 release 冻结 + manifest** | **待开发**（有 publish freeze 先例可循） | — |
| **release 私有分享/撤销（简报域）** | **待开发**（report share 先例可平移） | — |
| **brief_* Agent 工具** | **待开发**（tools/ 下无 story/brief 工具） | — |

## 3. 共享接缝（最小集中修改点）

1. `app/main.py`：挂载新路由 `app/api/routes/brief.py`（1 行 include）
2. `app/tools/__init__.py:_TOOL_MODULES`：追加 1 条 brief 工具模块
3. `migrations/versions/0104_map_briefs.py`：新表（head = `0103_workflow_run_session`，单 head）
4. `frontend/lib/i18n/messages.ts`：若新增 namespace 需登记（优先复用 story namespace 则零改动）
5. `frontend/lib/api/`：新增 brief client 模块（纯前端增量，非共享接缝）

## 4. 受保护区（避让）

- G09 worktree（workflow batches）、G10 worktree（data refresh）分支：不读取其未合并代码、不依赖其接口
- `app/main.py` 其余路由注册、全局配置、OpenAPI 生成物：不改动
- 全局 `.goal-loop-ledger.md`（tracked）：**不写入**（并行任务冲突面），G11 账本归 `planning/G11-EVIDENCE-MAP-BRIEFS/LOOP_LEDGER.md`
- 主工作树（`webgis-ai-agent`，他人分支 `zcode/review-g3dv2-fixes`）：只读
- Issue #1999–#2007：不在本任务清偿范围

## 5. 未验证项（执行期持续更新）

- 本 worktree `.venv` 全量测试基线绿（环境搭建中，见 LOOP_LEDGER 第 1 轮）
- Pi v1.5.0 vendor 状态与 `pi_doctor`（vendor/pi submodule ` m` 脏标记在主工作树；本 worktree 需独立核验）
- 真实浏览器 e2e real 模式（需后端起服 + 测试账号；M7 执行）
