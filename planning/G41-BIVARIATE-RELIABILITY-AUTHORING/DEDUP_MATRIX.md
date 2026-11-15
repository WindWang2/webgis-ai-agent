# G41 DEDUP MATRIX

判定口径：`present`（最新 master 生产调用链已完整满足，本任务只做兼容回归）/ `partial`（有基础但缺本域产品闭环）/ `missing`（无）/ `owned-by-other`（兄弟任务主所有权）/ `blocked`（外部能力/权限阻断）。每项追到生产调用链，不凭文件名推断。base = `1276692737a8cf58d0604fb40ae0e70fc61779a2`。

| # | 要求 | 判定 | 生产调用链证据 | 本任务增量 |
|---|---|---|---|---|
| 1 | 双变量两轴计算 / 2×2·3×3 色阵 / 类别字段 / 图例 | **present** | `app/lib/cartography/bivariate.py:92-225` → `analysis_cartography_converter.py:487-536` → `thematic_spec.py:568-587` → `legends.tsx:89-129` / `export-chrome.ts:1560` | 兼容回归（B04/B11/B12），不重写算法；复用 `bivariate_class_colors` |
| 2 | converter 从 `metadata.field_a/field_b` 生成类别/legend/typed `layer.bivariate` | **present** | `analysis_cartography_converter.py:487-536,827-840`；`mapspec_schema.py:634-654,759-761` | 不复制；G41 走作者服务 + 单笔 mutation，不再用 metadata 拼装冒充作者产品 |
| 3 | G33 typed plan 六模式 / inspect·preview·apply / CAS·checkpoint·undo | **present** | `thematic_authoring/{contract,service,builder,stats,integrity}.py`；`app/api/routes/thematic_authoring.py`；`app/tools/thematic_authoring_tools.py` | **复用同一套**：`resolve_layer`/`_shared_expression_scope` 语义、`apply_gis_mutation`+`UpsertLayerIntent`、`_current_revision`、`expression_fingerprint`。G41 独立模块但走相同入口形状，不改 G33 的 6 模式与 plan v1 |
| 4 | 恰等断点的两套相反语义 | **partial** | `bivariate.py:82-89`（`>` 归下类）vs `thematic_authoring/stats.py:45-59`（`>=` 归上类） | 新 `boundary_rule` 显式字段 + oracle 测试；旧图兼容回归，不统一函数 |
| 5 | 面层 uncertainty opacity / 点描边 width | **present** | `analysis_cartography_converter.py:663-707`；`thematic_spec.py:590-613` | 复用已支持视觉通道；G41 补度量/方向/阈值/缺失契约，不新造 hatch/图标 |
| 6 | SE / variance / CI / PI 度量与 FieldUncertainty ref | **present** | `app/lib/gis/uncertainty.py:40-96` | 复用词表与单位/level 语义；不建第二套统计证据库；只做「输入字段 ↔ 度量事实」可验证绑定 |
| 7 | live 矩阵图例 / Canvas 绘制 / SVG 省略诊断 | **partial** | `legends.tsx:89-129`、`export-chrome.ts:1504-1508,1560-1613`、`vector-svg-export.ts:104,260` | 三端适配器：live 补计数/区间；Canvas 补计数；**SVG 不再省略**而真实绘制矩阵与可靠性图例（语义变更，更新契约测试并说明） |
| 8 | G34 纸面版式 / 图例内容 | **present**（他域权威） | `print_layout_authoring/{legend,compile}.py` → `mapspec_to_svg.py:625-626` → `svg_marginalia.py:221-349` | 只在 canonical 图例内容 + 必要绘制 adapter 上加性接线；不改 G34 位置/尺寸/字体/页框决定权 |
| 9 | 全源统计 / 近似披露 / 共享表达成员拒绝 | **present** | `thematic_authoring/stats.py:1-9,98-238`；`service.py:190-221,432-438,510-515` | G41 的成对统计必须**同一冻结总体**，不拼两份独立样本；复用共享组资格判定（不改组计算器） |
| 10 | 强版本原子冻结 / ABA 检测 | **missing** | `thematic_authoring/service.py:224-286` 分开读 descriptor/content、缺 revision 回退 0；`session_data.py` 只有单调 `content_revision`，无 MVCC 快照 API | 新增 `snapshot.py`：同会话锁 + 权威单调 generation 前后核对 + 全量单次 get deepcopy + 内容指纹 + apply 前复验；无强版本即 typed 拒绝 |
| 11 | 成对联合计数 / 分型缺失 / 准入规则 | **missing** | `bivariate.compute_bivariate_classes` 只给 `matched/skipped`，`float()` 静默接纳数字字符串与 bool，无 joint counts | 新增 `stats.py`：valid_pairs / only_a_missing / only_b_missing / both_missing / bool·string·NaN·Inf·null·缺失 分型，严格数值准入 |
| 12 | 独立轴方法 / 手工断点 / 矩阵转置 / 非对称色阵 | **missing** | `compute_bivariate_classes` 两轴共用 `method`/`n`，无 swap/flip/自定义色阵 | 新增 `classify.py`：每轴 method + manual breaks + swap 转置（计数/颜色/图例同转）+ 非对称 9 色验收 |
| 13 | 可靠性作者（估计+有来源误差/区间，双通道） | **missing** | 全仓无作者 UI；`uncertainty-panel.tsx` 只是披露卡 | 新增 `reliability.py` + 服务 + UI + Pi 工具；无证据时 `blocked/unknown` 与可行下一步提示 |
| 14 | canonical 图例（矩阵格语义 + 可靠性量名/方向/level/缺失） | **missing** | `bivariate_legend_spec` 只有 colors/n/labels/breaks；`read_legend_content` 只给扁平 entries | 新增 `legend.py` 产出机器可验 canonical 图例；各输出适配器消费同一份 |
| 15 | 派生展示副本（不覆盖 `__biv_class`，稳定身份/血缘） | **missing** | `compute_bivariate_classes` 就地写源属性 | 新增冻结展示 ref + 独立派生字段 + `base_source_ref` 血缘，单笔 `UpsertLayerIntent(source_data={"ref_id":...})` 原子提交；失败清理派生 ref |
| 16 | 真实 Pi→dispatch→本域服务→成果旅程（两模式） | **missing** | `app/tools/thematic_authoring_tools.py` 只有 G33 三件套；`tests/integration/test_pi_g33_...` 是唯一同类旅程 | 新增 3 个 typed 工具 + 生产注册 + 两模式 Pi journey（stub LLM，主链不 mock） |
| 17 | G36 Pi 图像 / G37 模型选择 / G38 关联 / G39 栅格采样 / G40 符号纹理 | **owned-by-other** | 兄弟 worktree `g36/g37/g38` 在 `127669273`；`planning/G3*` | 不依赖、不等待、不复制；G41 只用当前颜色/透明度/既有描边 |
| 18 | G09 参数批分析 / G21 有向 OD | **owned-by-other**（历史已分配） | 提示词明示；`webgis-wt-g09-*`、`goal-g21-*` worktree | 不重新占用 |
| 19 | 真实浏览器 E2E | **blocked**（待实测） | 本环境无浏览器可执行文件（Playwright 未安装浏览器）；`frontend/e2e/journeys/*` 需要浏览器 | 如实标未测；用 vitest + 生产 SVG/PNG/PDF 独立回读替代，不写「同等覆盖」 |
| 20 | 线上 CI 结论 | **blocked**（设计上不等） | `CLAUDE.md:41`；任务 A 节明确不等线上 CI | 不轮询 checks、不建 watcher；本地 gate 真跑 |
| 21 | R1 审计问题 / 全仓 CI 修复 | **owned-by-other** | #2101–#2116 已合并修复 | 只尊重质量门，不清偿全部历史问题 |

## 收敛结论

- 已完成项 1/2/3/5/6/8/9 转为**兼容回归**（B04、B11、B12、B29、B30、B34/B35 的 CAS 语义），不制造第二套实现。
- 本任务真正的剩余增量是 10–16：强版本冻结、成对统计、独立轴与转置、可靠性度量绑定、canonical 图例、派生展示副本、Pi 双模式旅程、以及 7 的 SVG/服务端图例适配器。
- 兄弟任务（17/18）与线上 CI（20）不构成等待条件；19 按实测如实披露。
