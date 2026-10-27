# G3D03 INDEPENDENT_REVIEW — 两轮独立评审与修复处置

评审人：两个未参与实现的独立 agent 上下文（科学正确性面 / 工程 ABI 面），
各自只读评审 + 本地复跑验证。以下报告为**原文收录**，处置列于每条之后。

## 第一轮 · 评审 A：GIS 科学正确性与数据完整性（approve-with-fixes）

### P1-1 抛物线弧长闭式公式错误（asinh 系数差 2 倍因子）
> 测试写成 `0.5·(t·√(1+4t²) + 0.25·asinh(2t))`，正确原函数是
> `(t/2)·√(1+4t²) + (1/4)·asinh(2t)`；真弧长 64.991556 而非 64.558217，
> 6e-3 容差侥幸吞掉偏差；EVIDENCE.md 把错误值当解析期望公示。

**处置：已修。** `tests/unit/lib/test_terrain_measurements.py` 改为正确原函数
+ mpmath 复核锚点断言（64.991556）+ 容差收紧至 rel=2e-3；EVIDENCE.md 数值
与表述已更正。

### P2-1 「surface ≥ horizontal」无条件主张在 skip+洞时可被违反
**处置：已修（三管齐下）。** 新增 `covered_horizontal_m`（有效采样对水平长，
`surface ≥ covered_horizontal` 无条件成立）；lib method/descriptor/工具描述/
DECISIONS 四处主张改为限定式；新增洞场景测试钉扎双不变量。

### P2-2 approximate=True 只标了 LOS，三个采样近似算法漏标
**处置：已修。** profile/surface_distance/surface_area 三个 descriptor 补
`approximate=True`（与 viewshed 先例一致）。

### P2-3 LOS 对称性表述字面为假（offsets 不影响判定的读法）
**处置：已修。** lib meta/descriptor/工具描述/ADR 四处统一为「几何视线对称：
角色互换（连同各自高度）判定不变；高度本身改变视线与可见性」。

### P3-1 折射系数注释 ~1/7 与 0.1307 不符
**处置：已修。** 注释改为测量学口径 k=0.1307（R_eff=R/(1−k)）。

### P3-2 ADR 与实现四处口径不一致（按像元 vs quad、单位不一致路径、
资源 notes、mask planar 近似未披露）
**处置：已修。** ADR 改 quad 口径 + 词表校验口径；三个 descriptor 资源
notes 改为「(h,w) 级仅 _prepare 副本」；surface_area meta 与 descriptor
limitations 增补 mask 边界按像元中心/planar 为 quad 近似的披露。

### P3-3 ft 词表未披露国际英尺 vs US survey foot
**处置：已修。** 词表注释 + ADR 增补（survey foot 须先换算）。

### P3-4 收敛指示收敛到 bilinear 插值面而非真实地表
**处置：已修。** surface_distance meta method 与 descriptor limitations 增补。

### P3-5 杂项（surface_distance 双重采样 / LOS 阈值硬编码不披露 / 
bilinear 0×NaN 保守口径 / method refs 借邻）
**处置：** 双重采样已修（include_samples 单次采样，meta.sample_points 装配
FC 后剥离出 payload）；LOS meta 披露 `max_invalid_fraction: 0.5`；bilinear
保守口径为既有 viewshed 同款（保持一致，评审亦认记录性）；method refs 维持
（书目核实无误，弱相关非虚引）。

## 第一轮 · 评审 B：ABI / 契约 / 资源 / 回归 / 治理（approve-with-fixes）

### P1-1 生成物账本失配（format 后未再 --update）
**处置：已修并流程化。** 根因是 ruff format 在 ledger --update 之后执行；
本轮修复顺序改为 format → 测试 → 全 regen 链 → ledger --update（最后一步），
`test_ledger_is_current` 现绿。

### P2-1 垂直基准/单位词表两处硬编码、无单一事实源、无 pin 测试
**处置：已修。** 新增 `test_measurement_vocab_parity_pinned`：四契约的
vertical_datum/z_units/nodata_policy/curvature/override_vertical_datum enum
与 lib 词表钉死（漂移即红）。

### P2-2 新 4 工具寄居 `_register_hydrology_v4_tool`，归属错位
**处置：已修。** 移入独立 `_register_measurement_tools(registry)`，
由 `register_terrain_tools` 显式调用（不再寄生水文 V4 helper）。

### P3-1 memory_class 声明与同读路径族不一致
**处置：已修。** 三工具 medium → heavy（与 viewshed 同一整波段读路径一致）。

### P3-2 surface_distance_analysis 三倍采样浪费
**处置：已修**（同评审 A P3-5，include_samples 单次采样）。

### P3-3 terrain_profile nodata_gaps 按抽稀后输出表统计
**处置：已修。** gaps 改用 meta.invalid_sample_count（全采样口径）。

### P3-4 wiring 测试 payload 缺 profile 键（未覆盖 #688 零遍历路径）
**处置：已修。** `_dispatch_analysis_payload` 补 profile（featureCount/
geometryTypes）。

### P3-5 全文件 ruff format 噪声 diff（~9k 行）
**处置：接受并披露。** format 已应用（AST 级零语义变化，评审 B 已验证既有
工具函数逐名零变化）；PR 描述注明。未来 reformat 与功能改动分开提交。

### P3-6 skip 政策下可能产出单坐标 LineString
**处置：已修。** lib 对 profile/surface_distance 增补「有效采样 < 2 →
NO_VALID_OBSERVATIONS」守卫 + 测试（比例守卫后可达路径）。

### P3-7 ADR 对称性措辞与实现披露易混淆
**处置：已修**（同评审 A P2-3，ADR 同步改写）。

## 评审 A/B 共同「已核、无问题」要点（摘要）

- 数值方法：bilinear 平面精确性、对偶网格叉积公式（独立推导一致）、
  逐 quad surface ≥ planar、边界计数公式；
- oracle 独立性：meas_* 期望值逐一手核为解析闭式，非实现回声；
- LOS：bulge 符号/物理口径、折射方向、400k 独立网格对照方法学、容差口径；
- 垂直基准：词表双侧闭合、unknown 残余风险已在 RISKS 披露、单位语义一致；
- 资源/取消：先拒绝后分配、checkpoint 与声明一致、meta 确定性 dict 相等；
- 工具 ABI：Pi 投影 union 类型保留、schema 预算内、parity 门绿、dispatch
  ref 化通道命中、既有工具 AST 级零漂移、负例矩阵达标、无安全面。

## 第二轮（复审）

评审 A（科学面，与第一轮同一独立上下文）复审修复后的工作区：62 个相关测试
本地复跑通过、关键数值独立复算。结论：数值与治理修复**全部到位**（P1-1
抛物线闭式 + covered_horizontal_m + approximate 布尔位 + 对称性措辞 + ADR
口径 + 资源 notes + mask 披露 + 单次采样 + <2 有效采样守卫均确认，工程面
修复亦确认无科学问题）；发现 3 处**字符串级残留**（工具描述两处旧措辞因
修复脚本部分中止而丢失、PR 草稿陈旧容差数字）。**整体 approve-with-fixes**。

三处残留已全部补修（工具描述对称性/不变量限定、lib docstring、PR 草稿
64.991556/~0.11%），复跑受影响套件 74 passed + 四工具对齐 overall=pass +
再生成链与账本门 95 passed。**两轮评审闭环，最终状态通过。**
