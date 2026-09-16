# BASELINE — collab/spatial-review-approval-v1

- 执行时间：2026-09-17
- `origin/master`：`faa453a8935101378c23eb6694a42c3616d9c670`（feat(harness): Hot-path Convergence — Mission × SkillPolicy × Evidence, #1329）
- worktree：`../webgis-wt-collab-review-v1`；分支：`collab/spatial-review-approval-v1`
- 主仓工作分支 `fix/storymap-review-hardening` 不受影响；master 只读。

## 启动时 open PR（`gh pr list`，2026-09-17）

| PR | 分支 | 主题 | 与本方向关系 |
| --- | --- | --- | --- |
| #1335 | fix/harness-claim-mission-failclosed | claim/tenant/mission fail-closed（占用 #1330–#1334） | 不重叠；不碰 claim verify |
| #1336 | zcode/geoai-promptable-foundation-platform-11 | GeoAI promptable platform（ADR-0198） | 热区禁入（modelops/geoai、embedding-cache、frontend/components/geoai） |
| #1351 | data/spatial-quality-harmonization-v1 | 数据质量/语义协调 | 并行方向 |
| #1352 | eval/gis-agent-benchmark-factory-v2 | 评测基准工厂 v2 | 并行方向 |
| #1353 | frontend/spatial-agent-ops-cockpit-v1 | 前端 ops cockpit | 前端并行方向；文件重叠需复查 |
| #1354 | rs/temporal-cube-sar-optical-v1 | 遥感 temporal cube | 并行方向 |
| #1355 | harness/event-driven-spatial-ops-v1 | 事件驱动空间操作控制面 | 需复查 mission/workflow 文件重叠 |
| #1356 | cartography/multiscale-scene-intelligence-v1 | 多尺度场景智能 | 并行方向 |
| #1357 | cartography/standards-rulegraph-v1 | 制图标准规则图 | 并行方向 |

**结论**：无任何 PR 已实现 proposal/review/approval 工作流；本方向无重复施工。

## Open issues

- #1330–#1334（claim/tenant/mission fail-closed）：由 #1335 占用，本分支不碰。
- #1337–#1350（audit 跟踪）：与本方向无直接交集。

## 近期 master 关键合入（本方向依赖面）

- #1327 Production GIS Skill Policy（Direction 02）
- #1328 Spatial Evidence / Claim / Provenance Graph（Direction 03）
- #1329 Hot-path Convergence：Mission × SkillPolicy × Evidence 接入 Pi/SessionPlan 默认链路（Direction 04）
- ADR-0183 mutation transactions（方向 8）：client_mutation_id 幂等、信封、producer_class
- ADR-0195 空间反幻觉守护网关（apply_mutation 锁前管道）

## 目标（Oracle）

在既有 CollabEventBus/WS 与 MapSpec mutation transaction 之上实现：

1. ReviewProposal（base revision / mutation intents / evidence / author / risk）
2. feature/layer/claim 锚定评论（anchor 失效 → stale 标记）
3. review 状态机 draft→submitted→changes_requested/approved/rejected/merged/superseded
4. approval policy（reviewer 数/role/risk/destructive/export；Agent 不得自批）
5. proposal rebase/conflict detection（base revision 漂移不静默覆盖）
6. merge = 现有 mutation intents 回放，经 CAS/guardrails/锁 + checkpoint/rollback
7. decision/merge 写结构化 audit evidence；导出不泄露 secret/CoT
8. 前端 review drawer（diff/评论/approve/request changes/stale）

## 非目标

- Collab transport 重写；通用 OA；绕过 MapSpec transaction；自动批准高风险操作；#1335 claim verify 修复。
