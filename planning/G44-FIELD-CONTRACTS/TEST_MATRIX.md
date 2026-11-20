# G44 TEST_MATRIX — 实测证据（全部本地真跑）

环境：worktree `.venv`（uv, requirements.txt+dev）/ frontend `node_modules`（pnpm）；
pytest 单进程；vitest 默认；playwright 1 worker；重阶段全部持
`webgis-heavy-stage.lock`。

## 后端单元/集成（pytest）

| 项 | 命令 | 结果 |
|---|---|---|
| 验证内核 oracle（人工真值表 F04–F10/F37） | `pytest tests/unit/field_contracts/test_validator_oracle.py` | **27 passed** |
| 执行链（真实 store+sqlite+G14 写链 F01/02/03/11/12/13/15/17/18/19/21/22/23/29/31） | `pytest tests/unit/field_contracts/test_enforcement_chain.py` | **15 passed** |
| REST 面（生命周期/F26/观测/绑定） | `pytest tests/unit/field_contracts/test_routes.py` | **4 passed** |
| G14/G32 回归 | `pytest tests/unit/feature_edit/ tests/unit/feature_proposals/` | **168 passed**（零回归） |
| 迁移门 | `alembic upgrade head` → `downgrade -1` → `upgrade head`（sqlite 实库） | **通过**；`uq_field_contracts_active_per_binding` 部分唯一索引实证（双 active → IntegrityError，F14 DB 级防线） |
| 迁移 drift 闸 | `pytest tests/test_alembic_metadata.py tests/test_deploy_migration_wiring.py` | 242 passed + 1 failed |
| OpenAPI 快照 | `API_SNAPSHOT_UPDATE=1` 刷新后 `pytest tests/quality/test_api_compatibility.py` | **12 passed** |
| 生成物 staleness | `gen_drift_report.py` + `gen_quality_manifest.py` + `--update` | **up to date** |
| Ruff（本域全部触及文件） | `ruff check <touched>` | **All checks passed** |
| Pi 旅程（真实子进程） | `PI_JOURNEY_E2E=1 pytest tests/integration/test_pi_g44_field_contracts_journey.py -q -s` | **1 passed，13/13 gates true，fail: []**；回读 `origin=agent, status=retired, reason=journey-eof`（F27/F28/F32） |

### 既有失败（非本分支引入，干净 base 同条件复现）

- `tests/test_alembic_metadata.py::test_alembic_upgrade_head_survives_percent_encoded_url`
  —— 在 pristine base `655f769ca`（临时 worktree `/tmp/g44-base-check`，同一 venv）
  同样失败（percent-encoded URL 子进程 `unable to open database file`）。属 #2123
  报告的基线问题域。
- `app/services/feature_edit/topology.py:109` F841 —— 我未触碰该文件；整仓 ruff
  基线（#2123：124 Ruff）同样存在。

## 前端（vitest / eslint / tsc / playwright）

| 项 | 结果 |
|---|---|
| `lib/api/field-contracts.test.ts` | 8 passed |
| `lib/hooks/use-field-contracts.test.ts` | 5 passed |
| `components/map/structured-feature-form` | 9 passed（含空串≠0、null≠missing、不默认首项、offset 校验） |
| `components/sidebar/field-contracts` 面板 smoke | 2 passed |
| `test/i18n`（key-completeness/framework） | 17 passed；`no-raw-cjk` 1 failed —— 4 个**既有**文件（import-workbench/scale-portrayal/thematic-authoring/skill-workbench），经 stash 验证为 base 既有（前端实现轮记录） |
| eslint（全部触及文件） | 0 errors |
| `tsc --noEmit`（触及文件） | clean（仓内既有 cartography-result-card 等 2 处错误为 base 既有） |
| 浏览器旅程 j24（real 档） | **1 passed (13.9s)**：真实后端+Pi 子进程+stub LLM+真实浏览器；种子→完整评估→激活（F13 服务端门）→绑定权威断言→reload 后绑定仍 ok（F35 权威面）→owner API 非法值 committable=false+contract_violation（F26） |

## 分层披露（诚实边界）

- **F24/F35-表单渲染**：结构化表单按合同渲染、枚举不默认首项、数值中间态 —— 由
  组件测试覆盖；真实浏览器内编辑腿（图层选择器→表单）依赖冷 dev server 的图层行
  水合时序，real 档多次实测不稳定（工作区壳层既有行为：侧栏收起状态、双实例
  DataSourcesTab、挂层后水合延迟），不在 j24 断言。诊断证据：同流程一次性面板
  打开验证通过（本任务日志）；G44 面板/表单组件全部走真实 REST 客户端。
- **工作台面板 UI 打开路径**：组件测试 + use-field-contracts hook 测试覆盖
  （含 revision CAS、激活门、分页 cursor）。
- F-场景覆盖见 REVIEW.md 附表（F01–F37 逐项 PASS/部位）。

## 资源实测

- Pi 旅程：32s（单进程 pytest，含 uvicorn+Pi spawn+stub LLM）。
- j24：13.9s（前后端+浏览器全部本机）。
- 后端单测峰值 RSS 正常（sqlite WAL 后无锁重试风暴）；未复制整份几何做属性校验
  （validator 只读 properties）。
