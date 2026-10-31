# TEST_MATRIX — M1 场景 trace + 验收矩阵 + 测试命令

## 1. M1：5×用户场景 trace（现状逐节点判定）

### S1 行政区设施分布（「统计成都市各区小学数量与每万人密度」）
| 节点 | 现状 | 断链 |
|---|---|---|
| 意图→recipe 选择 | production（distribution.py 12 recipe；intent 规则表） | — |
| POI/边界获取 | production（本地边界工具 + fabric/POI 工具） | 角色绑定到具体 ref 靠 Pi 裸调 |
| 空间连接/按区计数 | production（spatial_aggregate：UTM+sjoin+count，无数据区 has_data=false） | M:N cardinality 无人收口；链无契约 |
| 密度（每万人） | partial（aggregate_with_denominator 显式分母、零分母 rate=null） | 分母角色缺失时无工作流级 typed blocked——靠 LLM 自觉 |
| 结果→MapSpec | production（converter + webgis_map_product） | 步骤证据/lineage 不进产物元数据 |
| 局部重算 | absent | 换 POI 数据→全链盲跑或靠工具级 shape 复用碰运气 |

### S2 公共服务可达性（「小学 15 分钟步行可达覆盖与低覆盖区」）
| 节点 | 现状 | 断链 |
|---|---|---|
| 路网获取 | partial（本地 OSM 提取/fabric/高德；无 osmnx） | 无路网时无 typed 工作流门（工具会报错但无 alternatives 面板） |
| 等时圈 | production（geo_analysis/network.py 真 Dijkstra；速度表/STRtree/部分边裁剪/不可达 reachable=false） | — |
| 覆盖叠加/覆盖人口 | production（overlay + zonal/dasymetric） | 链式组装 + 步骤产物复用键 absent |
| buffer 降级 | production（service_area_simple 诚实 proxy 声明） | 工作流层未把它作为显式 fallback 分支披露科学含义差异 |
| 低覆盖区识别 | partial（差值/重分类可拼） | 无族级编排 |

### S3 风险暴露（「化工厂 1km 内人口/资产暴露」）
buffer（metric CRS 全纪律）production；overlay production（M:N 碎片无选项）；zonal/dasymetric production。断链：缓冲→叠加→统计的链契约、阈值/置信度警示、overlay 链 evidence 块（插值族有、矢量族无）。

### S4 多期变化（「2018 vs 2023 建成区变化」）
detect_raster_change production（窗口化+对齐证据）；时间角色资格 production（has_time_field）。断链：双期输入年份/CRS/范围对齐校验无工作流级门；矢量双期差分需手工 overlay 组合；变化面积/百分比/不确定度无族级封套。

### S5 统计分区（「按网格统计案件率热点」）
h3_binning production（含 legend 裁决）；moran/lisa/EB production。断链：网格/行政组合链的分母与边界契约（不产生虚假归属）无显式声明；多重比较/伪 p 披露仅 recipe 声明层。

## 2. 验收矩阵（任务书 §验收，逐项落测试）

| 验收项 | 测试 | 层 |
|---|---|---|
| 总数=分区计数合计（边界点策略显式） | synthetic 学校/行政区：sum(区计数)==POI 总数（intersects 多方属策略声明下允许>，须披露）或去重策略断言 | unit |
| 缺行政边界 typed blocked | 无 boundary ref → plan 返回 AWF_DATA_ROLE_MISSING_BOUNDARY + alternatives，run 拒绝执行 | unit |
| 分母不存在只给原始计数 | density 步骤 typed skipped(AWF_DENOMINATOR_MISSING)，输出仅 count + 披露 | unit |
| 无路网不得 15min 数值结论 | 无 network → typed AWF_NO_NETWORK_FOR_ACCESSIBILITY；建议 buffer 替代但 message 注明科学含义差异 | unit+negative |
| 输入 B 变更只失效依赖步骤 | 改 POI ref → 仅 join/aggregate 步 stale，边界步骤复用原产物 | unit |
| 仅改配色零科学重算 | style 事件 → stale 集为空 | unit |
| 重启/中断恢复不重复写产物 | state 重建 + 同 fp 幂等跳过；产物 ref 不重复铸造（复用既封套） | unit |
| >10 万 features 限额/阻断不进上下文 | executor 行数预算 typed AWF_BUDGET_EXCEEDED；Pi 返回只有有界摘要+ref | unit+perf |
| 每步 MethodUseContract/receipt | StepReceipt 断言 algorithm_id/输入指纹/警告；未执行步骤不出现在 receipt | unit |
| 工具失败不得 pass verdict | dispatch success=false → 步 failed，workflow verdict 非 pass | unit |
| 越权 ref 绑定被拒 | 他 session ref → 角色绑定 typed 拒绝（session store 隔离语义） | unit |
| 输出图层可被 Map Product Planner 消费 | run 结果经 webgis_map_product → MapSpec layer 断言（E2E） | acceptance |
| 真实 dispatch E2E | G05 新族场景走真 runner | acceptance |
| 科学负例（单位/年份/小样本/多重比较） | unit 负例族 | unit |

## 3. 测试命令（主仓 venv）

```bash
PY=/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python
cd /home/kevin/projects/webgis/webgis-worktrees/gis-analysis-workflows
$PY -m pytest tests/unit/gis_harness/analysis_workflows -o addopts= -q          # 新模块单测
$PY -m pytest tests/unit/gis -o addopts= -q                                    # 相邻回归（G02/G03 面）
$PY -m pytest tests/acceptance/gis_agent -m cartography --no-cov --timeout=120 --timeout-method=thread -q
$PY scripts/g05_task_acceptance.py pin && $PY scripts/g05_task_acceptance.py run --profile smoke
$PY -m ruff check app/services/gis_harness/analysis_workflows app/tools/analysis_workflow_tools.py tests/unit/gis_harness/analysis_workflows
$PY scripts/check_tool_capability_conformance.py                               # 新工具对账门禁
$PY -m pytest -m cartography -q --no-cov                                       # 全量制图 lane（终验）
```

性能锁：`-m perf` 新增 10 万 features 上限行为测试（预算阻断路径，不实际加载进上下文）。
