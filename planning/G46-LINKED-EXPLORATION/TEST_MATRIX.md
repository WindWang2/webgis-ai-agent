# TEST_MATRIX — G46（证据分层；每层真实执行）

## L1 单元（后端）— 全绿
- tests/unit/test_g46_contract.py（41→合并后计）：组合语义（E02/E03）、typed identity（E06）、
  null（E07）、闭开（E08）、facet 排除（E11）、MapLibre 编译禁 to-string、digest、ChartSpec 帽
- tests/unit/test_g46_buckets.py：typed 分桶（含 bool/数字 hash 归并 bug 修复回归）、
  other 精确值集（E13）、__other__ 真类别分立（E12）、诚实分母（E09）、
  [a,b) 末桶闭（E08）、facet/full 双口径（E11）
- tests/unit/test_g46_filter_semantics.py：evaluate_where ↔ mapspec_filter 逐算子对齐
  （含 == null/!= null 语义修正回归；两端一致断言）

## L1 单元（前端）— 全绿
- lib/linked-exploration/contracts.test.ts：TS 编译器与后端同构（typed/闭开/null）
- lib/linked-exploration/condition-store.test.ts：多条件并存、分域清空（E04/E05/E16）、
  过滤投影不越层（E17）、toggle-off 自动移除
- test/project/exploration-panel.test.tsx：源锚展示、chips 增删、空集 0（E10）、保存路径

## L2 集成（服务层真实链）— 18+2 项全绿
- test_g46_service_journey.py：
  - inspect 强版本锚；E02 交集 oracle（手算 2）；E03 OR（9）；E06 typed（1/"1"/true 各 1）；
    E07 null+缺席（2）；E08 断点闭开（3/1）；E09 NaN/Inf/字符串披露（15/1/1）；E10 空集 0；
    E11 facet 5/2 与 12/2；E13 组数/截断结构；E14 handle count=5 exact
  - E19 保存/重开同代重放（match=2 oracle）；E20/E21 源推进 → stale + 保存拒绝（独立 session）；
    E22 非 ref: 拒绝；E25 CAS 冲突（detail.current_revision=2）；E27 跨会话 404
  - E26 apply 组件写入 MapSpec 落盘（exploration-chart-chA）
  - E30 SVG 回读（口径注记在 SVG 文本中）+ PNG/PDF 魔数/尺寸/三格式同计划一致
    （重阶段统一锁内执行）
- 空库 alembic upgrade head 实测：exploration_views 落地、head=g46_linked_exploration_views

## L3 Pi journey（真实子进程）— opt-in 门控（PI_JOURNEY_E2E=1）
- test_pi_g46_linked_exploration_journey.py：stub LLM → uvicorn(app) → Pi 子进程
  tool_search 发现 → webgis_exploration_inspect（feature_count=6）→ 组合 preview
  （oracle match=2、facet=3、handle）→ view_save（DB 行落盘证据）→ open ready →
  apply count=2 → MapSpec 组件持久化。10 个 gate。待真实执行记录（重阶段锁排队）。

## L4 浏览器 e2e — 未运行（阻塞：无 Playwright journey 环境时段）
- 前端组件/联动已由 L1 组件测+条件 store 测覆盖逻辑面；浏览器层标未测（诚实披露）

## 既有回归
- mapspec_filter 相关 28 项、chart-map-link/registry parity 11 项、
  frontend map-components+selection 271 项、catalog parity 4 项 — 全绿
- 既有失败（不在本域）：alembic % 编码 URL 测试（干净 base 复现）；
  cartography-result-card tsc 2 错（干净 base 复现）——均属 issue #2123 基线
