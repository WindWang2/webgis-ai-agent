# INTERFACE_CONTRACTS — Track B 共享契约（主 Agent 锁定）

所有扩展 **additive**：既有字段/语义零破坏（既有测试是回归门）。空缺值语义：未知=缺席≠0。

## 1. GET /layers/data/{ref}/features 扩展（M1/M2）

新增 query 参数（全部可选）：

- `order`：`<field>` 升序 / `-<field>` 降序（单字段）。缺省 = FC 自然序（现状）。
  - 排序键全序：数值型按数值、字符串按字典序、缺失/非可比值恒排最后（与方向无关的 null-last）；并列按自然索引稳定。
  - 实现：按 (ref, revision, order) 缓存排列数组；cursor v2 绑定 order 摘要 —— v1 cursor 与 v2 cursor 混用 → typed 400。
- `where`：typed 谓词 AST（JSON，≤2KB）：
  `{"field":F,"op":eq|ne|in|between|contains|startswith,"values"|[...]...}`、
  `{"op":"and"|"or","terms":[≤16]}`、`{"op":"not","term":{...}}`，深度≤4。
  - 约束：field 名 ≤64 字符；in/between 值数 ≤64；未知 op/字段类型不匹配 → 400；**无任意 SQL/JS**。
  - contains/startswith 仅字符串字段，大小写不敏感。

响应新增（additive，既有 features/pagination/revision/feature_count/bbox_mode 不动）：

- `total_matching`: int|null —— 有 filter/bbox 且 n ≤ TOTAL_EXACT_LIMIT(250k) 时精确全扫一次（按 (ref,rev,where,bbox) 缓存）；超限/不可证 → null + reason_code `total_unknown`。**绝不用 returned 假冒**。
- `truncated`: bool —— 本页因 WINDOW_SCAN_BUDGET 停止且后续仍可能有匹配。
- `is_complete`: bool —— next_cursor 为 null（当前序下再无数据）。
- `reason_codes`: [str] —— `bbox_coarse`/`scan_budget_stop`/`filter_applied`/`sorted:<field>`/`total_exact`/`total_unknown`/`positional_id_projected`。
- `id_mode`: "native"|"positional"|"mixed"（见 §3）。

## 2. GET /layers/data/{ref}/aggregate（新端点，M7/M8）

Query：session_id, v(revision guard), group_by(必填单字段), agg=count|sum|mean|min|max(默认 count), agg_field(sum/mean/min/max 必填数值字段), bbox?, where?, max_groups(默认 64，≤256)。

响应：`{rows:[{key, count, sum?, mean?, min?, max?}], group_count, is_complete, truncated, scanned, null_key_count, non_numeric_count, total_matching, revision, feature_count, reason_codes}`。

诚实规则：n > AGG_SCAN_LIMIT(500k) → 422 `aggregate_over_limit`；mean/sum 只吃数值（非数值计入 non_numeric_count，绝不静默当 0）；分组键 null → key=null 计 null_key_count；组数超 max_groups → 其余合并入 `__other__` + truncated=true。

## 3. MVT positional feature id（M3）

- `_encode_feature` 增加可选 field 1（uint64）：**仅当要素无法通过 FEATURE_ID_KEYS 链解析出 id 时**，编码位置 id = index+1。有链内 id 的要素瓦片字节保持不变（ETag/缓存奇偶性）。
- `id_mode`（page/aggregate 响应披露）：native（全部有链内 id）/ positional（全部位置 id）/ mixed。
- 位置 id 语义：**仅 revision 内稳定**（锚点=FC 数组不可变，与 cursor 同源）；跨 revision 不可寻址；不进入 #1873 写通道（feature_edit 永远走 WFID/链内 id）。
- 单要素端点 `GET /layers/data/{ref}/feature/{fid}` 扩展：`id_mode=positional` query 参数显式声明位置寻址（fid-1=索引，越界 404）；链内寻址保持现状。`SpatialIndexEntry` 增 `id_index`（链内 id→索引 dict，懒构建缓存）把 O(n) 降 O(1)。

## 4. 前端 paged table（M4，消费方契约）

- `paged-features.ts` 扩展：`order/where` 参数 + 响应新字段透出（totalMatching/isComplete/truncated/reasonCodes/idMode）。
- table-panel：MVT 层进入 `server-paged` 数据模式——首屏 200 行、"加载更多"续页、列排序→`order`（重置分页）、搜索→`where contains`、视口开关→`bbox`；选择数与总量分开显示（"已选 N · 匹配 M · 共 T"）。
- 状态机：idle/loading/error{kind: conflict|rate|forbidden|notfound|network|overlimit}/ready；409→提示"数据已更新 v<x>"+重载按钮；429→退避重试一次；403/404→诚实文案；会话/层/revision 变更→AbortController 作废 in-flight。
- inline ≤5000 / 非 MVT 路径字节不动（回归门：现有 table-panel 测试全绿）。

## 5. Chart viewport 聚合（M7/M8）

- chart-panel：MVT 绑定 + extentLinked 时改走 §2 端点（bbox=ViewportContext bounds，generation 取消旧请求）；inline 路径不动。
- 证据行（evidence line）最小集：`视口聚合 · 扫描 N · 匹配 M · 分组 K · v<r>` + sampled/truncated 徽标 + null/non-numeric 计数；单位与分母缺失时拒绝 per-capita 表述。

## 6. 标注导出一致性（M9-M10，行为契约）

- 导出 label_requests 的 priority 必须来自 spec 重要度字段（label.priorityField / 类别序列），不得再用收集序。
- 线标注 repeat：spec 有 repeat 语义时导出孪生生成等弧长多锚点候选（≤4/线，additive 候选，不改 solver）。
- CJK 折行：SVG/PDF 导出支持 shared 字宽口径（CJK 1.0em/其他 0.6em）的多行 tspan；截断 60 仍是最后防线。
- 晕圈：导出缺省晕圈色与 live 自适应规则共享同一常量表（不引入像素一致伪验收）。
- MAX_LABELS_PER_EXPORT=400 不改；超预算保留 suppress reason+count 披露。

## 7. Harness 工具（M14）

新模块 `app/tools/layer_browse_tools.py`：
- `webgis_layer_features_page`（分页浏览：ref/order/where/bbox/limit/cursor/v）
- `webgis_layer_aggregate`（视口/过滤聚合：§2 语义）
- `webgis_layer_feature_detail`（单要素详情，含 id_mode 语义披露）
注册进 `_TOOL_MODULES`（单行，放最后提交）；工具响应必须带 reason_codes/total/truncated，Agent 不得宣称全量。

## 8. 禁改清单（交叠护栏）

design_candidates*、component_registry.py、semantic_checks*、component_renderers.py 的 `_SUPPORT_MATRIX` 区、renderer-parity-matrix.md 表体、publication_export.py、atlas_batch/*、map-panel.tsx、use-feature-selection.ts、map-studio-tab.tsx、feature_edit/*、analysis_workflows/*。
