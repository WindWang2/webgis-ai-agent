# G05 DECISIONS

| # | 决策 | 理由 | 备选与为何不选 |
|---|---|---|---|
| D1 | G05 只做 task 级投影/断言层，不改 app/lib/harness/replay/ 语义 | 任务书红线"不重造 replay"；RECON §2.1 replay 已含管线级 141 场景 | 扩展 replayer 支持 NL 场景——会动共享语义、与维护者竞争 |
| D2 | 执行面用真实 dispatch 缝（set_tool_registry + dispatch_tool + kernel runtime），不用 HTTP 全栈 | 与 kernel e2e S1–S7 同模式（tests/test_harness_kernel_e2e_scenarios.py 实证可行）；无 Node/Pi 子进程依赖，离线确定 | HTTP TestClient 全链——需要 Pi 子进程/LLM，离线不可复现 |
| D3 | NL→tool-call 由 ScenarioContract 的 scripted agent 行为驱动；NL 理解本身记 not_evaluated | 生产中该步是 Pi LLM；离线无 LLM。诚实边界 + 判错归因仍覆盖"LLM 若选错数据/方法，下游判错面必须翻红"（负例脚本模拟错误选择） | fake LLM provider 全仿真——重造 Pi 子进程协议，越界 |
| D4 | 场景 = Python 生成器 + JSON pin（生成器↔fixture 一致性测试钉死） | replay corpus 同模式（test_replay_corpus.py:52-66），防手工漂移 | 纯 YAML 手写——易漂移、无规模保证 |
| D5 | 断言复用权威纯函数：evaluate_goal_satisfaction、build_product_manifest/verify_export_semantic_consistency、HarnessFailureClass、MapSpecStore 读 API | Single Source of Truth；这些已是生产判定面 | 自写第二套判定——任务书禁止 |
| D6 | 不加 pytest marker、不动 tests/conftest.py _ENV_BASELINE | RECON §6：这两个是唯二共享冲突面；复用 cartography marker 即可 | 新 marker acceptance——需动 pytest.ini，与 #1759/#1760 潜在 textual 冲突 |
| D7 | CLI 落 scripts/g05_task_acceptance.py，报告落 reports/g05/（不入库）+ docs/quality 手写文档 | scripts 通配已进 ruff 面（ci-local.sh:23-26）；reports/ 不污染 git | 报告入库——体积与 churn |
| D8 | task 级 failure taxonomy 独立定义（attribution.py），不修改 HarnessFailureClass | 工具执行面词汇 ≠ 任务级归因；只读引用 classify_harness_failure 做底层分类来源 | 扩展 HarnessFailureClass——动 #1760 邻接生产文件 |
| D9 | 多轮/跨会话场景基于 kernel S2/S5 模式扩展，session store 用真实持久化缝（tmp_path 落盘） | 跨会话恢复断言需要真实序列化往返 | 纯内存——测不出序列化丢失 |
| D10 | 不等待 #1759/#1760 合并；G05 场景只依赖 master 已有接口；对 G01/G02 语义对齐通过场景命名/注释表达，不 import 其分支代码 | 任务书 WP-D.15 | cherry-pick 并发分支——引入未评审依赖 |
