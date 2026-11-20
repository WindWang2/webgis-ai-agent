# G30: 可执行 GIS 技能作业与版本化参数工作台

## 用户问题

技能此前是「可查阅的方法知识」：用户无法明确选择一个版本、绑定自己的数据、填写有类型参数、审阅具体计划并启动执行；`chat.skill_name` 被显式披露忽略；执行进度/结果/复用无从谈起（ADR-0220 记录的 step 证据事件生产零调用）。

## 行为增量

- **版本化选择 + typed 参数 + 数据角色绑定**：SkillContract schema v2（additive）新增 `parameters`（enum/range/unit/field_ref/条件必填）、`role_bindings`、`execution`（step→既有生产工具的声明式映射，`role:/param:/const:` 闭词表）。三个核心技能升 v1.1.0：
  - `validate_spatial_data` → `audit_spatial_quality`（报告）
  - `point_distribution_analysis` → QC + `webgis_layer_upsert`（绑定点集落 MapSpec 图层，不重抓 POI）
  - `choropleth_map_design` → `create_thematic_map`（count/percentage 诚实可执行；rate/density 归一化未实现即 typed 拒绝，绝不把分子总量着成率）
- **prepare（零业务副作用）**：typed 拒绝码 27 个（缺字段/错单位/过期 ref/枚举外/rate 缺分母/几何不符/要素不足/digest 漂移…）、有界计划预览（步骤/读写性质/证据覆盖/**未覆盖义务**）、幂等键=内容+归属指纹
- **start（真实执行）**：durable job（`JobKind=skill_job`，G23 范式）→ 步骤经 **ToolDispatchService 同一 owner/scope/side-effect 闸**；start 期资产 digest/输入 digest 复核（`SKILL_ASSET_DRIFT`/`INPUT_DRIFT`）；双击/重试幂等；两阶段取消（DB 事实 + 进程内令牌）；结果按类型记录（report=可回读报告 ref；map=图层/geojson refs + `observation_pending` 不伪称 render_observed）
- **Pi 使用链**：`gis_skill_job_{prepare,start,status}` 注册进 deferred 动态面（tool_search 可发现，webgis_execute 同管线）；`bindings` 走 ref 身份 skip_keys（同 webgis_map_product 先例）
- **`chat.skill_name` 真实生效**：三分支解析（id/name/md_guide/歧义/未命中）+ SSE 披露；显式选择经 session ctx 进计划缝（`SkillPolicy._decide_explicit`：execute_guided@confidence=1.0，但 **eligibility/弃用/隔离照常裁决**，unknown≠不满足红线保留）；选择按 turn 清除不粘滞
- **API + 工作台**：`/api/v1/skill-jobs/*`（owner-scoped：catalog/prepare/start/runs/cancel/presets）；前端技能工作台（目录分型 runnable/guidance_only/deprecated/unavailable → 绑定 → 参数 → 预览 → 启动 → 轮询 → 刷新回看 → 预设复用）；md 指南与扩展 candidate 如实分型、不晋级、不按同名合并
- **参数预设**：新表 `skill_job_presets`（迁移 g3001，纯加性）——配置引用（不含数据/凭据），重用=显式重新绑定+重新校验（参数来源 provenance: user/skill_default/preset/derived）
- **ADR-0220 缺口接线**：`step_started/step_completed/step_skipped/skill_completed` 证据事件接入生产

## 复用与范围

复用：SkillLibrary/contract/policy/resolver、SessionPlan（`apply_tool_result` 既有锚点）、durable jobs 全套（store/runner/fencing/取消）、ToolDispatchService 全部闸、session store ref 体系、MapSpec 写路径。不新增第二 planner/DAG/工具注册表/状态总线；不改 Pi pin（v1.5.2）。

## 独立评审（review → fix → rereview）

独立 reviewer（未参与实现）R1：**APPROVE-WITH-FIXES**——5×P1（跨 org 预设更新 IDOR、运行单读 IDOR、choropleth rate 语义谎言、预设重用截断、内联 DoS）+7×P2，全部修复于 `fecd2b7a9` 并附回归测试；R2 复审：**APPROVE**（关键修复另经服务调用级功能复验，非仅测试；残余 nits 已清偿/披露，详见 `planning/G30-EXECUTABLE-GIS-SKILLS/REVIEW.md`）。

## 实际验证

- 新增 39 后端测试（prepare 拒绝矩阵/三旅程真实运行/幂等/漂移/取消/刷新回看/API 归属/预设重用/Pi 工具）+ 4 前端组件测试，全绿
- **Pi 真子进程旅程 J-G30**（`PI_JOURNEY_E2E=1` opt-in）：node stub LLM → uvicorn 生产 app → 真 Pi 子进程：`skill_name` 选择披露 → tool_search 发现 → prepare → start（当前 turn 绑定）→ durable 行 + 报告服务端回读，**全链通过**（旅程内修复：匿名会话 owner-token 回传、per-turn 面两跳激活）
- 回归：gis_harness 全目录 3547 绿；相邻（skills/policy/surface/dedup/G13/durable-jobs 257）全绿；tsc 0 错误；ruff 清洁；alembic 单头
- 未运行：Playwright 真浏览器 E2E（工作台经组件测试+API 链验证；`TEST_MATRIX.md` 披露）；线上 CI（按任务约定不等）

## 既有缺陷修复（披露，附 base 证据）

- `scale_portrayal_tools.py` 两处 `memory_class="small"` 为非法值（词表 light/medium/heavy/unknown）→ G27 两工具在 master 启动期注册失败（`git show ca7b3ec27:app/tools/scale_portrayal_tools.py` 可证）；修复为 `light`
- 上述修复暴露 `cartography_scale_portrayal_{inspect,apply}` capability 悬空（strict manifest fatal）→ 按 #2093 G22 先例补种 2 个 capability（`app/lib/gis/capabilities/platform.py`）

## 兼容/迁移/回退

- SkillContract v2 纯加性：39 个技能全部装载校验通过（未写新字段的技能行为逐位不变）；schema 版本测试合法更新
- `chat.skill_name` 向后兼容：原「忽略」披露被真实解析替代；未带该字段的 turn 清除上一轮选择
- 迁移纯新表（downgrade 完整）；`JobKind` 列无 CHECK 纯加性
- 回退：还原两个 commit 即可（无数据迁移负担；durable job 行按既有任务中心语义留存）

## 未测项与已知边界

- Playwright 真浏览器 E2E 未跑（见上）；base 既有 8 个测试失败（6 类）与本任务隔离（纯净 base worktree 复现记录在 `TEST_MATRIX.md`）
- rate/density choropleth 执行面未实现（诚实拒绝，规划路径仍可用）——后续可接 G19 字段计算做归一化步骤
