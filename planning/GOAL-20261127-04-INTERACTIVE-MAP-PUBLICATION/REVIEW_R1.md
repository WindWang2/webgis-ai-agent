# REVIEW_R1 — 独立逐行审查（非作者 subagent 执行）

- 审查对象：origin/master(f34cb69dd)...HEAD（8 commits，50 文件 +7641/-8）
- 审查者：独立审查 agent（与实现者隔离的上下文，只读）
- 审查者实测：后端 tests/unit/publication 32 通过、前端 18 通过；迁移单 head 链脚本核验
- 结论：**无 P0、无 P1**；4×P2 + 13×P3。核心读者隔离面（share_code 校验/404 统一语义/
  字段双重投影/产物不可匿名直达）实现扎实且有真实负例覆盖。

## 发现与处置

| 编号 | 级别 | 摘要 | 处置 | 关闭证据 |
|---|---|---|---|---|
| P2-1 | P2 | base_publication_id 无归属校验，可跨 owner 挂靠产品谱系并继承 meta | create_draft 对 base 行补 `_owns` 校验（403） | test_r1_p2_1_cross_owner_base_publication_denied |
| P2-2 | P2 | publication_validate 无归属校验（Pi 面跨 owner 状态/QA 泄露） | 三元组 `_owns` + 运行时会话绑定双链归属；不符 access_denied | test_journey_draft_and_validate（会话链）+ test_journey_session_boundary_fail_closed（跨会话） |
| P2-3 | P2 | 发布门不在发布事务单点；executor CAS 前零校验；路由注释虚报"job 内二次校验"；publish_cas 死代码 | 发布门（license/预算/schema）搬进 executor CAS 前（fail-closed，retry/第二提交面均过门）；死代码语义由 executor 承接；路由注释已与事实一致 | test_r1_p2_3_executor_gate_blocks_license（无 license 草稿进入 job 被 typed 阻断） |
| P2-4 | P2 | 共享源多层 reader 重写错配（第二层指向第一层产物） | 源按层重写（publication:{layer_id} → 各自白名单产物 URL） | test_r1_p2_4_shared_source_per_layer_rewrite + 旅程/浏览器断言更新 |
| P3-5 | P3 | 状态迁移 check-then-act；并发 revoke×publish 可复活 | transition_status 与 executor 统一 DB 级条件 UPDATE（0 行即放弃） | test_r1_p3_5_revoke_publish_race_no_resurrection |
| P3-6 | P3 | bbox 裁剪"广告行为"（生产恒 None） | 文档如实标注"当前未启用；scope.bbox 如实 null" | projection.py docstring |
| P3-7 | P3 | summary 非数值静默跳过+分母注记失真；NaN/Inf 可产出非法 JSON | 非数值独立计数 + isfinite 守卫 + _json_body/store 写盘 allow_nan=False | test_r1_p3_7_summary_stats_honesty（level 字段 12 non_numeric） |
| P3-8 | P3 | 红线正则漏 file:///与 Windows 盘符；违例裸 ValueError→500 | 正则补全；create_draft 包装 typed 422 snapshot_forbidden_content | contract 单测形态验证（file:///、C:\、合法值不误伤） |
| P3-9 | P3 | next_version 读-增-写竞态 → uq 冲突 500 | IntegrityError 捕获 + 版本重取重试 | service.py（uq 约束兜底路径） |
| P3-10 | P3 | 草稿冻结与发布物化间数据漂移 → 分母失真 | executor 物化后逐层 feature_count 与快照核对，漂移 typed 失败 | test_r1_p3_10_snapshot_data_drift_typed_fail |
| P3-11 | P3 | 字段白名单默认全开（设计权衡） | 决策记录于 DECISIONS.md D11（默认全开 + 作者逐字段反选 + 预览强制可见；最小集预设留后续） | DECISIONS.md |
| P3-12 | P3 | 非法 layer_id 503 与 404 语义混乱 | 形态校验前置，非法与不在册同 404 | 路由 `_reader_layer_context` |
| P3-13 | P3 | reader-view 工作区残留旅程诊断 console.debug | `git checkout --` 恢复已提交干净版本 | git status 干净 |
| P3-14 | P3 | reader doc 含未物化层；前端表格只绑 layers[0] | reader_doc 只输出 materialized 层（blockers 已披露其余） | reader.py + 旅程（doc layers = materialized） |
| P3-15 | P3 | 读者限流单桶 240/min 共享 NAT 易触顶 | 分桶：meta 120/min、data/features/summary 600/min | access.py + 路由依赖 |
| P3-16 | P3 | 撤销不清产物目录（磁盘无界） | revoke 终态 best-effort rmtree | test_r1_p3_16_revoke_removes_artifacts |
| P3-17 | P3 | 恒真断言/死键（accessibility_hint）| 移除 | lint + 测试 |

## 修复后回归

- `pytest tests/unit/publication/`（含新增 test_publication_r1_fixes.py 7 项）→ **39 passed**
- cartography 基线 6 passed
- 真实服务旅程 journey_backend.py → ok:true（含 P2-4 新 spec 形状断言）
- 浏览器 R2 复跑（生产 next build/start）：map_canvas/筛选 5/12 行/分母/无 WebGL 等价全绿
