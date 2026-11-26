# SCOPE — GOAL-20261126-02 任务驱动数据覆盖与供给

## 方向一句话

针对具体任务（区域、时段、统计口径、尺度、许可证），把已有各层（目录/资格/ADS/OGC/预检）
拼成**有证据的角色级数据覆盖/缺口判定与实际供给体验**——不是再做目录、适配器、
融合算法、取数编译器或数据清洗。

## 文件所有权（首选，源码调查后校正）

### 本线新增（核心）
- `app/services/task_data_supply/`（命名待去重确认）— 任务需求投影、覆盖矩阵、供给计划编排
- `tests/unit/task_data_supply/` 或仓库惯用测试布局
- 必要的少量 tool 接线（`app/tools/` 内新增或最小扩展）

### 本线兼容投影（小改）
- `app/tools/data_discovery.py` — 仅必要接线
- `app/tools/catalog_discovery_tools.py` — 仅必要接线
- `app/services/gis_harness/workflow_v4/acquisition.py` — 兼容投影（不破坏现有声明语义）

### 只读消费（禁止修改）
- `app/services/data_catalog/`、`app/services/data_fabric/`（ADS/planning/retrieval 全部只读调用）
- `app/services/dataset_semantics/`（QUALIFICATION_STATES、preflight、scope staleness）
- `app/services/gis_harness/data_qualification.py`（五态资格）
- MapSpec、融合(data_fusion_authoring)、Pi runtime、全局 query optimizer、G46 探索核心
- vendor/pi gitlink

## PR / 分支去重记录

- 开放 PR = 0（2026-11-27 核查）
- #2272 方法实验、#2273 多源融合与冲突审阅、#2274–#2280、#2281/#2282 已合并 → 不重复实现
- 本分支：`zcode/goal-task-data-coverage-supply-20261126`（master 59eb3f527）

## 契约边界

- 复用：QUALIFICATION_STATES 五态、GISDatasetDescriptor、ADS request/plan、
  task_scope staleness、preflight 码、artifact/ref/descriptor/provenance、
  durable jobs、ToolRegistry 唯一注册
- 不新增资格枚举；不复制 ADS；不写第二个取数执行引擎；不另造数据目录/事实源
- 未知 ≠ 不存在：覆盖评估必须区分 不存在/未搜索/无权限/服务失败/未覆盖/只展示/部分覆盖/版本不匹配
- 展示类来源（WMS/tiles）不得进入数值统计资格

## 子代理边界（每 WP 执行时填写）

| WP | 开发 | 独立审查（未写被审文件） |
|---|---|---|
| WP1 | 主 Agent | A:科学语义 B:安全/竞态 |
| WP2 | 主 Agent | A:CRS/时间 oracle |
| WP3 | 主 Agent | B:ADS/授权/幂等 |
| WP4 | 主 Agent | C:UI/Agent 旅程 |
| WP5 | 主 Agent | A+B |
| WP6 | 修复 | A/B/C 全员 R1+R2 |
