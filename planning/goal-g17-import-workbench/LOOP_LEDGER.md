# LOOP_LEDGER.md — 迭代账本

## 轮次记录

### R0（2026-11-10）侦察 + 建树
- 基线 SHA 816705218；worktree/branch 建立；3 份侦察报告（后端链/工具面/前端面）。
- DEDUP_MATRIX：G17 核心 missing；开放 PR 无冲突；#2032 仅薄挂载点。
- 决策 D1-D8 落 PLAN.md。
- 下一步：WP02 schemas/models。

## 环境事实
- 测试解释器：`python`（conda 3.13.9，依赖齐全）。pytest 需在 worktree 根执行。
- 重型阶段资源纪律：默认串行（本任务无编译需求；pytest -x -q 定向子集）。

### R1（2026-11-10）后端纵向切片打通
- 新增域：app/services/data_import/{models,budgets,staging,discovery,interpretation,preview,commit,service}.py
- 接线：main.py 路由 + tools/_TOOL_MODULES + data_parser 公共守卫别名 + requirements(.lock) openpyxl
- API：app/api/routes/data_import.py（9 端点）；工具：app/tools/data_import_tools.py（5 工具，未测）
- 测试：tests/data_import/test_vertical_slice.py 3 passed（124s）
  - 覆盖场景 2（双 GeoJSON）、幂等（场景 14 机制）、不同解释→不同产物（场景 13 机制）、归属 NOT_FOUND、unsupported 诚实披露
- 修复记录：_load_owned async 化、shapely 2.x __geo_interface__、member container_ref 显式定位
- 下一步：容器 fixture 矩阵（GPKG 三层/ZIP 双套/CSV 变体/XLSX）+ 取消/恢复 + 工具经 dispatch 测试 + UI

### R2+R3（2026-11-10）容器矩阵 + 表格矩阵全绿（15 passed）
- 容器（5）：GPKG 三层选二（未选层无产物、线层真实 32650→4326 转换）、ZIP 双套 basename 归组
  （字段不串用、孤儿 .shp 披露）、KML、缺 .prj 声明 CRS（不声明→CRS_UNCONFIRMED；声明→转换成功）、
  partial 失败→修正重试（成功成员 ref 复用 duplicate=True）
- 表格（7）：Easting/Northing+投影 CRS（场景 5）、旧计划版本拒绝（场景 12）、分号+gb18030+引号（场景 6）、
  前导零+2^53+1 长整型保真（场景 7，>2^53 显式 number 也保真为字符串）、WKT 混族+非法行定位（场景 8）、
  纯表 ref:table-（场景 9）、XLSX 三 sheet/空表/公式无缓存（场景 10）
- 产品决策：勾选未配置成员 → save_plan 自动落默认解释（save_plan 为显式确认点）
- 修复：discovery `_MEMBER_SAFE_RE` 定义、read_only Workbook 无 context manager（discovery+interpretation 两处）
- 既有噪声（非本任务）：dataset_semantics store 在 Windows 因文件名含 ':' 写失败（warning，既有行为）
