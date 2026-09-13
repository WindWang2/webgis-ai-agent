# AC-09 决策日志（docs/dev/ac-09-decisions.md）

任务书：goals-ac/09-visual-judge-selfheal.md ｜ ADR：ADR-0158 ｜ 勘察：ac-09-closure-recon.md

| # | 岔路 | 决策 | 理由与影响 |
|---|---|---|---|
| D-1 | visual_evaluator.py 实际位于 `app/services/gis_harness/`（§8 禁改），而可改区写的是 `app/lib/harness/` | 桩不碰；在可改区新建生产模块，注入 vocabulary（`module:callable`）与 W9 seam 对齐 | 尊重 01/02 线域边界；两个文件职责清晰：seam（扩展点）vs 生产 judge+harness 接线 |
| D-2 | create_thematic_map 在 master 已被 dispatch authoring 覆盖（与任务书描述不符） | 尊重代码事实：P1 断口收敛到模板 symbology/heatmap 与图层样式 command 族；recon §1 给出真实覆盖矩阵 | 避免重复造轮子；勘察先行价值兑现 |
| D-3 | Plan A 触碰 app/tools/templates.py（§8 可改清单未列） | 仅 4 行回填 helper + 两处调用，属 P1 方案 A 明确授权的"给 command 路径工具补等价证据"；不触碰 03 线任何文件 | 任务书内部冲突以 P1 条款为准；ADR-0158 记录 |
| D-4 | 命令族白名单取舍 | 图面内容变更 8 族触发；相机/chrome/导出不触发；底图不挂本次触发（有独立 SetBasemapIntent 通道） | 保守触发面，避免每轮相机移动空转评审 |
| D-5 | 呈现提交（换色带/版面）的执行时机 | 会话锁释放后执行：不持锁路径内联（确定性），直接持锁路由后台调度 | apply_mutation 自带会话锁、不可重入；持锁重入 = 自死锁（实测） |
| D-6 | 投影恢复 patch"未改善"是否回退 | 仅**变差（有害）**回退；持平走既有 repeated→exhausted | 回退会把 live 拉离权威 desired——restore 类补丁持平即回退是伤害性的；既有语义由 test_identical_runtime_repair… 钉死 |
| D-7 | 语义级动作（换分类/级数/值域/标注）能否 runtime 自动执行 | 不能：只产出建议（selfheal_suggestions）；授权后也仅当 03 线 rejected[] 提供完整 recipe（含 legend_spec）才可提交 | runtime 无法重算分类断点（那是工具/recipe 侧职责）；绝不自行猜断点 |
| D-8 | 换色带必须连 paint 一起换 | rotate_palette 构造器同时产出 paint 输出色补丁 | legend↔paint 漂移会被 LEGEND_STYLE_EQUIVALENCE 拒绝（提交面 fail-closed 实证） |
| D-9 | 提交世代进入 harness 台账 | 执行器登记 `cartographic_selfheal_commit`（结构性分类） | 否则后续评估 reported 指纹永远落后 → 恒 superseded，闭环断裂 |
| D-10 | MAX_RUNTIME_REPAIR_ITERATIONS | 维持 2，回退/提交计入签发上限 | 任务书硬约束；上限语义 = 动作签发数 |
| D-11 | 03 线未合入（PR #1258 OPEN） | 定义 `candidates_from_rejected` 读取约定（cartographic_profile.symbology_decision.rejected）+ fixture 驱动测试 | §8 契约；03 合入后无损对接 |
| D-12 | 全量门禁 `-n 2` | 仓库未装 pytest-xdist（requirements-dev 无此依赖），按仓库实际能力串行执行并取证 | 不得为门禁形态新增依赖 |

## 测试资源纪律执行记录

- 日常：`tests/cartography` + harness 相关单测（未跑全量）；里程碑：`tests/unit` 串行全量。
- VLM：全部测试用注入 judge（env seam 同通道），零真实外呼；无 key 路径显式覆盖。
- 前端：零改动（frontend/** 未触碰，runtime-validate 的 map.png 仅只读消费）。
- 迁移：零新增（质量事实库归 10 线）。
