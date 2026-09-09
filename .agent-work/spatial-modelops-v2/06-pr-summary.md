# ModelOps V2 — 06 PR Summary（草稿，review 后定稿）

## 标题
feat(modelops): Epic 12 — Spatial ModelOps & GeoAI Inference Platform V2

## 摘要
新增 learned-model（GeoAI）推理平面：模型注册表（身份/版本/checksum/owner scope/parity）、typed provider 协议与 5 类参考实现 + remote/extension 通道、兼容性资格、tile 推理运行时（core/read 分离、有界批、取消、OOM 降批）、任务级 merge/postprocess（分割/检测/实例/嵌入/时序/promptable）、评估平台（IoU/AP/ECE + 泄漏防护）、推理 manifest/provenance、fingerprint 精确匹配的复用缓存、10 个 agent 工具。ADR-0119。

## 变更面
- 新增 `app/lib/modelops/`（14 模块，纯契约）、`app/services/modelops/`（运行时）、`app/tools/modelops_tools.py`
- 既有文件改动：`app/tools/__init__.py`（+1 挂表行）、`app/services/lakehouse/data_object.py`（kind 封闭集 +1 成员 `modelops_artifact`，ADR-0119 R1-M2 显式声明）、`.env.example`（+MODELOPS_* 旋钮）
- 测试：`tests/unit/modelops`（98）+ `tests/integration/modelops`（37，含 3 条 vertical slices）

## 完成证明（3 条 production vertical slices）
- A 语义分割：COG→资格→tile plan→有界推理→overlap blend→分类+置信度 COG→DataObject 发布（renderable）→manifest→reuse 精确命中/失效矩阵
- B Promptable：point/box prompt→capability 门→掩膜+GeoJSON 产物→manifest
- C Remote/Extension：allowlist-gated remote（fake server）/extension adapter→资源计划（externally_enforced）→可取消→artifact→评估→manifest

## 质量闸
- 135 ModelOps 测试全绿；ruff clean；extensions 套件零回归（5 个 win32 预存失败与 baseline 一致）
- 双轮 review：R1 correctness（Subagent-A）/ R2 perf+security（Subagent-B），findings 与处置见 05-review-findings.md

## 安全要点
- 模型包只校验不执行（.pkl/.py/.so 黑名单、嵌套 archive 拒绝、np.load(allow_pickle=False) 唯一消费口、checksum 双验）
- provider_ref 只解析进程内注册实例（无动态 import）；remote 默认拒绝 + operator allowlist（定义性权力）+ 逐跳 SSRF 重校验
- 复用 key 不含 secret；manifest 经统一 redaction；owner 隔离贯穿 registry/reuse/产物
