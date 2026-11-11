# G11-EVIDENCE-MAP-BRIEFS — OWNERSHIP（所有权与边界）

## 任务身份

- 分支：`zcode/goal-g11-evidence-map-briefs-20261108T160816Z`（基于 origin/master 8eb8aa1）
- Worktree：`/home/kevin/projects/webgis/webgis-wt-g11-evidence-map-briefs-20261108T160816Z`
- 账本：本目录（`planning/G11-EVIDENCE-MAP-BRIEFS/`）；不写全局 `.goal-loop-ledger.md`

## 主修改域（新增为主）

| 路径 | 性质 |
|---|---|
| `app/lib/storymap/brief_spec.py`、`brief_factcheck.py` | 新增：MapBriefSpec 数据模型 + 确定性事实检查 |
| `app/lib/storymap/brief_export.py` | 新增：简报离线 HTML/manifest/附件打包 |
| `app/services/storymap/brief_compiler.py` | 新增：trace/artifacts/claims → 简报草稿 |
| `app/services/storymap/brief_store.py` | 新增：brief/release 持久化（revision 乐观并发） |
| `app/api/routes/brief.py` | 新增：简报 REST（挂 main.py 一行） |
| `app/tools/brief_tools.py` | 新增：brief_* Agent 工具（挂 _TOOL_MODULES 一行） |
| `migrations/versions/0104_map_briefs.py` | 新增：map_briefs / map_brief_releases 表 |
| `frontend/lib/api/brief.ts`、`frontend/app/story/`、`frontend/components/story/` | 新增/局部修改：编辑器、预检、冻结、下载、证据侧栏 |
| `frontend/messages/{zh-CN,en-US}/story.json` | 新增文案 key（中英同步） |
| `frontend/e2e/journeys/j13-brief-*.journey.ts`、`frontend/e2e/fixtures/api-stubs.ts` | 新增 e2e + 桩 |
| `tests/unit/test_mapbrief_*.py`、`tests/test_brief_*.py` | 新增测试 |
| `docs/features/evidence-map-briefs.md`、`docs/adr/0288-*.md` | 新增文档 |

## 共享接缝（集中小提交，additive）

`app/main.py`（路由注册 1 行）、`app/tools/__init__.py`（1 条模块）、迁移 head 接续（0103→0104）。
既有签名/默认值/错误语义不改；既有 storymap 端点回归由既有测试守护。

## 只读复用（不修改）

`app/lib/storymap/{spec,story_compiler,export_packager,camera_planner}.py`（仅 import）；
`app/services/gis_harness/evidence_claim/*`（import）；`app/services/{artifact_registry,artifact_revisions,durable_blob_store}.py`（import）；
`app/services/project_runtime/*`（模式参考/按需 import）；`app/api/routes/report.py` 分享机制（模式平移）；
`app/services/mapspec_to_svg.py`（import）；Pi 宿主/规划器不重写。

## 明确不做

- 不建第二执行器/registry/MapSpec/session truth store/证据库
- 不新建 PDF 渲染器或报告服务（印刷 PDF 只经既有出版链，本轮只做 HTML+JSON+可选 CSV/SVG 附件）
- 不改 G04 出版约束链、不触碰 Issue #1999–#2007
- 不依赖 G09/G10 未合并接口；不整包 cherry-pick 任何在途分支
- 不自动升级 Pi vendor、不追 upstream main
- 不写全局 goal-loop ledger、不 stash/clean/reset 他人工作树

## 子 Agent 预算（全调用树 ≤3）

- A（已用）：后端审计（Explore，已完成）
- B（已用）：前端审计（Explore，已完成）
- C（预留）：独立冷读 Review（M8）
