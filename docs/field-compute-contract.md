# 类型化字段计算工作台 —— 函数与契约帮助

> **本文件由真实契约生成**：函数表来自 `field_compute.functions.function_help()`，
> 预算/版本/错误码来自 `field_compute.contracts`，单位表来自 `app.lib.gis.measurement.CANONICAL_UNITS`。
> 刷新命令（worktree 根目录）：`python app/services/data_preparation/field_compute/gen_help.py`

- IR 版本：`g19-ir-1`；函数集版本：`g19-fc-1.0`；收据版本：`1`


## 1. 表达式不是代码

只允许 `lit / field / unary / binary / call / case` 六类闭合节点。字段名用 `{中文 字段名}` 原样引用（按字典取值，不做字符串插值）。
无 `import`、文件/网络访问、属性反射、动态代码、系统时钟、随机数；不使用 `eval` / `exec` / JS `Function` / 拼接 SQL。
未知节点、未知函数、超预算、保留字段、循环/前向列依赖在**计算前**拒绝并返回 `code / detail / position / hint`。


## 2. 首批函数集（契约驱动）

| 函数 | 族 | 输入 → 输出 | null 行为 | 单位规则 | 资源 | 说明 |
|---|---|---|---|---|---|---|
| `abs` | number | number → number | null→null；非数值→TYPE_MISMATCH | `preserve` | O(1) | 绝对值 |
| `round` | number | number, number → number | null→null；digits 必须 0–9 的整数 | `preserve` | O(1) | 四舍五入（可指定小数位） |
| `floor` | number | number → number | null→null | `preserve` | O(1) | 向下取整 |
| `ceil` | number | number → number | null→null | `preserve` | O(1) | 向上取整 |
| `min` | number | number, number → number | null→null；两侧须同量纲 | `preserve` | O(1) | 较小值 |
| `max` | number | number, number → number | null→null；两侧须同量纲 | `preserve` | O(1) | 较大值 |
| `sqrt` | number | number → number | null→null；负数→NON_FINITE | `none` | O(1) | 平方根（负数产生行级错误） |
| `if` | condition | boolean, any, any → branch | 未选分支不求值 | `branch` | O(1) | 条件分支（只求值被选中的分支） |
| `coalesce` | condition | any, any → same_as_first | 返回第一个非 null 且非 error 的值 | `branch` | O(n args) | 取第一个非空值（显式处理 null，不隐式造 0） |
| `is_null` | condition | any → boolean | null→true；error→false | `none` | O(1) | 判断是否为 null |
| `trim` | text | string → string | null→null；输入 ≤2048 字符 | `none` | O(len) | 去除首尾空白 |
| `upper` | text | string → string | null→null；Unicode 安全 | `none` | O(len) | 转大写 |
| `lower` | text | string → string | null→null；Unicode 安全 | `none` | O(len) | 转小写 |
| `concat` | text | string, string → string | null→null（需显式 coalesce）；结果 ≤512 字符 | `none` | O(len) | 拼接文本 |
| `substring` | text | string, number, number → string | null→null；负数/越界按空串；结果 ≤512 字符 | `none` | O(len) | 取子串（起始位置从 0 开始） |
| `str_length` | text | string → number | null→null；按 Unicode 码点计数（non-BMP 计 1） | `none` | O(len) | 文本长度（Unicode 码点） |
| `pad_left` | code | string, number, string → string | null→null；前导零与代码精度保留 | `none` | O(len) | 左侧补齐（行政区代码前导零） |
| `to_string` | conversion | any → string | null→null；丢失单位并披露 | `none` | O(1) | 显式转文本（大整数保精度） |
| `to_number` | conversion | string → number | 空串→EMPTY_STRING_NOT_ZERO（绝不自动变 0）；bool→TYPE_MISMATCH | `none` | O(1) | 严格转数值 |
| `to_integer` | conversion | string → number | 空串/小数→CONVERSION_FAILED；大整数保 Python int 精度 | `none` | O(1) | 严格转整数（代码类文本用） |
| `parse_datetime` | datetime | string, string, string → datetime | 空/含糊格式→行级 INVALID_DATE；无时区→编译期 AMBIGUOUS_DATETIME | `none` | O(1) | 按声明格式与时区解析时间（1 参时用计划级 date_format/timezone） |
| `datetime_part` | datetime | datetime, string → number | null→null；part 必须是编译期字面量 | `none` | O(1) | 提取年/月/日/时/分/星期（按声明时区） |
| `date_diff_days` | datetime | datetime, datetime → number | null→null；按 UTC epoch 差 | `none` | O(1) | 两时间相差天数 |
| `convert_unit` | unit | number, string, string → number | null→null；参数必须是编译期字面量且同量纲 | `convert` | O(1) | 显式单位换算（平方米↔平方千米、percent↔fraction 等） |
| `area_km2` | geometry | (无参，读行几何) → number | 无/非法几何→GEOMETRY_NOT_MEASURABLE；CRS 不合格→CRS_UNKNOWN/CRS_NOT_METRIC | `fixed:square_kilometers` | O(vertices) | 要素面积（km²，CRS 安全） |
| `area_m2` | geometry | (无参，读行几何) → number | 同上 | `fixed:square_meters` | O(vertices) | 要素面积（m²，CRS 安全） |
| `length_km` | geometry | (无参，读行几何) → number | 仅线/多线；其余→GEOMETRY_NOT_MEASURABLE | `fixed:kilometers` | O(vertices) | 要素长度（km，CRS 安全） |
| `length_m` | geometry | (无参，读行几何) → number | 同上 | `fixed:meters` | O(vertices) | 要素长度（m，CRS 安全） |

算子词表：`add`, `and`, `div`, `eq`, `ge`, `gt`, `le`, `lt`, `mod`, `mul`, `ne`, `or`, `sub`；一元：`neg`, `not`。


## 3. 类型与缺失

- 类型：`number / string / boolean / datetime / null / error`。**布尔不是整数**；**空串不是 0**；null 不隐式变值。
- 显式转换：`to_number` 空串 → 行级 `EMPTY_STRING_NOT_ZERO`；`to_integer` 保 Python int 精度。
- 除零 / 溢出 / 非有限结果：`DIVISION_BY_ZERO` / `NUMERIC_OVERFLOW` / `NON_FINITE`；NaN/Infinity 绝不写为合法 JSON 数字。
- 大整数 > 2^53-1：字面量编译期拒绝 `UNSAFE_INTEGER`；数据中的大整数以文本写出并在收据披露 `precision_policy=bigint_as_string`，不静默舍入。
- 行政区代码等文本前导零原样保留（`pad_left` 是显式文本操作，不是数值转换）。


## 4. 单位与科学语义

字段名只是提示，**不是可信单位证据**；单位必须由计划的 `field_units` 显式声明（user-declared）。加减两侧同量纲但不同标尺也必须用 `convert_unit()` 显式换算。乘除生成派生量并保留分子/分母来源；未知组合保留 `undeclared`，不编造科学资格。

| canonical unit | 量纲 | 显示 | 参考倍率 |
|---|---|---|---|
| `celsius` | `temperature` | °C | 1.0 |
| `cny` | `currency` | 元 | 1.0 |
| `cny_100m` | `currency` | 亿元 | 100000000.0 |
| `cny_10k` | `currency` | 万元 | 10000.0 |
| `count` | `count` | 个 | 1.0 |
| `degrees` | `angle` | ° | 1.0 |
| `fraction` | `ratio` | 比例 | 1.0 |
| `hectares` | `area` | 公顷 | 10000.0 |
| `index` | `index` | 指数 | 1.0 |
| `kilometers` | `length` | km | 1000.0 |
| `meters` | `length` | m | 1.0 |
| `none` | `none` |  | 1.0 |
| `percent` | `ratio` | % | 100.0 |
| `persons` | `population` | 人 | 1.0 |
| `square_kilometers` | `area` | km² | 1000000.0 |
| `square_meters` | `area` | m² | 1.0 |
| `usd` | `currency` | USD | 1.0 |
| `years` | `time` | 年 | 1.0 |

几何函数（`area_km2` / `area_m2` / `length_km` / `length_m`）经 `crs_safety.classify_crs` 门控：地理 CRS 用 `pyproj.Geod` 椭球面积/长度；局部度量投影用 shapely 平面几何；3857 等尺度失真投影 → `CRS_NOT_METRIC`；未知 CRS 或 `assumed` → `CRS_UNKNOWN`。几何列被阻断时**其他不依赖几何的列仍可执行**。


## 5. 作用范围

- `all_rows`：全体行都计算。
- `predicate`：受限闭谓词（`eq/ne/lt/le/gt/ge/is_null/not_null/in`）。
- `selection`：必须完整、带 `selection_revision`、稳定唯一 ID 字段。选择集不可靠：请重新选择（完整 ID 集 + revision），或改用 all_rows / 显式 predicate。前端当前页、命中样本与截断 ID 集一律不算全量选中。
- 范围计算**保留全体行**，未选中行按 `unselected_policy`（默认 `null`，或 `absent`）处理；仅导出选中子集是另一项显式操作 `subset_export`，默认 false。


## 6. 错误策略、版本锚与恢复

- 错误策略：`null`（默认，错误行结果为 null 并披露计数与 ≤8 条示例）或 `abort`（遇首个行级错误终止整次，`ABORTED_BY_ROW_ERROR`）。
- 预览是 **sample/estimate**（≤50 行），采样错误率不是全量错误率；预览不产出公开 ref，不改变原数据。
- 计划指纹绑定：输入内容哈希 + schema 指纹 + scope + 列 IR + 函数集版本 + 单位 + 时区 + 错误策略。
- 预览/执行各自冻结一致快照；发布前二次核对内容/schema，变化即 `SNAPSHOT_CHANGED` 中止且不发布。预览后源变化 → `STALE_PLAN`，刷新预览即可继续。
- 取消 → `CANCELLED`；`POST /field-compute/plan/{plan_id}/cancel` 仅对**正在执行**的计划登记取消（空闲/已结束返回 `cancelled=false`，不留陈旧键），取消是**可恢复态**，可原计划重提；写入失败 → `STORE_FAILED` / `WRITE_FAILED`。发布前先探测收据两个写入面；最终收据写失败时撤销已写入的 session ref 与项目清单，**不发布半成品**，也不会让未发布成果被幂等命中。同计划重复提交返回既有收据（幂等，稳定键不受 ring 截断影响）。
- 持久化如实区分：`session_ref`（会话级，重开会失效）/ `project_artifact`（项目清单真实落盘，可重开找回）；项目清单保存失败降级为 `session_ref` 并披露 `PROJECT_SAVE_FAILED`，绝不谎称已可重开找回。


## 7. 支持的数据形态与规模

- ref-backed GeoJSON FeatureCollection（含 `crs` / `crs_source`）与真正的 `rows` 非空间表格；UI 按 `data_shape` 如实披露，不宣称未支持的通用能力。
- 行数上限 `50000`（超限在启动前 `ROW_LIMIT_EXCEEDED` 拒绝，不静默截断）；列 ≤ `8`；AST 节点 ≤ `64`、深度 ≤ `12`；每行操作数 ≤ `256`；字符串输入 ≤ `2048`、结果 ≤ `512`。
- 前端属性计算面板**一次编辑一列**；多列链式依赖（≤8 列，声明顺序即计算顺序，只允许引用之前的列）请用 Pi 对话或 API，不把 UI 单列说成已支持多列。


## 8. 再生成

函数表由 `FUNCTIONS` 词表驱动；新增函数必须同步提升 `FUNCTION_SET_VERSION`、补边界测试，并重跑：

```bash
python app/services/data_preparation/field_compute/gen_help.py
```
