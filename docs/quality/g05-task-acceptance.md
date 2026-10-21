# G05 任务级验收（task-acceptance）运行手册与覆盖热区

> 范围：`tests/acceptance/gis_agent/`（ScenarioContract DSL + 21 场景语料 +
> 真实 dispatch 缝 runner）与 `scripts/g05_task_acceptance.py`（本地 CLI）。
> 基线：`ee8c14f8`（含 #1758）。本文件为手写语义文档（非生成物）。

## 1. 一分钟上手

```bash
# 场景 pin（corpus ↔ fixtures/task_scenarios/scenarios.json 逐字节一致）
python scripts/g05_task_acceptance.py pin

# 本地运行（真实 dispatch 缝 + settle_turn；无 LLM/网络/Pi 子进程）
python scripts/g05_task_acceptance.py run --profile smoke     # 5 场景，~15s
python scripts/g05_task_acceptance.py run --profile focused   # 非栅格全量
python scripts/g05_task_acceptance.py run --profile heavy     # 全量 21

# 回归对照（first-failing-stage 定位）
python scripts/g05_task_acceptance.py compare reports/g05/run-A.json reports/g05/run-B.json

# pytest 侧（进 cartography lane 的口径）
pytest tests/acceptance/gis_agent -m cartography --no-cov --timeout=120 -q
```

三态口径：每条检查判 `pass / fail / not_evaluated`；场景通过 = 每条检查的
判定与场景期望一致（负例的期望即『判错面必须翻红』）。

## 2. 覆盖热区图（场景 × 断言面）

图例：● = 该面有 pass 级期望；○ = 该面有 not_evaluated 级期望（诚实缺席
钉扎）；neg = 负例注入；gap = 缺口披露型（注入不改变判错面，钉扎系统
现状红线，见 §3/§4）。**本表由脚本从 corpus 逐期望生成**（生成口径：
`python -c "from tests.acceptance.gis_agent.corpus import all_scenarios; ..."`
按 face×expected_verdict 聚合），corpus 变更后须同步重生成。

| 场景 | family | intent | req | method | data | mapspec | rend/exp | final | neg | 多轮 | 恢复/取消 | 缺口披露 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| f01-school-points-map | F01 | ● | ● |  |  | ● | ● | ● |  |  |  |  |
| f02a-heatmap-method | F02 |  |  | ● |  | ● | ● | ○ |  |  |  |  |
| f02b-sparse-heatmap-degraded | F02 |  |  | ● |  | ● |  | ○ | ✓ |  |  |  |
| f03-admin-aggregation | F03 |  |  | ● |  | ● | ● | ○ |  |  |  |  |
| f04a-density-denominator | F04 |  |  | ● |  |  | ● | ○ |  |  |  |  |
| f04b-visual-for-quantitative | F04 |  |  | ● |  | ● |  | ○ | ✓ |  |  |  |
| f05-temporal-points | F05 | ● |  | ● |  |  | ● | ○ |  |  |  |  |
| f06-raster-ndvi | F06 | ● |  | ● |  |  |  | ○ |  |  |  |  |
| f07-spatial-join | F07 |  |  | ● | ● |  | ● | ○ |  |  |  |  |
| f08-no-coordinates | F08 |  |  | ● |  | ● |  | ○ | ✓ |  |  |  |
| f09-wrong-crs | F09 |  |  | ● |  |  |  | ○ | ✓ |  |  | gap |
| f10-nodata-raster | F10 |  |  | ● |  |  |  | ○ |  |  |  |  |
| f11-unit-conflict | F11 |  |  | ● |  |  |  | ○ | ✓ |  |  | gap |
| f12-multiturn-edit-chain | F12 |  |  |  |  | ● | ● | ○ |  | ✓ |  |  |
| f13-cross-session-recovery | F13 |  |  |  |  | ● | ● | ● |  | ✓ | ✓ |  |
| f14-user-cancel | F14 |  |  | ● |  | ● |  | ○ |  |  | ✓ |  |
| f15-tier3-governance | F15 | ● |  | ● |  |  |  | ○ | ✓ |  |  |  |
| f16-export-offline | F16 |  |  |  |  | ● | ● | ● |  |  |  |  |
| f17-render-work-envelope | F17 |  |  |  |  |  | ● |  |  |  |  |  |
| f18-wrong-dataset | F18 |  |  |  | ● |  |  | ● | ✓ |  |  |  |
| f19-requirement-multiturn | F19 | ● | ● |  |  | ● |  |  |  | ✓ |  |  |

machine-readable 版本：`python scripts/g05_task_acceptance.py report
<run.json> --coverage`（faces/turns/checks/ok/first_failing_stage）。

负例分层（诚实口径）：
- **注入敏感负例**（判错面随注入翻转，有反转测试防线）：f02b（样本量→
  热力资格）、f08（无坐标→无点位层）、f15（tier-3 治理，运行时守卫）；
- **缺口披露型**（注入在当前生产接线下不改变判错面，钉扎『不放行+
  现状行为』并声明缺口归属）：f04b、f09、f11、f18。

## 3. 离线诚实语义（为什么正例也期望 blocked）

实证（探查记录见 planning/g05/DECISIONS.md D11/D12）：

1. **矢量任务 recipe 恒含 mandatory `poi_query`（部分含
   `admin_boundary_query`）**，而这两个能力的注册工具全部
   `network=True`（在线 POI/行政区服务）或依赖本机导入数据 → 离线沙箱
   中无法产出带 ref 的完成证据 → 终验：
   - 行已被 product 谱系标记 available 的形态 → 落 terminal 块
     （BLOCKED_BY_DATA，task_complete=False）→ 场景钉扎 **不得假成功**；
   - 存在从未执行的 mandatory 行 → finalize 判 `pending`，完成块按生产
     语义**不持久化** → 场景钉扎完成面 **not_evaluated** + 
     `needs_execution` 行对计划可见。
2. **栅格任务**（raster_distribution）的 `raster_source`/`ndvi` 行同样
   仅网络工具（fetch_dem / compute_ndvi STAC），离线诚实降级；
   `raster_calculator`（离线）完成的是 `band_math` 行。

这两条不是场景缺陷，而是被 G05 钉扎的系统红线：**数据源缺席时任务
判据 fail-closed，已交付的部分产品仍可断言**。

## 4. 真实发现（本方向产出、如实披露）

| # | 发现 | 证据 | 处置 |
|---|---|---|---|
| G05-F1 | 错误 CRS 坐标（投影平面坐标谎称 WGS84）在**产品路径**只触发分析侧告警（`to_utm_gdf` polar warning），点位层仍被组装——CRS 域校验无产品级接线 | f18 场景实测：layer_types=['circle']；任务级判据不放大（blocked），但错误地图已交付 | f18 场景 `not_evaluated` 声明披露；修复归 data-qualification 方向（不在本分支造第二判定面） |
| G05-F2 | `not_in` 类列表期望在自研评估器初版是标量成员语义（`['circle'] not in ['circle']` 恒真 → vacuous 假绿） | W2 校准 f18 期望时发现（实际 layer_types=['circle'] 而检查恒过）；评审 Round 1 确认 | 已修（集合式互斥语义）+ 边界单测钉死（TestEvaluatorOpBoundaries）+ f02b/f08 反转测试常驻保护 |
| G05-F3 | 离线能力矩阵：poi_query/admin_boundary_query/raster_source/ndvi 无任何离线工具（network-only），矢量任务全族离线终态不可达 satisfied | 探查：planner recipe mandatory 行 × algorithm_registry tool_to_capability 交叉 | 语义钉扎（§3）；将来接入离线数据源后场景期望应重校准 |

## 5. 判错归因（failure taxonomy）

九类：`intent_wrong / wrong_dataset / invalid_method / unmet_product /
stale_render / lost_interaction / export_parity / unsupported /
governance_violation`。归因链：失配检查 → face → 权威模块
（`contracts.FACE_AUTHORITY`）→ 检查定位。CLI `report` 的
`first_failing_stage` 即首坏环节；`attribution.attribute_runs` 提供批量
热区。底层工具执行面分类复用 `HarnessFailureClass`（只读）。

## 6. 质量门（本地，不依赖线上 CI）

```bash
pytest tests/acceptance/gis_agent -m cartography --no-cov --timeout=120 -q   # 45 绿
ruff check tests/acceptance/ scripts/g05_task_acceptance.py                  # clean
python scripts/g05_task_acceptance.py run --profile smoke                    # exit 0
python scripts/g05_task_acceptance.py pin                                    # 无漂移
pytest tests/test_ci_local_gate_contract.py tests/test_pytest_module_name_uniqueness.py --no-cov -q
```

已知边界：全量后端 suite 有 #1753 存量挂起——本方向只跑定向 lane。
