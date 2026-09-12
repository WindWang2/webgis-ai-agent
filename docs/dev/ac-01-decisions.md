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
| D13 | 任务书要求 `pytest -n 2`，但 `pytest-xdist` 不在 `requirements-dev.txt` | 本地临时装入 venv 失败后改为**串行**全量（更保守的资源占用）；不改 requirements-dev.txt（避免影响并行线） | `-n 2` 是资源上限不是并行要求 |
| D14 | ruff 默认镜像安装 403 同 D2，阿里云镜像装入 venv（仅本地工具，不进依赖清单） | `ruff check <变更文件>` 0 告警取证 | 与 §0.4 纪律一致 |
| D15 | 一致性语料（conformance 20,088 例）回归：人口/企业类「统计字段」主体标成 raster 会把 entity_type/geometry 拉向栅格，下游 `admin_aggregation` capability 与 administrative_choropleth recipe 全线失配（1188 例） | **撤销**该投机覆盖：人口/企业不进实体词表（legacy 口径 subject=unknown，几何由任务派生），词表注释留档 | 「既有语料无劣化」是硬门禁，覆盖扩展让位于契约稳定 |
| D16 | golden 语义锁（benchmark 33 例）：G16/G17/C018 锁定「裸密度词=视觉分布概览」、M074 锁定「Show the DEM terrain」=simple_view、M083 锁定「遥感影像算 NDWI」=指数优先 | 密度补强规则收窄到显式单位锚点（每平方公里/单位按每/（人/平方公里）/per km²）；`zh_raster_domain` 降到 SPEC 27 三档夹缝（15 栅格主体 < 27 < 28 光谱指数 < 30 变化/分类）；`categorical_breakdown` 加 `(?<!土壤)` 守卫；`bare_density_visual`（SPEC 11）给裸密度查询规则头标记而非 fallback | golden 锁优先于语料命中：先改规则，仅当语料期望与 legacy 锁定语义真冲突时改语料（zh-049/en-029 两处，改为 distribution_overview） |
| D17 | en-029 类「裸密度地图」查询落 fallback 头标记会误触发「缺主体」澄清 | `bare_density_visual`（SPEC_SPECIFIC 面内 SPEC 11，仅压过兜底）使其获得 distribution_overview 规则头；澄清策略不因「头标记=非 fallback」而误伤真模糊查询（语料模糊组 100% 仍触发，最低置信 0.24） | 零静默 fallback 与零误触发澄清的交界面 |
| D18 | 校准锚点在规则调优后复拟合 | 实测最优分箱误差 0.038（锚点 `CALIBRATION_ANCHORS` 保持 (0,0.15)(0.43,0.67)(0.60,0.95)(0.70,0.97)(0.90,1.0)(1.0,1.0)），模糊条目最低置信 0.24 << 澄清阈 0.55 | 分箱表见 recon §7 回填 |

## 语义抽取与规则冲突的证据优先级实现口径（§0.5 表第 2 行）

结构化数据事实（实体解析结果）> LLM 语义槽位 > 正则规则，落地为：

- task：规则引擎（特异性评分）为权威；仅当规则落入 fallback 且 LLM 给出
  合法 TaskType 时采信 LLM task（记录 `hint_applied` 同款审计痕迹）；
- subject/area/temporal/measure：规则与 LLM 取置信度高者，平手取规则；
- 实体解析（城市/行政区）：`local_first` 服务结果 > 本地快路径词表；
  服务不可用 → 词表 + `degraded_reason="entity_service_unavailable"`。
