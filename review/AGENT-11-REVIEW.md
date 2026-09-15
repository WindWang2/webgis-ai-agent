# AGENT-11 审查纪要：空间反幻觉与地理红线安全守护引擎（ADR-0195）

- 分支：`agent/11-spatial-anti-hallucination-guardrails`（基于 origin/master @ 3eb2cc6a）
- 执行：zcode Agent，标准研发流（Worktree → ADR/Spec → TDD → 实现 → Review → PR）
- 日期：2026-09-15

## 1. 任务书要求对照表（需求 → 证据）

| 任务书要求 | 交付证据 | 状态 |
| --- | --- | --- |
| ADR：`docs/adr/0195-spatial-anti-hallucination-guardrails.md` | 四级拦截层级（L1_FORMAT_CRS/L2_GEOGRAPHIC_BOUNDS/L3_LANDMASK_PLAUSIBILITY/L4_TOPOLOGY_CONSISTENCY）+ 三种防御策略（BLOCK/AUTO_FLIP/WARN_DEGRADE）齐备 | ✅ |
| 技术规格书 `docs/dev/spatial-guardrails-spec.md` | 模块接口/算法/数据格式/挂载点/测试计划/性能预算 | ✅ |
| 单测套件 `tests/unit/test_spatial_guardrails.py` | 99 用例全绿（先于实现编写，TDD 红灯基线留档） | ✅ |
| 20 组故意倒置坐标检测+自动修复+置信度标记 | 12 组硬信号（置信度 1.0）+ 8 组软信号（0.60–0.95 分档），断言 `corrected` 精确等于 `[lng, lat]`；基准另覆加 31 省会 31/31 精准修复 | ✅ |
| 陆地掩膜测试：公海/大洋建筑点位 → `GeographicImpossibilityError` | 太平洋/大西洋/印度洋/南太平洋 × 学校/医院/建筑/POI 全组合硬阻断 | ✅ |
| 虚构行政区划第一道防线截获 | `999999`/`190000`/`000000`/`990000`/格式错 → `FabricatedAdminDivisionError`（结构层零表依赖） | ✅ |
| 无网络完全离线守护 | socket 封禁 fixture 下倒置/掩膜/区划/网关全功能可用；引擎仅标准库依赖 | ✅ |
| `latlon_inversion_detector.py`（bbox 空间哈希） | `geo_index.py` 10°×10° BboxSpatialHash + PNPOLY + 线段测距 | ✅ |
| `landmask_validator.py` 离线轻量掩膜 | `data/world_landmask.py` 内嵌 40+ 陆块/水体多边形（~450 顶点，零外部文件） | ✅ |
| `admin_division_verifier.py` 归属校验+模糊容错 | 34 省级全量 + ~340 地级全量表；名称/别名→码、编辑距离≤2 近似码建议、归属纠偏 | ✅ |
| `guardrail_middleware.py` 前置挂载 | `ToolDispatchService.dispatch`（1.45 节，去重后/复用前）+ `lifecycle_engine.apply_mutation`（锁前管道） | ✅ |
| `tool_dispatch_service.py` 接入 | BLOCK → `status="error"` 且工具**绝不执行**（fake registry 断言）；AUTO_FLIP → arguments 就地改写 | ✅ |
| 反幻觉检出率 ≥ 99% | 20/20 测试样本 + 31/31 省会 = 51/51（100%）；231 组合法输入误报 0 | ✅ |
| 处理开销 < 5ms | 单点校验 p50=0.038ms / p95=0.056ms / p99=0.082ms（约为预算 1%）；100 点 FeatureCollection 2.9ms | ✅ |
| 安全边界防御（Geofence Redlines） | `redlines.py`：区域围栏（env JSON 注入）+ bbox 面积预算（默认 ~500km²） | ✅ |

## 2. 交付清单

新增（13 文件，~2160 行）：

```
docs/adr/0195-spatial-anti-hallucination-guardrails.md
docs/dev/spatial-guardrails-spec.md
app/services/spatial_guardrails/
├── __init__.py                  公共 API（75）
├── types.py                     层级/策略/issue 机器码词表/配置（135）
├── errors.py                    错误族：GeographicImpossibilityError 等（38）
├── geo_index.py                 空间哈希 + 等距圆柱测距（118）
├── latlon_inversion_detector.py 硬/软信号置信度融合倒置检测（110）
├── landmask_validator.py        三区制海陆判定 + 设施常识（148）
├── admin_division_verifier.py   GB/T 2260 三段校验 + 模糊修正（205）
├── redlines.py                  区域围栏 + 面积预算（142）
├── topology_checks.py           瞬移线/退化面拦截（102）
├── guardrail_middleware.py      编排网关 + 意图分发（608）
└── data/
    ├── __init__.py              惰性单例加载（69）
    ├── world_landmask.py        全球陆块/水体粗掩膜 v1（206）
    └── admin_divisions.py       行政区划快照 gb2260-2026.09（214）
tests/unit/test_spatial_guardrails.py   99 用例（553）
```

修改（2 文件，+108 行，均为单向可摘除的前置调用）：

- `app/services/tool_dispatch_service.py`：dispatch 1.45 节守护分支；
- `app/services/mapspec/lifecycle_engine.py`：apply_mutation 锁前校验 + 两个懒加载桥接函数。

## 3. TDD 过程记录

红灯基线：实现前运行 `pytest tests/unit/test_spatial_guardrails.py` → collection error（模块不存在），确认测试先行。

转绿过程中发现并修复的缺陷（测试驱动的真实价值）：

1. **软信号样本换算错误**（自纠）：安卡拉/雅典/金沙萨三组"倒置后落海"样本，
   按 (lng=原lat, lat=原lng) 换算后实际落在陆地（西伊拉克/红海沿岸/马里内陆）
   —— 代码判定正确，样本错误。替换为雷克雅未克/都柏林/巴马科（换算后确信落海）。
   教训：倒置样本必须以 (lng=φ, lat=λ) 逐组验算，不能凭直觉。
2. **地级表键长错配**：PREFECTURES 以 4 位段码为键，verify_code 用 6 位全码
   查表 → 全部地级码误判 UNKNOWN。修正为 `code[:4]` 查表 + 模糊候选补齐
   至 6 位后比距离。
3. **水体优先级**：里海等内陆水体被欧亚陆环包围，LAND 判定先于 WATER
   导致湖心点被判陆地。修正为水体环优先（warn-only，语义安全）。
4. **设施词表漏键**：GeoJSON properties 用 `kind` 键，`_FACILITY_KEYS`
   漏收 → 深海医院未硬阻断。补 `kind`。
5. **可疑倒置不硬杀**（设计补强）：置信度不足的疑似倒置点，其 L2/L3
   海陆判定基于未确认坐标 → 改为只警示不阻断（宁可 WARN 放行红线）。
6. **L4 越权拦截蝴蝶结多边形**（全量回归暴露的层级边界缺陷）：自交环的
   净鞋带面积可为 0，`validate_polygon` 的零面积判定把 quality gate 的
   职责数据（`_dirty_fc` 自交面，应有 make_valid 修复方案）提前硬拦，
   `test_quality_gate_lifecycle` 5 例在分支上转红。修正：退化判定只看
   顶点重复占比（≤50%），净面积检查删除（自交修复归 quality gate），
   ADR/Spec 同步更新层级边界注记。

## 4. 架构要点（决策与红线）

- **唯一事实源挂载**：两条 agent 路径的工具调用都经 `ToolDispatchService.dispatch`，
  全部 MapSpec 突变都经 `apply_mutation` —— 两处前置管道即全链路覆盖；
- **fail-open 纪律**：网关内部异常 → `GUARDRAIL_INTERNAL_ERROR` 警示放行，
  两个挂载点再各兜一层异常边界 —— 守护网关绝不瘫痪调度/突变面；
- **判级红线**：三区制 + 近岸带不阻断 + AUTO_FLIP 高置信才触发 + 可疑倒置
  只警示 —— 宁可漏杀，绝不冤杀真实位置；
- **离线纪律**：仅标准库、零网络、零重依赖（numpy/geopandas 不接触），
  数据资产进程内惰性构建一次（300 次校验 p99=0.082ms）；
- **kill switch**：`SPATIAL_GUARDRAILS=0` 恢复 master 行为，两挂载点均为
  单向可摘除调用，无状态残留；
- **可观测**：verdict 携带层级/策略/机器码/证据/耗时；BLOCK 事件含
  `guardrail_findings` 结构化数组（对齐 ADR-0078 findings 词汇）。

## 5. 基准数据

| 指标 | 结果 | 预算 |
| --- | --- | --- |
| 单点校验（L1 倒置+L2 海陆+L3 常识+区划）p50 / p95 / p99 | 0.038 / 0.056 / 0.082 ms | < 5ms ✅ |
| 100 features FeatureCollection | 2.9 ms | < 100ms ✅ |
| 倒置检出（20 测试样本 + 31 省会） | 51/51 = 100%，修复坐标全部精确 | ≥ 99% ✅ |
| 合法输入倒置误报（31 城市 + 200 随机陆地/近岸点） | 0/231 = 0.00% | — |
| 新增单测 | 99/99 通过（含 perf 预算断言 p95<5ms） | — |
| ruff check（新模块 + 测试 + 两个被改文件） | All checks passed | — |
| 全量 `tests/unit` 回归 | 见 §7 | 无回归 |

## 6. 已知边界（v1 明确不做，规格书 §11）

- 粗掩膜（1–2° 容差）只回答"开阔水域深处"，不做海岸线级判定；近岸
  不确定带全部降级警示；
- 内陆水体仅覆盖大型水体（里海/黑海/五大湖/贝加尔/维多利亚/青海湖/哈德逊湾），
  水库/中小湖泊不识别；湖内设施为 warn 不为 block；
- GCJ02/WGS84 不做转换判定（中国境内 ~百米偏移远小于 150km 海区置信余量）；
- 县级行政区划码不入表（体量大且年度增删频繁），结构合法未收录一律
  UNKNOWN_BUT_PLAUSIBLE 警示；
- 后续路标：landmask provider 抽象接高精掩膜、findings 面板化、DEM 高程
  常识（山脊/绝壁）。

## 7. 回归与门禁结果

- `pytest tests/unit/test_spatial_guardrails.py -v`：**99 passed**（任务书
  原命令，含默认覆盖率报告）；
- `ruff check app/services/spatial_guardrails/ tests/unit/test_spatial_guardrails.py
  app/services/tool_dispatch_service.py app/services/mapspec/lifecycle_engine.py`：
  **All checks passed**；
- 全量 `pytest tests/unit -q -n 4`（11,906 用例，修复后终跑）：**30 failed /
  11,776 passed / 127 skipped，零本分支回归**。逐一对账（stash 回 master
  基线 + 串行复跑双重验证）：
  - **18 例 master 既有环境失败**：PostGIS localhost:5432 拒连 ×1、
    pmtiles/flatgeobuf 真实文件 ×9、Windows 符号链接 path_guard ×4、
    双进程 recovery_ledger ×1、real-socket llm_http ×1、mapspec_store
    compile 依赖 ×1、domain_c_raster ×1；
  - **9 例 extensions_platform Windows 环境失败**（lifecycle_v3 ×1、
    resource_limits rlimit/mem-cap ×3、review_r1 stdout-hang ×1、
    review_r2 settings ×1、streaming_v3 bwrap ×1、worker_integration ×1、
    worker_projection ×1）——stash 回 master 串行复跑同样失败（bwrap 用例
    断言 PYTHONPATH 以 ":" 分隔，Windows os.pathsep=";" 导致的确定性
    环境失败；rlimit 为 Unix-only API）；
  - **3 例并行负载 flake**（pi_bridge_leak 内存增长 ×1、
    reproducible_gis_runtime DB 用例 ×2，且两轮全量跑中失败集合漂移）
    ——分支与 master 串行复跑均通过；
  - 首轮全量中 `test_quality_gate_lifecycle` ×5 为本分支引入（缺陷 #6），
    已修复并在后续全量跑中归零。

## 8. 结论

按任务书四阶段完成：ADR/Spec 先行 → TDD 红灯 → 实现 → 全绿收敛。
四类空间幻觉（倒置/虚构点位/虚构区划/越权抓取）在两条唯一事实源路径上
获得前置拦截与自动纠偏，检出率 100%、延迟为预算的约 1%、合法输入零误报，
可安全合入。
