# PR_HANDOFF · G13-CAPABILITY-EXECUTION

- branch: `zcode/goal-g13-capability-execution-20261110T133500Z`
- base: `master` @ `816705218a5aa6202bf15d745cb31cddb3437f44`
- head: 见 `git log -1`（提交序：94be1d3e1 → 0f7ed8ae0 → 99e5cd3ab → dbe37c62c → 6451fa980 → review fixes）
- worktree: `/home/kevin/projects/webgis/webgis-worktrees/goal-g13-capability-execution-20261110T133500Z`

## 用户获得的行为

1. Pi 会话工具面现在感知**真实可用性**：停用扩展/缺 provider/声明态工具
   不再因相似度高进入激活名单（抑制原因入披露与 SurfaceReceipt）；
   未完全验证的工具降权但保留可见。
2. 执行前复核：选中后到执行前发生撤权/停用/同名 schema 换代时，
   dispatch 以 typed 错误拒绝（CAPABILITY_UNAVAILABLE / STALE_TOOL_SCHEMA），
   附同能力替代与重试指引；拒绝释放去重占位，纠因后同任务可重试。
3. 两个分析原语成为一等能力公民：真实算法链（planner 面
   algorithm_backed）→ 可用性快照 native_verified（真实八面对账，非手改）；
   选择器/编译器可从 zonal_statistics / service_area 能力解析到它们。
4. 原语产物真实可复用：`ref:analysis-*` 落 session store + ArtifactRegistry；
   LLM 载荷拿到 ref 与有界摘要（完整逐 zone 表不进上下文）；下一轮把
   ref 当参数传回即生产解引用；伪造/跨会话 ref 诚实拒绝。
5. 能力发现面（list_available_tools）披露可用性状态与不可用理由——
   与抑制面、拒绝面同一词表。

## 实现边界（如实）

- 复核只认**阳性结构事实**；权限判定属既有路由/owner 门禁（未建第二权限
  系统）；provider 健康不做在线探测（缺事实=not_evaluated）。
- 快照缓存只含结构事实（注册/目录/扩展状态代）；权限/租户事实逐请求投影。
- 两原语刻意不声明 fallback_algorithms（均值/单方案服务区非科学等价替代）。
- 扩展宿主生命周期、Pi vendor/bridge 零修改（G12 所有）；pi_native_surface
  仅 additive 消费（skip 集/disclosure/receipt drop）。

## 已执行命令与结果

见 `planning/G13-CAPABILITY-EXECUTION/TEST_MATRIX.md`（26 项 G13 用例 +
全部相关回归 + verify 6/6 + catalog --check + ruff/mypy）。
线上 CI 不等待（任务书授权）；本地全部真实执行。

## 独立评审

见 `planning/G13-CAPABILITY-EXECUTION/REVIEW.md`（独立 reviewer 冷读全量
diff，逐项 a)–j) 核验；修复后复审记录在内）。

## 与其它 PR 的关系

- 依赖 master 已合并 #2016（G06 基础）；#2036（Pi-only）后语义。
- 与开放 #2021/#2022/#2032 无文件交集；不吞并 #2071/#2072 专项。
- G12 若升级 Pi pin：建议合并后跑
  `pytest tests/unit/gis/test_g13_capability_execution.py` 作契约回归。

## 迁移/回退

- 三个 kill switch：`GIS_CAPABILITY_AVAILABILITY_CACHE=0`（缓存直编）、
  `GIS_AVAILABILITY_RERANK=0`（selector 逐位回退 V7）、
  `GIS_AVAILABILITY_DISPATCH_RECHECK=0`（复核回退既有闸）。
- schema 变化：capability-artifact/v1→v2（additive：+stored，ref 变
  Optional；v1 无存量消费者——grep 零读取方）；pi-availability-projection
  /v1 不变 + 新增 pi-tool-qualification/v1 只读视图。
- 回退 = revert 本分支（无数据迁移、无 schema 破坏）。

## 未测限制

- Playwright/浏览器面与真实外部 provider 未测（本地无外部服务）；
- Pi 扩展侧 setActiveTools（vendor JS）不在本地测试面（Python 侧以
  marker 内容断言）；
- 全仓 3 万级 pytest 未整跑（资源纪律，按影响面选择）。
