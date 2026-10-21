# G05 PLAN — WebGIS Agent 任务级端到端验收、判错与回归基础

基线：origin/master = `ee8c14f840755bd47df45a107cd4ff08515ecfd2`（含已合并 #1758）
分支：`feat/g05-harness-task-acceptance-vnext`（worktree `../webgis-g05-task-acceptance`）
使命：覆盖「NL 目标→数据与方法→MapSpec/统计产品→交互→最终答复→导出」的**任务级判错**，错误归因到权威模块；不重造 replay oracle / unittest 颗粒度工具。

## 架构决策（详见 DECISIONS.md）

1. **层位**：G05 = task-level 投影/断言层。执行面复用 kernel e2e 模式（真实 `dispatch_tool` 缝 + `set_tool_registry`，无 LLM/Pi 子进程）；断言面复用权威纯函数（`evaluate_goal_satisfaction`、`build_product_manifest`/`verify_export_semantic_consistency`、RenderWork 投影、MapSpecStore 读 API）。
2. **NL→tool-call 步的处理**：生产中由 Pi LLM 决策，离线场景中由 ScenarioContract 内声明的 **scripted agent 行为**（dispatch 调用脚本）驱动；负例场景的"错行为"（选错数据集/错方法）即脚本注入，断言判错归因能抓住。此为诚实边界：NL 理解本身不在本 lane 断言范围（记 not_evaluated）。
3. **场景存放**：Python 生成器（`corpus.py`）+ 序列化 JSON pin（`fixtures/task_scenarios/`），仿 replay corpus 模式（生成器↔fixture 逐字节 pin 测试）。
4. **CLI**：`scripts/g05_task_acceptance.py`（run/compare/report，smoke/focused/heavy 三 profile），薄壳复用 runner 库；报告落 `reports/g05/`（gitignore 内）+ 覆盖热区图文档。
5. **共享文件**：不动 `tests/conftest.py` 的 `_ENV_BASELINE`、不加 pytest marker（复用 `cartography` marker）。

## 模块切分（全部纯新增，零 #1759/#1760 交叠）

```
tests/acceptance/gis_agent/
  __init__.py
  contracts.py        # ScenarioContract DSL（pydantic，版本化 task_scenario.v1）
  corpus.py           # 20+ 场景定义（12+ case family，正/负/多轮/恢复/注入）
  runner.py           # TaskScenarioRunner：驱动真实 kernel+dispatch 缝
  projections.py      # 链路证据投影：从 session/mapspec store 取各环产物
  attribution.py      # task 级 failure taxonomy + 归因（链→模块）
  reporting.py        # 结果模型 pass/fail/not_evaluated + first_failing_stage + 覆盖矩阵
  fixtures/task_scenarios/*.json   # pin 的场景语料
  test_contracts.py   # DSL/schema 契约 + pin 一致性
  test_corpus.py      # 场景库完整性（family 覆盖、负例可反转）
  test_task_e2e.py    # 正例端到端（真实 producer→dispatcher→MapSpec→导出证据）
  test_task_failures.py   # 负例/失败注入→红 + taxonomy 归因
  test_multiturn.py   # 多轮修改持久性/user-wins/revision 单调
  test_recovery.py    # 跨会话恢复 + 取消
scripts/g05_task_acceptance.py     # run/compare/report CLI
docs/quality/g05-task-acceptance.md
planning/g05/*
```

## Waves

- **W0**（本文件+RECON）：侦察、worktree、planning 骨架。
- **W1**：contracts.py + corpus 骨架 + runner 最小闭环（1 个场景真实跑通 dispatch→MapSpec）+ pin 测试。
- **W2**：12+ case family / 20+ 场景全量 + 导出/答复断言 + projections 完整化。
- **W3**：失败注入→红、expect-fail 反转测试、多轮、跨会话恢复、取消。
- **W4/C**：attribution taxonomy 完整化 + CLI run/compare/report + 覆盖热区图 + not_evaluated 统计口径。
- **W5**：独立 reviewer 两轮 review-fix-review。
- **W6**：docs/quality + PR body + `gh pr create`（不合并）。

## 停机条件（DoD 对照任务书 §5）

20+ 场景离线绿；≥1 多轮 + ≥1 跨会话恢复；科学/制图失败注入各自有红测试；CLI 三 profile 可跑且 smoke 常规开发机可承受；review 两轮无未解 P0/P1；planning/g05 文件齐备；PR 已创建未合并。
