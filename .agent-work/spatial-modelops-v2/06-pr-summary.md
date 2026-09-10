# ModelOps V2 — 06 PR Summary（定稿）

## 标题
feat(modelops): Epic 12 — Spatial ModelOps & GeoAI Inference Platform V2

## 摘要
新增 learned-model（GeoAI）推理平面，闭环：模型注册 → 兼容性资格 →（显式重投影）→ tile 计划 → 资源规划 → provider 运行时 → 有界切片推理 → 任务级融合 → 产物/Provenance → 评估/复用缓存。ADR-0119。

## 提交序列（基于 origin/master `8a33e3a5`）
1. `2027e6f1` Phase A/B —— 基线审计（14 问）+ 架构冻结 + wave 计划
2. `8f474397` waves 主体 —— descriptor/registry/providers/包安全/兼容性/planner/preprocess/stitching/evaluation/engine/service/工具 + ADR-0119
3. `599dc144` 测试套件 + 修复（批循环/轴序/端点注入等）
4. `2b193d89` Review Round 1 修复（6C/7M/5m，correctness）
5. `595d946c` Review Round 2 修复（2C/10M/7m，perf/GPU/security/并发）

## 既有文件改动（最小冲突面）
- `app/tools/__init__.py`：+1 挂表行
- `app/services/lakehouse/data_object.py`：kind 封闭集 +1 成员 `modelops_artifact`（ADR-0119 R1-M2 显式声明的单行跨文件契约改动）
- `.env.example` / `tests/conftest.py`：MODELOPS_* 旋钮 parity（8 键）
- `tests/unit/test_registry_cache_preprocess.py`（本地内存守卫测试更新，M-9）

## 完成证明（3 条 production vertical slices，全部经 production 工具链路）
- **A 语义分割**：COG →资格→ tile plan → 有界推理（取消/OOM 降批/字节计数）→ 概率空间 blend → 分类+置信度+（可选概率栈）COG → DataObject 发布（renderable）→ InferenceManifest → reuse 精确命中/失效矩阵
- **B Promptable**：point/box/mask prompt → capability 二道门（qualifier + provider caps）→ 窗口推理（georef 平移）→ 掩膜+GeoJSON 产物 → manifest
- **C Remote/Extension**：allowlist-gated remote（fake server，逐跳 SSRF/流式字节上限）/ extension adapter（独立池+信号量+僵尸计数）→ 资源计划（externally_enforced）→ 可取消（typed 超时/协作）→ artifact → 评估 → manifest

## 质量
- **157 用例全绿**（tests/unit/modelops 100 + tests/integration/modelops 57，含 R1/R2 修复回归与 3 slices）
- ruff clean；extensions/gis/data_object 套件零回归（本机 11 个失败与 stashed baseline 逐一比对一致，均为 win32 平台预存）
- 双轮 subagent review + 架构挑战，共 40 findings 全部处置（05-review-findings.md）

## 安全要点
- 模型包只校验不执行（.pkl/.py/.so 黑名单、嵌套 archive 拒绝、np.load(allow_pickle=False) 唯一消费口、checksum 双验、register 可选 package_bytes 走全门）
- provider_ref 只解析进程内注册实例（无动态 import）；remote 默认拒绝 + operator allowlist（定义性权力）+ 逐跳 SSRF 重校验 + 流式字节上限
- 资源全有界：tile 数硬上限、批元素/字节预算、merge RAM→memmap→typed 三级、OOM 降批 ≤2、墙钟 deadline、并发信号量
- 复用 key 不含 secret；manifest 分节 redaction；owner 隔离贯穿 registry/reuse/产物；scope 值白名单防穿越
