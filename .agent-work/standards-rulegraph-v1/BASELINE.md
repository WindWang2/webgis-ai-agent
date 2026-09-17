# BASELINE — cartography/standards-rulegraph-v1

- **执行时 origin/master SHA**: `faa453a8935101378c23eb6694a42c3616d9c670`（feat(harness): Hot-path Convergence — Mission × SkillPolicy × Evidence, #1329）
- **worktree**: `../webgis-wt-carto-rules-v1`；branch `cartography/standards-rulegraph-v1`（基于 origin/master，master 只读）
- **fetch 时间**: 2026-09-17（`git fetch --all --prune`，无新增 master commits；新增远程分支 `cartography/multiscale-scene-intelligence-v1`、`harness/event-driven-spatial-ops-v1`）
- **基线验证**: `python -m pytest tests/cartography/test_context_matrix.py -q --no-cov` → 6 passed（worktree 内，复用主 checkout venv `../webgis-ai-agent/.venv/Scripts/python.exe`）
- **环境注意**: `LLM_API_KEY` 为 placeholder（env 提示，非测试阻断）。

## Open PR 快照（Phase 0 时点）

| PR | branch | 主题 | 与本方向关系 |
|---|---|---|---|
| #1356 | cartography/multiscale-scene-intelligence-v1 | 2D/2.5D/3D 场景智能（ADR-0199） | **唯一 cartography 文件竞争者**（mapspec_schema.py、render_diagnostics.py、semantic-checks 契约 v2、tests/cartography/test_semantic_checks_contract.py） |
| #1355 | harness/event-driven-spatial-ops-v1 | 事件驱动空间操作控制面 | 无 cartography 重叠；动 tests/conftest.py、app/main.py |
| #1354 | rs/temporal-cube-sar-optical-v1 | 遥感时空立方体 | 无重叠 |
| #1353 | frontend/spatial-agent-ops-cockpit-v1 | Agent Ops 前端驾驶舱 | 无重叠；ledger 放 `.agent-work/<branch>/` 的先例 |
| #1352 | eval/gis-agent-benchmark-factory-v2 | 评测工厂 v2 | 相邻：`app/evaluation/cartography_axes_corpus.py`（评测轴，非规则引擎） |
| #1351 | data/spatial-quality-harmonization-v1 | 数据质量语义协调 | 相邻词汇（data_quality/semantic_checks.py），路径不相交 |
| #1335 | fix/harness-claim-mission-failclosed | claim/mission fail-closed（#1330–#1334） | 禁止重复修复 |
| #1336 | zcode/geoai-promptable-foundation-platform-11 | GeoAI 平台 | 热区禁入（modelops/geoai/embedding-cache/frontend） |

## Open issues 快照

#1330–#1334（claim fail-closed，已被 #1335 占用）；#1337–#1350（audit 跟踪项，均与本方向无直接交集；#1342 观测 except-pass 与本方向无文件交集）。
