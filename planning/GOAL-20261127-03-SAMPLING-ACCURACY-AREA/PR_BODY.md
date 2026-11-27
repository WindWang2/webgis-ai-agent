# PR 正文（GOAL-20261127-03 概率抽样调查、专题图精度核查与面积估计 Harness）

## TL;DR
在既有 Pi 工具面/ModelOps 像元评估之外，补齐**概率抽样调查闭环**：分类栅格→
冻结抽样框（等面积核查）→分层 SRS 设计（Olofsson 2014）→可复现选样（纳入
概率/权重/预抽替补）→独立参考标签调查（盲标/CAS/争议裁定）→**设计一致
估计**（加权误差矩阵、OA/UA/PA、类别面积与 95% CI）。Pi 工具与 REST/UI
双入口共用同一领域服务。无概率设计或无参考标签时拒绝总体推断（诚实
not_evaluated），不把描述性准确率伪装成总体精度。

## Baseline / Pi pin
- base：origin/master `f34cb69dda6150ee4135e6d2a476708149d9eef7`（启动审计
  与参考快照一致）；vendor/pi gitlink `ef4a1cfc`（Pi v1.6.1，2026-11-25
  官方 latest——本线只消费现有运行能力，未改 vendor/pi、未改 host/路由）。
- 启动审计：开放 PR/issue = 0；#2272–#2289 均已合并并纳入基线；
  DEDUP_MATRIX 见 planning/（`area_adjusted`/`inclusion_probability`/
  `accuracy_assessment` 零生产命中确认）。

## 新能力（工作包对照）
- **WP1 设计合同**：`app/lib/survey_sampling/design.py`——分层 SRS（不放回）
  唯一可证设计；等额/比例/Neyman 分配；Eq.13 目标 SE 反推；稀有类不跳过；
  设计冻结 digest（canonical JSON+sha256）。
- **WP2 抽样框与样点**：`frame.py`——分块读+取消检查点、uint32 索引、
  **测地面积核查**（跨框采样，>1% 变化 typed 拒绝并建议等面积 CRS，
  经纬度像元数不当面积）；`selection.py`——层独立 SRSnwr、π=nh/Nh、
  w=1/π、uuid5 稳定 sample_id、预抽替补（非响应按序顶替、原因披露）。
- **WP3 参考闭环**：`service.py`——调查记录状态机（未访问/待核查/已标注/
  争议/不可判/拒绝）、append-only 版本化（历史保留）、批量导入（同类幂等/
  异类→disputed→裁定终版）、盲标默认（解释员不可见预测类）、revision CAS。
- **WP4 设计一致估计**：`estimation.py`——加权误差矩阵（行=地图类=层，
  行和已知 ⇒ UA=qi 精确式）、OA/UA/PA δ 方法方差、面积=比例×总面积、
  全查方差归零、n_h<2 不可估披露、CI 越界双披露（原始+裁剪）、非响应
  越限拒绝总体推断。
- **WP5 双入口**：8 个 typed Pi 工具（survey_frame_preview/design_preview/
  create_design/samples/record_reference/progress/estimate/get_estimate，
  descriptor 证据挂载）+ 12 个 REST 端点 + 前端工作台面板（五步向导：
  建框→预览→创建→核查上图→估计报表），同一 service、同一权限、同一状态。
- **WP6 审查**：独立 Agent 数学 R1（见 REVIEW_R1.md）+ R2 复审；覆盖模拟
  （400 次重复：OA 覆盖 0.968、SE/SD=1.045）；全查/手算 oracle/独立性
  交叉核对。

## 与相邻线边界
- 02 线（轨迹）、04 线（图符/发布）、05 线（工程包）、01 线（Pi host）零触碰；
- ModelOps 像元全量评估不改定义（估计器独立；全查时二者对照锚定）；
- G39 raster_sampling（栅格取值采样）不同域不触碰；
- 共享文件接线最小化：main.py（2 行）、tools/__init__.py（2 行）、
  tool_catalog.py（域关键词 1 项）、algorithms/__init__.py、
  capabilities/__init__.py、method_references.py（+olofsson2014）。

## 测试与性能（实测）
- 52/52 调查域测试全绿：手算 oracle 11、覆盖模拟 2、设计 12、选样 6、
  抽样框 7、必过旅程 4、参考闭环 3、Pi 工具 2、REST 2、性能 2；
- 必过旅程 1-4 全部真实跑通（合成数据、无 mock 被测语义）；
- 性能（实测打印进 test_survey_perf.py stdout）：1e6 像元 frame=0.06s、
  draw=14ms、estimate=0.4ms、峰值 RSS+3MB；4e10-cell 守卫拒绝 0.1ms；
  3.84M 像元分块取消无半成品（J4）。
- 契约层回归：与 master 失败集逐项一致（2 tool_meta + 2 perf_coverage，
  基线既有）；docs drift 修复（master 红→本分支绿）。

## 未测项 / 基线遗留（诚实披露）
- 全量后端套件未跑（focused+契约层绿；CI 分片执行）；
- master 基线既有红项（与本 PR 无关，逐项核对过）：GIS runtime manifest
  启动 strict 闸（兄弟线工具引用未注册能力 map_design/gis_scenario_harness/
  cartography_paper_scale_*）；execution-catalog manifest 重生成后诚实暴露
  同一批 dangling（shape 测试红，master 旧文件掩盖）；debt_ratchet
  god_modules×4；ruff 26 处；GIS_MANIFEST_STRICT=0 先例用于全 app 路由测试；
- Playwright e2e 未新建：双入口语义已由 Pi dispatch 真跑 + REST TestClient
  + 面板 vitest 覆盖；
- real_services/browser lanes 未跑（CI nightly 专属）。

## 迁移 / 兼容 / 回退
- 无 DB 迁移（调查状态走 DATA_DIR 原子 JSON/JSONL + revision CAS，
  先例 field_compute receipt / modelops evaluations 目录）；
- 全部新增文件 + 6 处共享文件加性接线；回退 = revert 本分支即可，
  无数据格式耦合。

## R1 / R2
- REVIEW_R1.md：独立审查员（非实现作者）从 Olofsson 定义手推对照 +
  权限/并发/诚实性/回归清单，P0-P2 修复记录；
- R2：重读最新 diff、复跑关键 journey 与契约层、冲突检查见下方"合并顺序"。

## 合并顺序与冲突边界
- 执行时本地在研分支：zcode/goal-pi-adaptive-execution-20261127（01 线）、
  zcode/goal-moving-object-events-20261127（02 线）——与本线无文件交集
  （01 线独占 vendor/pi/host 层，本线未触碰）；
- PR 前重新 fetch 核对 origin/master 与 changed files 交集；如 master 有
  新合并则 rebase 复验。
