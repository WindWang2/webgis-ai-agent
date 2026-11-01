# INDEPENDENT_REVIEW — GOAL 05

## 第一轮（2026-11-02，Reviewer：独立 general-purpose agent，未参与实现）

- 审查范围：`git diff origin/master...HEAD` 全量逐文件 + 复跑命令实测。
- 结论：**0 P0 / 5 P1 / 8 P2 / 5 P3 备忘**；红线核验通过（11 类词表未动、
  无第二 trace store/runtime/调度引擎、auto_retry 无生产执行方、
  tier3/付费/导出/写永不自动重试、跨会话零泄漏）。
- 清偿（commit 6dd206593）：
  - P1-A evaluation 模数 pin 32→33 + corpus manifest 基线更新 + RC- 注册（含 manifest 计数 pin 18→19）
  - P1-B TDS 错误分支传 `self._registry.descriptor(tool_name)`（缺席保守）——风险裁决面在生产激活
  - P1-C reconcile `layers` 兼容生产 dict 形状（observation_states.build_observation_summary）
  - P1-D `scientific_quality` 需求族缺席 → `counts_toward_overall=False`；SSE 处境推导过滤 N/A slot（纯地图目标全绿不再误报 displayed_unverified）
  - P1-E `_user_hidden_layers_from_spec`（spec `cartographic_intent.presentation_owner=="user"` + 不可见；agent 隐藏不享豁免）接入 maybe_finalize→map_product_block→reconcile
  - P2-1 数字状态码词边界+语境双门（failure_taxonomy 与 diagnose 双侧；"buffered 1500 features" 不再误判）
  - P2-2 disposition 改 registry 驱动 property（单一事实源，构造方不可漂移）
  - P2-3 destructive 分支保留 ask_user_data/owner_confirmation
  - P2-4 last_recovery 白名单键集重建（int/bool 强转 + 界长）
  - P2-5 `requirement_id` 键回退（真实 RequirementVerdict.to_bounded_dict 形状）
  - P2-6 幂等键豁免验证责任写入 policy docstring（生产不传、语料显式携带）
  - P2-7 语料 forbidden 独立复核（重新驱动 wiring 断言）+ reconcile/corpus fixture 换生产形状 + E2E no-op 清理 + map-only 全绿回归用例
  - P2-8 文档 27 码勘误（ADR/EVIDENCE/LOOP_STATE/TEST_MATRIX）
  - P3×5：dead call、backoff 切片兜底（防零退避立即重试）、explain 不可达分支、continuation 早退截断、锁对称
- 验证：清偿后 326 passed（recovery/lib/evaluation/taxonomy 族）+ 全影响面 7461 passed / 7 failed（全部为基线存量：component_composition×2、semantic_v2、workflow_guards、terrain_v3、golden_corpus_hash、mutation_parity——逐一 git stash 复现核对）。

## 第二轮（2026-11-02，Reviewer：另一独立 agent，未参与实现与第一轮）

- 审查范围：`git diff 19c97a2e1..HEAD`（R1 修复增量）+ 第一轮遗留面抽查 + 核心套件复跑 + 动态复现实验（classify_situation 前后对照、生产形状 FP、383 工具 risk 扫描、伪造 payload ValueError、403 FP）。
- 结论：**0 P0 / 1 P1 / 4 P2 / 5 P3**；三项 R1 修复"未达声称目的"（SSE 过滤 no-op、语境门恒真、descriptor 字段名错配），均附可复现证据。
- 清偿（commit 0c026d0b7）：
  - P1-1 classify_situation：science∈(None,"pass") 且 rendering pass → clean（纯地图目标全绿不再误报 displayed_unverified）+ 测试钉死
  - P2-1 数字状态码语境门排除泛化 status（"error"/"failed"），信息量 status 保留；新增 `app/lib/gis/http_status` 单一事实源（failure_taxonomy 与 diagnose 共用，消除双份维护）+ 语境词补 service/unavailable/gateway/temporarily + 生产形状 FP 回归测试（"buffered 1500 features"/"found 4291 POIs"/"column value 502"/"exported 403 records"）
  - P2-2 risk.resolve_tool_risk 读真实 `side_effect` 字段（legacy side_effect_class 兼容）——383 工具副作用裁决面激活 + 真实 ToolDescriptor 测试
  - P2-3 last_recovery 重建抽 `_bound_recovery_payload` helper（_int 安全强转 + 全体 try/except）——类型异常只丢 payload，绝不影响能力行标记
  - P2-4 401/403 从 _PERMISSION_MARKERS 移入共享双门
  - P3：_user_hidden_layers_from_spec 观察死臂移除（spec 单一来源）、reconcise 注释勘误、死常量清理
- 复核验证：R2 影响面 310 passed（含全部新增回归）/ 全影响面 7468 passed / 7 failed（基线存量不变）。
- Reviewer 关闭项：P2-6 幂等键豁免（policy docstring 已记录验证责任与生产不传的保守缺省——文档化延期，非未验证豁免）。

## 终验

- 两轮独立 review 全部 P0/P1 清偿；最终全量双跑见 EVIDENCE.md。
- PR：（创建后回填）
