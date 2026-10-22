# PR: feat(task-acceptance-lab) — G05 任务级端到端验收、判错与回归基础

> **PR URL**: https://github.com/WindWang2/webgis-ai-agent/pull/1762（已创建，未 merge）

- 分支：`feat/g05-harness-task-acceptance-vnext`（基线 `ee8c14f8`，含已合并 #1758）
- Worktree：`../webgis-g05-task-acceptance`（保留供回看）

## Why / Now

系统已有 Trace/Replay Oracle v3（管线级 141 场景）、#1755 的 33 个导出
conformance 场景（spec→verify 层）、kernel e2e 7 个最小生命周期场景——
但**任务级**判错（用户 NL 目标→数据与方法→MapSpec/产品→交互→完成判据
→导出）没有任何场景资产：无法回答「这个真实制图任务做对了没有、错在
哪一环、归因给谁」。本 PR 建立该层：ScenarioContract DSL + 21 个离线
场景（19 任务族）+ 九类 failure taxonomy 归因 + 本地 run/compare/report
CLI，全部经**真实生产缝**（dispatch_tool + harness kernel settle_turn）
执行，不引入 LLM/网络/Pi 子进程。

## 与其他 PR 的 overlap（确切不重复证据）

- **#1758**（已并入基线）：本分支直接基于其合并后 master 开发，未回退
  任何变更。
- **#1759 (G01) / #1760 (G02) / #1761 (G03)**：`gh pr view --json files`
  逐一核对，与本分支 **0 个语义文件交叠**；唯二共享接触：
  - `.gitignore`（#1761 也在追加白名单行）：双方均为纯追加，机械可合；
  - 无（tests/conftest.py、pytest.ini、pyproject.toml 均未触碰——目录级
    conftest 自治，复用既有 `cartography` marker）。
- **不重复既有资产**（差异层位）：replay oracle 断言管线决策面（不改其
  语义，只读引用）；h02 33 场景输入是 MapSpec JSON（导出 conformance），
  本分支场景输入是用户任务（导出环节复用同一判定函数
  `verify_export_semantic_consistency`，不复制场景）；kernel e2e S1–S7
  是单断言面最小验收，本分支是任务×多断言面×判错归因矩阵（模式复用）。

## 改动（25 文件，+6778，全部新增路径）

- `tests/acceptance/gis_agent/`：contracts（task_scenario.v1 DSL）/ corpus
  （21 场景·19 族·7 负例）/ runner（真实 dispatch 缝）/ projections
  （八面证据投影）/ reporting（三态判定）/ attribution（九类 taxonomy
  +infra_error）/ fixtures_data（模拟合成数据工厂）/ 4 个测试文件（45 项）
  / fixtures pin（逐字节一致性钉住）
- `scripts/g05_task_acceptance.py`：pin / run（smoke·focused·heavy）/
  compare（first-failing-stage + missing 检出）/ report --coverage
- `docs/quality/g05-task-acceptance.md`：运行手册 + 覆盖热区图 + 三真实发现
- `planning/g05/`：RECON/PLAN/TASKS/DECISIONS/TEST_MATRIX/
  PR_OVERLAP_MATRIX/REVIEW（两轮审查全记录）
- `.gitignore`：+`!/scripts/g05_task_acceptance.py` 白名单 + `/reports/`
  `/tmp/` 忽略（4 行追加）

## DoD 对照（任务书 §5）

| 要求 | 证据 |
|---|---|
| 20+ task-level 离线场景真实链路证据 | 21 场景经 dispatch_tool→kernel→settle_turn→MapSpecStore→manifest 全绿（pytest 45 项含 e2e 聚合） |
| ≥1 多轮交互 + ≥1 跨会话恢复 | f12（两轮编辑链·同 envelope）/ f19（requirement 多轮）；f13（中断→恢复→续绘，resume_count≥1）+ f14（取消） |
| 科学/地图错误各有失败注入→红 | 科学：f02b（样本量→热力资格）+ f04b（方法错配）；地图/数据：f18（错误数据集）；反转防线 TestBehaviorInversion（注入移除→负例必须翻红，实测） |
| pass/fail/not_evaluated 三态 + 诚实边界 | 三态评估器 + 边界单测；15 条显式 not_evaluated 期望（完成面诚实缺席钉扎） |
| 本地 CLI 可复跑/对照 master/定位 first-failing | smoke ~10s exit 0；双跑 diff=0；compare 给 NEW-FAIL first_failing_stage + MISSING |
| 不依赖外部 LLM/在线 CI | 全链离线（合成数据显式 simulated 标注） |
| 不重造 replay oracle / 不碰 #1758+#1759/#1760 面 | diff 无任何 app/ 生产文件与共享测试基建（两轮审查 G 项核验） |
| review 两轮无未解 P0/P1 | Round 1 FIX-FIRST（P1×3）→ 全清偿 → Round 2 **APPROVE**（blocker=0；残留 5 项文档级已当场清偿） |

## 测试结果（本地实测）

- `pytest tests/acceptance/gis_agent -m cartography --no-cov --timeout=300 -q`
  → **45 passed**（~26s；两轮审查各复跑一致）
- `ruff check tests/acceptance/ scripts/g05_task_acceptance.py` → clean
- `python scripts/g05_task_acceptance.py run --profile smoke` → 5/5 ok
  exit 0；`--profile heavy` → 21/21 ok（pass=71 / not_evaluated=15）exit 0
- 双跑确定性：run1 vs run2 compare → new_failures=0 fixed=0
- 守卫回归：`tests/test_ci_local_gate_contract.py` +
  `tests/test_pytest_module_name_uniqueness.py` → 19 passed
- 已知边界：全量后端 suite 有 #1753 存量挂起——本 PR 只跑定向 lane（与
  h01/h02 PR 同策略）；未跑项如实未声称。

## Performance

单场景 0.3–1.5s（settle 链为主）；smoke 5 场景 ~10s；heavy 21 场景 ~35s；
CLI 报告落 gitignored `reports/g05/`；栅格 fixture ≤10KB、矢量 ≤64 要素，
`./tmp/g05-rasters` 运行残留（已 gitignore）。

## 风险 / 回退

- 纯新增路径 + 4 行 .gitignore 追加；回退 = revert 整个 PR，无数据迁移、
  无生产行为变化（评测侧资产）。
- 场景期望钉扎了当前生产语义（含两处缺口：CRS 域校验、单位一致性——见
  docs §4 真实发现）；data-qualification 方向落地后 f09/f11/f18 的
  gap-disclosure 期望需随真实行为重校准（已在场景 note 标注）。
- `.gitignore` 与 #1761 可能有一处纯追加冲突（机械解决）。

## 后续扩展（不属本 PR）

- 接入 G01/G02/G03 并入后的能力（单位/科学契约 tripwire 场景）；
- f09/f18 的 CRS 域校验 tripwire 在 data-qualification 接线后转正；
- 场景 manifest 插件化扩展点（corpus register 装饰器即扩展缝）。
