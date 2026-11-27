# PR: feat(moving-object): 移动对象轨迹、时空事件与地理过程分析（GOAL-20261127-02）

## 基线与分支

- 基于 origin/master `f34cb69dd`（2026-11-27，PR #2289 合并后；启动审计时快照一致）
- 分支：`zcode/goal-moving-object-events-20261127`（独立 worktree 开发，master 只读）
- vendor/pi gitlink 未改动（02 线只消费现有运行能力）

## 新能力（真实生产入口，非 demo）

**领域内核** `app/services/moving_object/`（02 线独占移动对象语义，temporal/network 只读复用）：

1. **WP1 实体-观测合同**：WGS84-only 点表摄取；时区感知时间解析（ISO/epoch/naive+IANA 假设、DST fold=0 披露）；稳定排序（时间+输入序 tie-break）；重复时间策略（keep_all/first/last）；行级异常全计数不静默；稳定 source_row 身份（数组索引永不为身份）。
2. **WP2 轨迹构建**：显式可溯源分段（time_gap/speed_jump/distance_jump/zero_dt_conflict 断点带两端 source_row_id）；WGS84 测地距离/方位；区间估计语义（dt=0 → speed=None，无假零/inf）；驻留=锚定半径+时长+点数可验证阈值（漂移拆分披露、near-miss 计数、不自动合并）；质量诊断与清洗预览分离（应用=新 ref，原数据不可变）。
3. **WP3 区域时空事件**：enter/exit 只给观测推断区间（inferred=true）、cross 不伪造时刻、首修复已在内披露；closed/open 边界规则（covers/contains）；洞/多面原生；反经线解绕三帧 + O(1) bbox 预筛；跨日界线区域 typed 拒绝；unknown 谓词失败计数；区域汇总含 entities_zero_hit 诚实分母。
4. **WP4 地图表达与 UI**：MapSpec 2.3 additive `layer.trajectory`（MapSpecLayerTrajectory.v1）；轨迹线速度编码（实测 p05..p95 km/h 域、图例注明区间估计语义）、方向箭头、驻留点、事件标记；Studio 面板（map-studio-tab 挂载）：源选择→inspect 预填→参数→build→apply→统计/事件表/实体钻取（经共享 selection store 联动）；zh/en i18n。
5. **WP5 Pi 工具面**：`trajectory_inspect/build/apply/query` 四工具（ref_cursor 声明、typed args、科学证据块）；REST `/chat/sessions/{sid}/trajectory/*` 与 Pi 工具同链同引擎（J4 断言同 product_ref/同指纹/同证据）；`ApplyTrajectoryProductIntent` 原子落图（family 校验/源 STALE 拒绝/ref 活性/无半成品）；算法域包 4 描述符 + 能力包 5 描述符（ADR-0099 §34）。
6. **WP6 科学对照与规模**：公布测地常数独立 oracle；合成公交/双对象/跨日界线/稀疏越界/洞/零时冲突/噪声驻留/低质量 fixture；78 后端测试（含 4 条必过旅程）；规模实测 1k/10k/100k（events 100k = 361ms，bbox 预筛 32×）。

## 去重矩阵摘要（详 planning/GOAL-20261127-02-*/DEDUP_MATRIX.md）

五工作包启动审计均为 missing/partial；#2266 OD 流图为两点聚合非实体轨迹；temporal engine 族为通用时序无实体身份链；#2285 联动/selection 语义按契约复用不重写。排除项未触碰：通用 temporal engine、ST-DBSCAN、OD 图、时序媒体、拓扑编辑、数据供给、provider host、工程包、方向 04 发布、方向 05 打包。

## 测试与性能数据

- 后端：`tests/unit/moving_object` **78 passed**（含必过旅程 J1-J4）；MapSpec 契约族 155 passed；mutation registry 一致性 17 passed；parity corpus 74 passed（新 intent case 已入 corpus）；ADR 编号契约 3 passed
- 工具门禁：executable 613；conformance overall=fail 14 **与 master 基线持平（14=14）**，trajectory 工具 0 fail；descriptor coverage 的 capabilities 80% FAIL 为 master 既有（122 missing 无新增）
- 前端：16 个新 vitest 全绿（lib 投影 11 + 面板组件 5）；新文件 eslint/typecheck 清洁
- 规模：100k 修复 ingest 346ms / build 1.3s / dwell 180ms / events 361ms（详 TEST_MATRIX.md）

## 未测项 / 基线遗留（诚实披露）

- **未验证**：Playwright 浏览器 E2E journey（未新增 spec）；Pi 真实子进程 RPC（vendor/pi 未构建，本机 skip；J4 以 ToolDispatchService 同链断言替代）；real_services 容器 lane
- **master 既有失败（非本任务引入，均已在 pristine master 复现核实）**：parity golden `patch_label_authoring_set_reset` 漂移（本 PR 将 golden 对齐当前行为，已验证 master 同样漂移）；`test_tool_meta_contract` extension 文档检查×2；descriptor coverage capabilities 80%；前端 map-panel 族等 50 测试失败与 live-spec tiles 类型错误
- mypy 门禁只覆盖白名单文件；moving_object 手动 mypy 7 项发现已全部修复

## 迁移 / 兼容 / 回退

- MapSpec 2.3 纯 additive（缺失 trajectory 键 = 行为逐字节不变；identity upgrader；版本钉住测试同步）
- ApplyTrajectoryProductIntent 走单一 mutation registry（一致性门禁 + parity corpus 覆盖）；回退=不注册即可（无存量消费面）
- 无 DB 迁移；无依赖变更；无 CI 门禁修改

## 冲突边界

变更文件集中在：`app/services/moving_object/*`（新）、`app/tools/moving_object_tools.py`（新）、`app/api/routes/moving_object.py`（新）、`app/lib/gis/{algorithms,capabilities}/moving_object.py`（新）、MapSpec 2.3（schema + generated types + 版本钉住）、`app/tools/__init__.py`/`categories.py`/`app/main.py`/`app/services/mapspec/*` 最小接线、`frontend/{lib,components,messages,test}/moving-object|project`（新）+ i18n 登记。与并行线共享面只有 MapSpec 版本链与 mutation registry——均按既有 additive 流程并入。

## Review

- R1 独立逐行审查（独立上下文子代理，非作者）：见 planning/GOAL-20261127-02-*/REVIEW_R1.md（P0/P1 全部修复后回归）
- R2 复核：REVIEW_R2.md（重新 diff + 关闭证据 + 旅程重跑）
