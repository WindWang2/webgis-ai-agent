# PROGRESS — GOAL-20261127-02 移动对象轨迹、时空事件与地理过程分析

更新: 2026-11-28 (UTC)

## 已完成

### S0 启动审计（完成）
- master f34cb69dd；开放 PR=[]；开放 issue=[]；无冲突分支
- DEDUP_MATRIX：五工作包 missing/partial，增量确认
- worktree: /home/kevin/projects/webgis/webgis-moving-object-events-20261127
- 分支: zcode/goal-moving-object-events-20261127

### WP1 数据合同（完成，提交 bac06ea31/0138ce294）
- contracts.py: IngestConfig/SegmentationConfig/DwellConfig/RegionEventConfig/TimeWindow（pydantic，WGS84-only、时区感知窗口、ref_cursor 声明）
- ingest.py: 稳定排序（time+order）、重复时间策略、IANA tz 假设（DST fold=0）、行级异常计数、派生 source_row 身份
- 测试: tests/unit/moving_object/{test_contracts,test_ingest}.py

### WP2 轨迹构建/运动量/停留/质量（完成）
- motion.py: pyproj.Geod 测地距离/方位；dt=0 → speed=None（无假零/inf）
- builder.py: 显式断点（time_gap/speed_jump/distance_jump/zero_dt_conflict）、SegmentCapExceeded、TrajectoryBuild（runs+intervals+线要素）
- dwell.py: 锚定半径游程 O(n)、near_miss 披露、无自动合并
- quality.py: 诊断与清洗预览分离（联合行计数；应用=新 ref）
- 已发布常数 oracle: 1°经度=111319.49m、赤道→极点=10001965.73m、反经线短弧

### WP3 区域时空事件（完成）
- events.py: 决策表（enter/exit 推断区间、cross 只给区间、首修复已在内披露）、closed/open 边界规则、洞/多面、反经线解绕三帧、跨日界线区域 typed 拒绝、unknown 计数
- 汇总: 区域级行含 entities_zero_hit 诚实分母
- 修复 bug: 首修复 continue 跳过状态更新（测试捕获）

### WP5 后端（完成，提交 afc5b922c）
- service.py: inspect/preview/apply/query 双入口单一编排（ref 解析+指纹缓存+STALE 拒绝+有界 envelope）
- moving_object_tools.py: trajectory_inspect/build/apply/query（ref_cursor、词表合规、科学证据块）
- routes/moving_object.py: /chat/sessions/{sid}/trajectory/*（require_owned_session）
- MapSpec: ApplyTrajectoryProductIntent + handler + registry + Body codec；一致性测试与 parity corpus 同步
- 算法域包 4 描述符 + 能力包 5 描述符
- 门禁: descriptor coverage/conformance fail 数与 master 持平（14=14）

### MapSpec 2.3 契约（完成，提交 253de298b）
- MapSpecLayerTrajectory.v1 additive + upgrader + KNOWN_VERSIONS/LATEST 2.3
- ts_projection 再生成 + 版本钉住测试机械更新（155 passed）

### WP6 规模验证（后端完成，提交 3045df062）
- 实测（本地 dev, 20 实体）: 1k: 4/10/1/17ms, 10k: 34/301/15/73ms, 100k: 346/1304/180/361ms（ingest/build/dwell/events）
- bbox O(1) 预筛优化: events 100k 11.6s→0.36s（32×）

### WP4 前端（代码完成，测试绿）
- frontend/lib/moving-object/trajectory-product.ts: 唯一网络边界 + 纯投影（11 vitest）
- frontend/components/moving-object/trajectory-panel.tsx: 源选择→inspect 预填→参数→build→apply（CAS）→统计/事件表/实体钻取（5 vitest）
- 挂载: map-studio-tab.tsx StudioSection；i18n movingObject 命名空间 zh/en
- MapSpec 2.3 types.generated 同步

## 测试现状
- 后端: tests/unit/moving_object 74 passed（含 scale）；mapspec/parity/registry 契约测试绿
- 前端: 16 新测试绿；typecheck 仅剩 master 既有失败（live-spec.test.ts tiles）

## 基线遗留（非本任务，已在 master 验证）
1. tests/cartography/test_mutation_parity_corpus.py patch_label_authoring_set_reset golden 漂移（golden 已对齐当前 master+branch 行为）
2. tests/test_tool_meta_contract.py extension_example_names_exist / extension_routes_distribution（index.mjs 文档面）
3. check_tool_descriptor_coverage capabilities 80% FAIL（122 missing，与我改动无关）
4. frontend live-spec.test.ts tiles 类型错误

## 待办
- 必过旅程 1-4 后端验证脚本 + Pi RPC 同链验证
- R1 独立 review（上下文隔离）+ 修复
- R2 复核 + PR

## 追加（2026-11-28 R1 前状态）

- 必过旅程 J1-J4 全过（tests/unit/moving_object/test_journeys.py，78 总绿）
- 旅程抓到并修复真 bug: oracle 腿采样失真（4m/30s→驻留误判）、constant 速度域 interpolate stops 非严格递增（MapSpec 校验器拒绝）
- MapSpec 2.3 bump 回归修复: component_graph 版本钉住、carto_ir corpus manifest+5 fixture 期望
- cartography 全目录: 4073 passed / 5 failed（全部为 pristine master 既有，逐一对照核实）
- 前端: 16 vitest 绿；typecheck 仅剩 master 既有 live-spec tiles；全量 50 失败经 master parity worktree 核实为基线
- diff: 67 files, +7914/-74; git diff --check 干净

## R1 闭环（2026-11-28）

- R1 审查（独立子代理）：科学核心 sound；2×P1 + 4×P2 + ~14×P3
- 全部修复于 commit 1c103c518，新增 test_r1_closures.py 11 用例，J2 强化家族替换断言
- 修复后复验: moving_object 89 passed；契约族 125 passed；conformance fail 14=master 基线；前端 16 passed；typecheck/eslint 新代码清洁
- R2 复核子代理已启动（finding→closed 核验 + 修复无新问题 + 旅程重跑）

## 终验（2026-11-28）

- origin/master 仍 f34cb69dd，本分支无分歧；开放 PR #2290（零交集）/#2291（5 个共享注册文件追加行，无逻辑冲突）
- 终验回归 192 passed；R1/R2 全闭环；git diff --check 干净；冲突标记扫描干净

## 交付（2026-11-28）

- 分支已推送: zcode/goal-moving-object-events-20261127（16 commits, origin/master f34cb69dd 无分歧）
- **PR 已创建: https://github.com/WindWang2/webgis-ai-agent/pull/2292**（base master）
- 完成定义核对: 必过旅程 1-4 真实跑通（tests/unit/moving_object/test_journeys.py）；五工作包代码+测试+可见入口（REST/Pi/UI 三面）；R1×14 项与 R2×6 项无未解决 P0/P1/P2；master 与其他 worktree/分支零改动；未自动合并、未删任何分支/worktree
