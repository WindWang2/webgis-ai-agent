# G31-CROSS-LAYER-TOPOLOGY — LOOP_LEDGER

执行循环账本（每轮：确认闭环 → 调用链 → 增量 → 验证 → 修正 → 记账）。

## Round 1（2026-11-12 晚）—— 研究与基座

- [x] 复核 master ca7b3ec27、open PR=0、#2087–#2093 评审记录（G14/G23/G24/G28 终审结论读毕）
- [x] DEDUP_MATRIX 25 项逐条定状态（含研究子代理"模块缺失"误报的勘误 —— 它读了旧主工作区）
- [x] worktree + branch：zcode/g31-cross-layer-topology-20261112T144915Z
- [x] WP01–WP07 后端：contracts/ruleset/model+0105 迁移/snapshots/geometry_core/evaluators×5/runner/jobs/service
- [x] 语义冒烟 T01–T11 全绿（oracle：40_000 m² 洞 / 10_000 m² 嵌套 / 覆盖排除）
- 关键修正：关系运算必须在**源 CRS 空间**（顶点级 UTM 变换在共享边界产生 ~5 m² 伪缝隙 → 差集误报）；度量在 UTM 分量级变换（MeasureSpace）

## Round 2（2026-11-13 凌晨）—— REST/工具/前端

- [x] REST 17 端点 + main.py 挂载 + api-docs 再生成（drift 测试过）
- [x] 6 个 @tool 注册（_TOOL_MODULES + tier/latency/memory 词表修正）
- [x] 例外/复验/修复桥/导出（exceptions_svc / repair）
- [x] 前端：typed client + 工作台面板（topology 页签）+ i18n（zh/en）+ 10 例测试
- 关键修正：apiFetch 只收 2 参；registry 用注解对象（去 `from __future__ import annotations`）；tsc 基线回到 72（= clean base）

## Round 3（2026-11-13 凌晨）—— 测试矩阵与真实修复

- [x] test_evaluators_g31 20 例（oracle 断言）
- [x] test_api_g31 13 例（J-G31 全旅程 + T12–T23）
  - 真实缺陷修复①：复用运行不克隆 findings（复用后问题列表为空）→ `_clone_reused_run` 全量投影 + 例外按锚点重应用
  - 真实缺陷修复②：stale 判定比对提交期快照（永不触发）→ 改为与源**当前** revision 比对
  - 真实缺陷修复③：apply_scope 的 evaluate_where 实参序反了（where, feature）
- [x] test_ruleset_repair_g31 13 例
- [x] test_perf_curve_g31 3 例（10k 级：8.5s/39,601 对、4.0s coverage、4.2s 端点 —— 预算截断如实 partial）
  - 真实缺陷修复④：端点无候选距离逐次变换 10k 点并集（O(n²)）→ 预变换基准（120s → 5s）
- [x] 回归：quality 88p / feature_edit 123p / selection+data 1212p（2 个 pre-existing 失败均以干净 base 复现归档）
- [x] vendor/pi 子模块 pin b4afdddf 本地构建（重锁内）
- [ ] Pi 车道（test_pi_g31_topology_journey）：driver 认证修复进行中（404 根因 = 匿名流式 × 用户属会话）

## 待办

- Pi 旅程绿 → TEST_EVIDENCE 落盘 → 独立 review → 修复复审 → PR
