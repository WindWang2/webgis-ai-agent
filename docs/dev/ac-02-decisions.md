# AC-02 决策日志（ADR-0151 执行期记录）

> 与 ADR-0151 互补：ADR 记「决策是什么」，本文件记「执行中遇到岔路时怎么
> 裁的、为什么」。全部为 §0.5 全自动默认决策，未停下来问人。

## D-01 环境：`pip install -e .` 不可用 → 仅装 requirements-dev.txt

- 现象：pyproject setuptools flat-layout 自动发现遇多顶层包报错。
- 裁决：`pytest.ini` 已有 `pythonpath = .`，测试从 rootdir 解析 `app`；
  相邻 worktree（ac-03/ac-04）同为仅装依赖。属存量仓问题，本线不修
  pyproject（超边界），在复核纪要记录。
- 顺带：清华镜像 403/阿里云镜像 rasterio 30MB 拖死 → 换官方 PyPI 装成。

## D-02 分支复用：本地已存在同名分支

- `adaptive-cartography/02-recipe-adjudication` 已存在且与 origin/master
  零差异（干净未使用指针），直接 `git worktree add` 复用，未重建。

## D-03 ADR-0151 编号照任务书采用

- docs/adr watermark 实为 0147；0148-0150 无 open PR 占用声明。按任务书
  预分配取 0151，并在 ADR 头部注明 0148-0150 为并行线预留。

## D-04 `<8` 样本硬下限不隐式全局生效（声明驱动）

- 首版实现把 `n<8` 作为隐式 recipe 级拒绝 → 7 点场景从「元素降级」变成
  「recipe 失格换案」，破坏 golden Case B 契约（点图回退）。
- 裁决：`check_sample_size` 仅在规则**声明 min_samples** 时产生硬性检查
  （<8 → SAMPLE_BELOW_FLOOR；8–声明值 → SAMPLE_INSUFFICIENT）；分档信息
  始终进 evidence。这同时满足「样本量分档进裁决依据」与「既有契约不劣化」。

## D-05 recipe 级失格 → 换案（完整重规划）而非禁层凑合

- 任务书 P3 要求「链式降级 → 仍失败则说明卡」。方案 B 的实现选择：
  按目标 recipe 走完整 `plan_from_intent + finalize_with_profile`（确定性、
  `_chain_depth=1` 封顶），而非在原计划上换图层——保证方案 B 的能力面/
  组件/模板证据自洽（P0 case05/06 的教训：局部修补产出矛盾计划）。
- 代价：换案改变 plan_id（query+recipe_id 派生）。发生在绑定前，
  tools.py 以 finalize 返回值为权威，无消费漂移。

## D-06 黄金 Case C 与格网几何失配用例按新契约更新

- 两用例旧断言锚定「层 note + 单条 fallback」旧形态；新行为是链式换案
  （更诚实的产品）。更新断言为「换案决策存在 + 真实原因码 + attempts +
  disclosure」，并保留原测试精神（面数据不产热力/格网层）。非弱化。

## D-07 `fact_signals` 局部变量遮蔽修复

- finalize 内插值块既有局部变量 `fact_signals` 把模块级新函数遮蔽成
  UnboundLocalError（被 try/except 吞掉、plan 字段恒空）。重命名局部变量
  为 `interp_signals` 并留注释。

## D-08 P7 批量补齐采用「声明显式化」而非依赖运行时默认

- 运行时已对无链 recipe 自动应用通用兜底；P7 把 64 条缺声明 recipe 显式
  写入 `fallback_links=auto_fallback()`（60 条 auto_generated）+ 4 条 seed
  领域链（categorical→poi、hotspot→point_density、proximity→poi、
  accessibility→proximity_analysis）。理由：覆盖率可审计（§5 门禁）、
  意图显式、悬空校验可对账、后续可按领域覆写。
- 与任务书预估偏差：pack 存量声明覆盖实测 61%（预估 ~0）——现有 100 条
  声明全是单元素非链式，缺口如实重述（见 recon §1）。

## D-09 `pytest -n 2` 不可用 → 串行跑全量

- pytest-xdist 不在 requirements-dev（仓内无并行跑法），里程碑全量改串行
  执行（本地即门禁，无 CI）。

## D-10 `scripts/` 为 gitignore allowlist → 为审计脚本加白名单条目

- `.gitignore` 按仓内既有惯例追加 `!/scripts/recipe_eligibility_audit.py`
  （任务书 §3 明确该脚本为交付杠杆）。

## 协调点（待对齐）

- `FallbackDecision.attempts/auto_generated`：01 线（adaptive-intent）统一
  契约前按本线定义实现（任务书 §8 授权）；01 合入时以此对齐。
- `EligibilityContext` 字段：接口本线定义，04 线（数据剖析）供给事实时
  按此对接；当前 `from_profile` 只派生既有 profile 键，新键
  （distribution/pointDensityPerKm2/temporal*）由 04 线后续填充。
- `fallback_llm`/`fallback_summary` 事件字段：07 线（layout/present）消费。

## R-01 §6.2 双轴 Review 的 findings 处置（Standards 8 + Spec 7）

### 修复（已落码）

| finding | 处置 |
|---|---|
| 悬空校验未接启动失败（Spec-c，§5 门禁） | `load_builtins()` 构建后校验 `fallback_links`/`DEFAULT_FALLBACK_CHAIN`，悬空即 RuntimeError（get_recipe_registry 惰性单例每次重抛）——真正的启动闸；`validate_gis_library` 降为测试/工具面（有测试） |
| `screen_density` 契约字段缺失（Spec-a1） | `EligibilityContext.screen_density` + `from_profile(screenDensity)` 直通，04 线供给前 None 放行 |
| P5 白名单「只读消费」仅注释（Spec-a2） | `fact_signals` 真实 import `_HINT_OVERRIDABLE`/`_HINT_PROTECTED_TASKS`：证据摘要带 `protected_task`，冲突披露逐条标注（intent.py 零改动） |
| 缺失率双重记录（Std-重复） | 基数检查已以 `FIELD_MISSING_RATIO_HIGH` 拒绝时跳过独立缺失率检查 |
| `DEFAULT_FALLBACK_CHAIN` 校验在逐 recipe 循环内 N 次重复（Std-重复） | 外提为全库一次对账 |
| kit 目标元组三处重复（Std-重复） | `auto_fallback(targets=DEFAULT_FALLBACK_CHAIN)` 单一来源；seed 内联字面量保留（声明式知识库风格） |
| `FallbackLink.evidence_hint` 从未被读（Std-投机通用性；spec schema 要求该字段） | 接线：匹配 link 的 hint 转录进 `FallbackAttempt.evidence`（有测试） |

### 反驳（写入 PR 供裁决）

| finding | 反驳 |
|---|---|
| 排序键仲裁简化为 (priority,id)（Spec-c1） | 11 层键的 1-9 层是 intent 依赖的路由层，在 origin recipe 选择时已生效；链求解按任务书是 intent 无关的纯函数，重复实现路由层才是第二事实源。ADR-0151 D2 已注明 |
| P7 分批节奏未在历史体现（Spec-a3） | 16 模块按批脚本化注入、每批即时 registry 计数审计 + ruff，最后统一跑 pack 套件；声明是纯数据 additive，风险面等价，单一 commit 保证原子性 |
| P0 探针未入库（Spec-a4） | 复现集已超集化为参数化回归（`test_recipe_downgrade_regression.py` 前 14 例即 P0 场景）；探针脚本留在 .agent-work 不入库 |
| workflow-catalog.md 越界（Spec-b1） | 生成物；recipe 指纹变化使 `test_catalog_matches_registry`（R1-B6 闸）必红，重新生成是仓内既定流程 |
| tools.py 不在 §8 可改清单（Spec-b2） | P4「只改后端事件字段」的唯一后端事件面就是 webgis_map_product 输出（tools.py）；diff 最小化且 guidance 文案改进属降级可解释范围 |
| `element:dimension` 字符串编解码（Std-Primitive Obsession） | 有界格式 + 测试锁定；独立类型化字段对会把 CheckResult 复杂化一倍，收益不成比例 |
| planner 参数束（Std-Data Clumps）/ stats 派生规则位置（Std-Divergent）/ P7 撒点（Std-Shotgun） | 与仓内既有风格一致；P7 撒点为台账 D-08 的显式决策 |

### 新增测试（评审修复伴随）

- `TestStartupDanglingGate::test_load_builtins_dangling_link_fails_startup`
- `TestContextScreenDensity`（passthrough/absent）
- `TestFactHintWiring::test_evidence_hint_propagates_to_attempt`
- `TestFactHintWiring::test_protected_task_marked_readonly`
