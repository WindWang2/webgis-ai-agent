# Acceptance — goal-gis-topology-authoring-20261125

对照任务书「最低验收」逐条自证（R1 冷审将独立复核）。

## 1. 面共享边编辑后两面无重叠/缺口（基线违规披露）；邻面同步原子提交；拒绝零写入；undo 恢复真实字节
- 双侧精确一致：test_shared_topology.py::TestMoveSharedVertex::test_moves_both_sides_of_shared_edge_node
  （两侧 moved 坐标逐字节相等 + 环闭合保持）；insert 两侧插入点同一坐标。
- 无新增重叠：共享编辑后 A∩B 面积 0（test_shared_edge_stays_consistent_after_reshape，
  shapely intersection 断言）。
- 基线披露不阻断：test_baseline_violation_disclosed_not_blocking（baseline_total≥1、
  existing_violations≥1、new_violations=0、committable=True）。
- 新增违规阻断：test_topology_new_violation_blocks_commit（线网自触经声明规则阻断，
  decision 端点拒绝非 committable plan）。
- 原子提交：test_full_journey_atomic_commit_and_undo（单 journal entry，两面同步，
  changed_wfids=[A,B]）。
- 拒绝零写入：test_reject_writes_nothing。
- undo 字节恢复：同测试（undo 后 strip _wfid 逐字节相等 —— 物化是一次性身份升级，
  ADR-0277 D2）+ WP6 逆元恒等（五 op apply→invert→apply==原字节）。

## 2. 线网受控连接；不同层级不误连；错误输入可靠阻断
- 受控连接：test_connect_within_tolerance_moves_endpoint（容差内移端点）；
  test_beyond_tolerance_fails_closed（no_snap_candidate）。
- 层级守卫：test_level_conflict_blocks_grade_separated_connect（connect_level_conflict）
  + test_level_guard_splits_same_coordinate_nodes（同坐标不同层级=不同节点，显式
  vertex ref 才能寻址）。
- 容差歧义：test_tolerance_ambiguity_fails_closed（shared_node_ambiguous + 候选披露）。
- 容差上限：MAX_SHARED_VERTEX_TOLERANCE_M=100 / MAX_CONNECT_TOLERANCE_M=50，
  超限 unit_required（test_tolerance_bound_enforced、test_invalid_tolerance_rejected）。
- CRS：数据集恒 4326（几何算子 _common_guards 拒绝显式非默认 crs 成员）；op 坐标
  均为 GeoJSON lon/lat（schema 类型校验）。
- 超预算：顶点预算（test_vertex_budget_typed_rejection，index_too_large）、
  触及面预算（test_touched_feature_budget，target_limit_exceeded）、reshape 路径
  1024 上限、journal 256KB 闸（既有）。

## 3. UI 与 Pi 同链；≥3 用户旅程 + 负例
- 同一链路证据：Pi 工具（vector_feature_edit_preview/commit）与 REST 走同一
  build_and_store_plan / commit_plan / 决策端点；三只读工具与 REST 端点共用
  service 函数；策略闸/确认门共享。
- 旅程：
  1) 面边界：REST 事务链（preview→approve→两面同步→undo→redo）+ 前端共享拖拽
     组件测试（move_shared_vertex 提交、高亮、Escape 零写取消）。
  2) 线网：connect_endpoint 工具/REST（j50 SSE 面为面编辑；线网由
     test_shared_topology.py TestConnectEndpoint + flow 测试覆盖）。
  3) 拒绝/撤销：j50 第二旅程（reject→rejected 卡片，仅 reject 决策）+
     REST undo/redo 事务链。
  - 负例：stale CAS 409、越容差拒绝、层级冲突拒绝、新增违规阻断不可 approve、
    重复 approve 幂等零二次写。

## 4. 权限/词表/预算纪律
- 三新端点均 Depends(require_owned_session)；404 不泄存在
  （test_unknown_dataset_404）。
- 词表 additive：SHARED_GEOMETRY_OPS 进 EDIT_OPS；schema Literal 同步；
  plan._validate_op_shapes 形状校验；finding codes 封闭集扩展 4 码。
- 无第二写路径：shared op → derive_changes deltas → commit_plan 唯一执行路径。
- safe 分类不变：坐标类永不 safe（test_move_shared_vertex_plan 断言 safe=False）；
  Pi 提交必经 CONFIRMATION_REQUIRED 或用户卡。

## 5. 测试证据（本机实测命令与结果）
- pytest tests/unit/feature_edit/ → 187 passed（含 42+14+8 新例）— venv
  python3.12, shapely 2.2.0, pyproj 3.8.0。
- ruff check（改动域文件）→ All checks passed。
- pnpm vitest run（前端触碰测试）→ 29 passed；全套件 5743 passed / 3 failed
  （既有，stash 复核）；typecheck 触碰文件零错误；lint 零错误。
- pnpm exec playwright test j50 → 2 passed（mock 档，E2E_PORT=3331）。
- tests/unit/feature_proposals + topology_workbench/ → 56 passed / 2 failed
  （既有 event-loop 污染，基线复现确认）。
- 邻域回归：feature_edit 全套、topology_workbench 97 passed、feature_proposals
  56 passed（独立运行）。
