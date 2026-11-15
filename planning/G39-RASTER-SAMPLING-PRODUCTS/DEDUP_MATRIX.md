# G39 DEDUP MATRIX — 既有能力 vs 本任务增量（base 1276692737）

判定口径：`present`=生产链已有且满足 G39 验收；`partial`=存在但不满足验收、需兼容扩展；`missing`=不存在；`owned-by-other`=兄弟任务所有权。

| # | 能力 | 状态 | 证据（生产调用链） | G39 动作 |
|---|---|---|---|---|
| 1 | 源像元检视（raw/scaled/mask/row/col/unit） | present | `raster_authoring/probe.py` probe()；`/raster/probe` 路由 | 只读复用；S05 需与 probe 口径一致（floor 反算 + tie 规则） |
| 2 | 检视点/波段帽（16/16） | present | `budgets.bounded_points/bounded_bands` | 不放大 probe 帽；新采样域用独立预算（WP07） |
| 3 | DEM bilinear 沿线剖面（高程语义） | present | `terrain_measurements.py:terrain_profile` | 保持不动；G39 通用波段剖面不复用其 z_m 语义，复用数学原语 |
| 4 | 通用数值栅格采样计划（versioned plan、CAS 修订） | missing | 无任何 SamplingPlan 生产代码 | 新建 `app/services/raster_sampling/`（WP02） |
| 5 | 点集站位（保身份/属性、不去重丢站） | missing | probe 仅 16 点即时检视，无持久站位 | 新建（WP04） |
| 6 | 线路站位/距离轴（part chainage、测地/平面米制、断口） | missing | terrain_profile 距离=网格 cos-lat 米制（非椭球）；无 part 概念 | 新建（WP04）；缺 CRS/绕地球组合 typed 拒绝 |
| 7 | 连续波段 bilinear（保留支撑像元+权重、跨孔严格无效） | partial | `terrain._bilinear_sample` 存在但 DEM 专用、不披露权重、无重归一模式 | 新建通用 kernel（WP05），解析夹具 + 权重事实 |
| 8 | 类别波段只许保类别采样 | partial | probe 有 classification 声明通道（dataset_tags），但不拦 bilinear（probe 本就不插值） | 新建：bilinear 对 declared categorical typed 拒绝（S06/S07） |
| 9 | 波段解释（dtype 不猜语义；scale=0/负 scale/offset=0 恰一次换算） | partial | `_read_band_meta` 已有 scale_declared/offset_declared；probe 用 `or 1.0` 默认（有 declared 标志区分） | 复用 band_meta；kernel 换算只看 declared 值本身（0/负均合法），`or` 默认仅在未声明时（S10） |
| 10 | band 17 超摘要截断（≤16 descriptor）的受控补读 | missing | descriptor 16 波段帽 + `bands_truncated` 披露；band_meta ≤64 | 新建：选中超摘要波段时按预算直读该波段真实元数据（S12） |
| 11 | 块读取调度（分组、reader 重用、块数/解码字节记账） | missing | probe 每点 1×1 窗口（一次打开）；RasterReader 有窗口读但无采样调度 | 新建（WP07）：source/band/block 分组 + 批处理 + 预算闸 |
| 12 | 完整样本长表 + 站位矢量成果（持久、可回读、lineage） | missing | 无。G18 只交付既有 ref；G19 产字段层 | 新建（WP08）：parquet 长表 → publish_data_object CAS + 站位 GeoJSON 会话 ref |
| 13 | 采样域 durable job（幂等、取消、恢复、单次发布） | missing | G23 runner 现成；kind 无 raster_sampling | 新增 kind + jobs.py（WP09） |
| 14 | API（describe/plan/preview/execute/status/results/cancel） | missing | 无 raster_sampling 路由 | 新建（WP10） |
| 15 | Pi 工具（typed 参数、有界回执、refs） | missing | 无 raster_sampling_* 工具 | 新建（WP10）；真实 Pi 子进程旅程（S30） |
| 16 | 工作台面板（选源/波段/方法/预算预估/预览/执行/表格/曲线/地图联动） | missing | elevation-profile 只高程；无通用采样面板 | 新建（WP11） |
| 17 | 输入冻结/不可变执行（跨批 ABA 防护） | missing | data_revision=有界指纹；无冻结副本机制 | 新建（WP03）：执行时打开源后 stat+mtime 锁窗口 + 冻结快照 stat 事实；可重现执行要求「执行期文件代次不越界」校验，否则 typed 拒绝（S31/S34） |
| 18 | G24 handle 冻结为精确 scope | present（handle 侧） | `selection_handles` materialize + revision 快照 | 复用：plan 准备时 materialize → 冻结 FC ref；不囤 handle（S22） |
| 19 | 样本表分页/曲线抽稀保端点/缺口断线 | missing | 无 | 新建（WP11 + results API） |
| 20 | G18 下载回读（全行/单位/无效原因） | present（通道） | data_delivery 支持 rows/fabric parquet | 复用：长表走 fabric parquet DataObject；S29 验证 |
| 21 | G29 交互路由 / G28 完成证据 / G34 版式 / G35 媒体 | present | 各域已合入 | 不动；G39 面板与收据与之并行 |
| 22 | 图像输入 Pi 通道（G36）/ 会话模型选择（G37） | owned-by-other | open PR #2117=G37；G36 未开 PR | 无依赖；工具走标准 dispatcher |
| 23 | 数据关联富集（G38） | owned-by-other | worktree 存在未开 PR | 不等待；G39 点集直接用已有导入/ref |
| 24 | 符号纹理资产（G40）/ 双轴可靠性（G41） | owned-by-other | worktree 存在未开 PR | 不依赖 |
| 25 | 遥感指数/分类算法、LOS/体积、垂直基准转换、raster cube | owned-by-other/越界 | remote_sensing/terrain/volumes 现有域 | 不做（明确排除） |

## 已合并相关 PR 复核结论

- #2078（G20 raster authoring）：probe/source/预算基座，见上表 1-3、9-10。
- #2080（G18 dataset delivery）：交付通道（行/parquet/预算），G39 只消费不重造。
- #2089（G23 durable data operations）：runner/job kind 收据短路模式，G39 加性接入。
- #2094-#2100（G29-G35）：交互/技能/拓扑/提案/专题/纸面/媒体域，与本域无文件级冲突；共享 main.py include 与 registry 仅加性。
- #2101-#2116（R1 修复 + chaos/mobile）：权限/预算/源边界语义保留（每请求重鉴权、有界披露），G39 遵守。
- #2117（G37 open）：会话模型运行配置，不触 raster_sampling 文件。
