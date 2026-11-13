# G33 PLAN — 可复现的专题表达编辑工作台

目标：对**已有矢量专题层**完成 作者检查 → 预览 → 单笔权威提交（可 undo/可恢复）的完整产品链。
用户能：选字段、看真实分布与缺失、选分级方法、精确编辑人工断点/类颜色/类别顺序与标签、调
比例符号/线宽映射、预览地图与图例、一次提交；下一轮对话读到用户已确认的表达。

## 架构（对齐 G20 raster_authoring 模式，命名带域前缀防并行冲突）

### 后端 `app/services/thematic_authoring/`
- `contract.py`：`ThematicAuthoringPlan`（pydantic，extra=forbid，schema_version=1）。
  mode ∈ {graduated_auto, graduated_manual, categorical, continuous, divergent, revert_auto}；
  字段：method/k(≤12)/breaks/palette/invert_palette/divergent_center/labels/class_colors/
  categories[{key,color,label}]+category_other/nodata{color,label}/unit/title/
  size_channel{channel: radius|width, field, min_value,max_value,min_size,max_size,scaling: linear|area_sqrt}。
  校验：断点有限、严格递增、2..13 个（不自动排序，逐对指出错误）；颜色 hex 校验；类表长度=类数；
  未知 method/palette 拒绝；divergent_center 有限。
- `stats.py`：单一求值器 `assign_class(value,breaks)`（与 step 语义逐字一致：v>=b 归上类，
  首类吸收 -inf，末类吸收 +inf）；`field_distribution(...)` 全量/有界扫描（values 上限 25 万、
  直方图 ≤96 bins、类别 top60+other、无效值分型 {bool,string,nan,inf,null,missing}、zero_count）；
  统计缓存 LRU（条目≤64 / 字节≤2MB，键=(ref_id,content_revision,field,stats_version)，
  revision 变化即失效）；MVT/无全源层 → basis=degraded，如实标注 approximate/unknown。
- `builder.py`：plan+值样本 → 候选 legend_spec（复用 classify_values/resolve_thematic_colors/
  build_graduated_spec；manual 断点走 thematic_spec 新增加性 builder `build_graduated_spec_from_breaks`）
  + paint（复用 spec_to_paint）+ size_channel StyleMethod（interpolate；area_sqrt = sqrt 输入域上
  线性 → 面积∝数值，精确非近似）+ 类计数表 + under/over 披露 + requested_k vs actual_k 原因。
- `service.py`：`inspect(session,layer_id)` / `preview(...)` / `commit(...)` / revert_auto=
  commit(mode=revert_auto)。commit：重验源锚（content_revision 变 → 409 STALE_SOURCE）→ 组装
  同 id 整层载荷（只改 paint.color/legend_spec/data_binding.field/paint.radius|width/
  thematic_authoring 证据块；保留 source/type/layout/opacity/visibility/labels/scale_profile/
  metadata/provenance）→ `apply_gis_mutation(UpsertLayerIntent(layer,source_data=None),
  expected_revision)` 单笔 CAS+checkpoint；superseded→409。证据块含 previous_fingerprint、
  decided_by(origin)、stats_basis、plan_digest——旧“自动 Jenks”证据在人工提交时被替换，不残留。
- `errors.py`：错误码（UNKNOWN_LAYER/SOURCE_NOT_GEOJSON/STALE_SOURCE/CAS_SUPERSEDED/
  INVALID_BREAKS[i]/INVALID_CATEGORY/FIELD_NOT_FOUND/…）→ HTTP 映射。

### 路由 `app/api/routes/thematic_authoring.py`（prefix=/thematic-authoring，注册进 app/main.py）
- GET `/sessions/{sid}/layers/{layer_id}/inspect?field=`
- POST `/sessions/{sid}/layers/{layer_id}/preview` {plan}
- POST `/sessions/{sid}/layers/{layer_id}/commit` {plan, expected_revision, client_mutation_id}
鉴权/归属：require_owned_session + get_current_user_optional_with_version（抄 raster_authoring.py）。

### Pi 工具 `app/tools/thematic_authoring_tools.py`（_TOOL_MODULES 加一行）
- `webgis_thematic_style_inspect` / `webgis_thematic_style_preview` / `webgis_thematic_style_apply`
  —— 只返回有界统计/类表/授权 refs；apply 带 expected_revision；side_effect=state_mutation、
  map_mutations=("style_layer",)。widget_reply 的 breaks 由 agent 转述进 preview/apply，服务端
  同一校验器兜底（reply 自由文本不直接写 paint）。

### 前端
- `frontend/lib/thematic-authoring/contracts.ts`：回执类型 + TS 孪生 assignClass/sizeForValue。
- `frontend/lib/thematic-authoring/api.ts`：inspectThematicLayer/previewThematicPlan/commitThematicPlan。
- `frontend/components/hud/thematic-authoring-panel.tsx`：挂载于 layer-style-panel（有 legend_spec 的
  矢量层）；字段选择/分布条/模式选择/断点精确输入/类表（色+标签+顺序）/类别分页编辑/发散中心/
  大小映射/预览/应用/取消/恢复自动；generation epoch 防迟到响应；错误定位到具体断点/类别。

### 共享文件加性编辑（最小面）
- thematic_spec.py：+build_graduated_spec_from_breaks；_categorical_to_match 尊重显式
  spec["default"]（缺省行为不变）。
- app/tools/__init__.py、app/main.py：注册行。
- layer-style-panel.tsx：挂载块。
- frontend/lib/map-kit/types.ts：LegendSpec 加性可选字段（default、size_channel）。
- thematic-paint.ts：镜像 categorical default。
- categorical 图例组件：other 条目渲染；graduated 图例：size_channel 行（由同一 sizeForValue 生成样本）。
- i18n messages：加键。

## 边界（不做）
栅格样式(G20)、纸面排版(G34)、时间媒体(G35)、OD 弧线(G21)、字段派生(G19)、标签(G16)、
尺度策略(G27)、跨幅 shared_expression 复制（识别+整组原子更新或提示转交，不复制计算器）、
新符号资产/图案/SVG sprite、数据属性写入、单位换算（unit 仅显示标签）。

## 工作包顺序
WP01 生产链矩阵+fixtures → WP02 contract → WP03 stats → WP04/05/06 builder → WP07 service+routes
→ WP08 前端 → WP09 Pi 工具+journey → WP10 导出核验 → WP11 故障/性能测试 → WP12 独立评审→修复→复审
→ PR。

## 资源纪律
pytest 单进程；vitest 1-2 workers；playwright 1 worker；重阶段用
`$(git common dir)/webgis-heavy-stage.lock` flock；统计扫描有上界+缓存；预览零 mutation。
