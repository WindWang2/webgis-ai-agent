# G33 DEDUP_MATRIX — 每项判断追到生产调用链

标记：present=已完整存在 / partial=部分存在需接线 / missing=需新增 / owned-by-other=他任务所有权 / blocked

| 能力面 | 状态 | 生产调用链证据 | G33 动作 |
|---|---|---|---|
| 数值分类算法 5 法 + Jenks 采样披露 | present | classify.py:18-177（#441/#618-19） | 只复用，不重做 |
| legend_spec 合同 + v2 溯源字段 | present | thematic_spec.py:31-50,629-663 | 兼容扩展（加性），不重命名 |
| 自动符号化裁决（method/k/palette） | present | symbology.py symbology_decision_from_values（create_thematic_map 消费 cartography.py:509-579） | 复用为 "恢复自动" 的重算路径 |
| create_thematic_map 新建专题层 | present | cartography.py:338-757 → dispatch `_author_display_result` | 不改行为；G33 面向**已有层**编辑 |
| 对已有层的 inspect/preview/apply | **missing** | 全仓无 vector 层专题表达编辑服务（G20 raster_authoring 只覆盖 numeric_raster） | 新增 app/services/thematic_authoring/ |
| 全源字段分布统计（总数/有效/无效/直方图/类别计数） | partial | sources[].profile.fields（spatial_meta_profiler.py:360）有 min/max/mean/quantiles/null_count；无作者口径直方图+无效值分型+精确类计数 | 新增 stats 模块复用 profile + get_ref_data_shared 有界扫描 |
| 人工断点编辑 + 精确键盘输入 | missing | HUD 只有 color/width/radius 常数（layer-style-panel.tsx:92-153）；histogram widget 回复不落 mutation | 新增（服务端校验 + UI） |
| 类别表编辑（键/色/标签/顺序/other） | missing | build_categorical_spec 只在发射器侧（LISA 等）；无作者入口 | 新增 categorical 编辑（高基数分页 + other 策略披露） |
| 连续/发散色域 + 反转 + 中心 | partial | build_continuous_spec/build_divergent_spec 存在但无作者入口；diverging_center 仅自动 | 新增作者控制 |
| 大小/线宽比例映射 | partial | paint-bridge.ts:169 降级 radius/width StyleMethod；SVG _resolve_paint_value:1702 求值——两端就绪，无生产者 | 新增作者入口产出 StyleMethod（面积/半径声明） |
| nodata 规则（色/标签） | present | NODATA_DEFAULT + 三端 guard | 作者可编辑（加性） |
| 单笔 MapSpec 提交 + CAS + undo | present | apply_gis_mutation + UpsertLayerIntent + agent_undo（G20 commit_expression 同款） | 复用；不新建撤销栈 |
| 前端工作台 UI | missing | layer-style-panel 无专题面；raster-authoring-panel 模式可复制 | 新增 thematic-authoring-panel 挂载 |
| Pi 工具（inspect/preview/apply） | missing | webgis_raster_expression_* 是栅格先例 | 新增 webgis_thematic_style_* |
| widget_reply→计划锚定 | partial | canvas_affordance.py:233-250 只封尺寸 | G33 服务端消费时自校验 |
| live/图例/paint 同源 | present | ADR-0078 链 | 验证面（WP10） |
| SVG/PNG/PDF 同表达导出 | present | mapspec_to_svg + G26 publication | 验证面（WP10）；不重分类 |
| MVT 大层统计诚实性 | partial | feature_pages/aggregate 有分页；无作者口径 | inspect 披露统计总体（全源 vs 近似），禁视窗推断 |
| G15 shared_expression 跨幅共享 | owned-by-other/present | G15 frame_product | 只识别作用域并提示/整组原子更新（沿用现有计算器） |
| G27 尺度策略 | owned-by-other | scale_profile | 不覆盖；统计口径来自原数据 |
| G16 标签 / G19 字段计算 / G21 OD / G20 栅格 | owned-by-other | — | 不进入 |
| Pi 真实子进程旅程 | missing（对本域） | G22 恢复链、journeys 基建已有 | 新增本域 journey + stub LLM 驱动 |

## 与并行任务的冲突面预防

- 工具名 `webgis_thematic_style_*`、路由 `/thematic-authoring`、目录 `app/services/thematic_authoring/`、`frontend/components/hud/thematic-authoring-panel.tsx`、`tests/unit/thematic_authoring/` 均带域前缀，无编号冲突。
- 对共享文件（tools/__init__.py、layer-style-panel.tsx、mapspec_mutations.py 挂载、i18n messages）只做局部加性编辑。
- schema revision：legend_spec 走加性字段（schema_version=1 的作者计划模型独立于 legend_spec v2 冻结面），不占用 ADR 编号（不新增 ADR——合同是既有 ADR-0078/0152 的作者面）。
