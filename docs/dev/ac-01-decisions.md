# AC-01 决策日志（§0.5 全自动默认决策）

> 分支 `adaptive-cartography/01-adaptive-intent`；所有岔路按任务书 §0.5 默认执行，逐条留痕。

| # | 岔路 | 决策 | 理由 |
|---|---|---|---|
| D1 | worktree venv `pip install -e .` 失败（flat-layout 多顶层包被 setuptools 拒绝；任务书命令与仓库现实不符） | 改按 CI 方式 `pip install -r requirements-dev.txt`（`.github/workflows/production.yml` 同款） | app 从仓库根导入，无需 editable 安装；与 CI 一致 |
| D2 | 默认 pip 清华镜像对 setuptools 返回 403 | 换阿里云镜像 `mirrors.aliyun.com/pypi/simple` 完成安装 | 环境问题，不影响交付物 |
| D3 | §0.2 复核结论 | **(a) 无重叠 → 全量执行** | PR/issue/分支检索均无既有「意图语义槽位+澄清+校准置信度」实现；ADR-0121/0134/0137 消费 intent 但不定义解析，与本线正交；ADR-0150 未被占用（docs/adr 最大 0147，并行 02/03/04 分支未新增 ADR） |
| D4 | 300 条语料的模糊条目仅中文 10 条（任务书总量 300 不变） | 澄清策略语言无关性由 `test_intent_adaptive.py` 双语单测补充覆盖；台账注明 | 保持任务书规定的 300/180/120 配比 |
| D5 | 覆盖缺口语义归属（zh-122 / en-116「步行圈内没有任何公园」） | 带「步行/分钟/覆盖」距离框架 → accessibility_analysis；「不足/公平/underserved」剥夺框架 → spatial_equity | 与既有 #779 盲区/缺口守卫契约一致 |
| D6 | `resolve_map_request_intent` 是否内联调 LLM | **不调**：保持纯函数确定性（评估链 `evaluation/runner.py` 有复跑一致性锁）；LLM 双轨放新入口 `resolve_intent_adaptive`（可选注入） | 确定性是 intent.py 模块头声明的第一设计约束；LLM 不可用时全链路不抛错且零延迟回归 |
| D7 | P3 要求「权重进 settings」但 §8 可改清单未列 `app/core/config.py` | 按 P3 执行：config.py 纯加法新增 `INTENT_*` 字段 + 同步 `tests/conftest.py` `_ENV_BASELINE`（`test_env_hygiene` 锁要求） | 具体条款优先于枚举清单；纯加法对并行线零风险 |
| D8 | ScopeIntent.level 是否加「region」值 | **不加**；自然地理实体（青藏高原/岷江流域等）只进 IntentSlots.area 锚点，不进行政 scope | 避免改 Literal 影响下游穷举消费 |
| D9 | 主体词表合并后 category 的取值 | zh 沿用「命中的表面形式」（golden 兼容）；英文表面词映射到中文规范类目（语料期望口径） | planner/template_selector 对 category 仅做插值，两种取值都安全 |
| D10 | 任务规则冲突裁决 | 特异性分级评分（specificity, span, index 字典序），分级值经 300 条语料 + 既有 golden 校准 | 纯 first-match 无法处理「视域分析（雷达站选址点）」类跨级冲突 |
| D11 | LLM 结构化输出通道 | 复用 `chat/llm_client.call_llm`（无 JSON mode），按 `spatial_reasoning.py` 范式：prompt 内嵌 JSON schema + fence 剥离 + pydantic 校验失败降级 | 与仓库既有结构化抽取完全同构 |
| D12 | P8 登记：`build_default_components` 旧词汇兼容分支 | 属 02 线边界，本线仅在 PR 登记，不动 | 任务书 §2 P8 原文约定 |

## 语义抽取与规则冲突的证据优先级实现口径（§0.5 表第 2 行）

结构化数据事实（实体解析结果）> LLM 语义槽位 > 正则规则，落地为：

- task：规则引擎（特异性评分）为权威；仅当规则落入 fallback 且 LLM 给出
  合法 TaskType 时采信 LLM task（记录 `hint_applied` 同款审计痕迹）；
- subject/area/temporal/measure：规则与 LLM 取置信度高者，平手取规则；
- 实体解析（城市/行政区）：`local_first` 服务结果 > 本地快路径词表；
  服务不可用 → 词表 + `degraded_reason="entity_service_unavailable"`。
