# ModelOps V2 — 02 Plan（waves 与验收）

每个 wave：实现 + 测试 + 本地验证 + 独立 commit。测试不下载任何模型/数据。

| Wave | 内容 | 主要产物 | 验收 |
|---|---|---|---|
| 1 | errors/capabilities/metrics/config | lib 骨架 + typed 词表 | unit 契约测试 |
| 2 | descriptor.py + fingerprint.py | ModelDescriptor V2 + InferenceFingerprint | 校验器/碰撞/序列化 roundtrip |
| 3 | package_security.py | 包校验门（不执行） | traversal/symlink/size/bad checksum 单测 |
| 4 | registry.py + seeds.py | ModelRegistryStore + tiny seeds | scope/revision/parity/持久化 |
| 5 | providers/base.py + tiny_reference + tiny_detection + mock_gpu | Protocol + 3 providers | lifecycle/cancel/确定性 oracle |
| 6 | promptable_reference + temporal_reference | prompt/时序参考实现 | prompt 能力门/时序契约 |
| 7 | remote_client + fake server fixture | remote provider + allowlist 策略 | SSRF 拒绝/超时/字节上限/fake server |
| 8 | compatibility.py | qualifier | 10 类兼容失败矩阵 |
| 9 | planning.py | TilePlanner | 确定性/边缘/virtual huge metadata-only |
| 10 | preprocess.py | PreprocessPlan + 纯变换 | band order/归一化/nodata 参数入指纹 |
| 11 | engine.py 核心（tile loop + cancel + progress + counters） | 推理引擎 | mid-batch cancel/lazy 证明/bytes 计数 |
| 12 | stitching.py（segmentation/detection/instance/embedding） | merge/postprocess | 4 oracle（接缝/边缘重复/实例身份） |
| 13 | resources.py + resource_plan.py + loaded_cache.py | 资源规划 + 模型缓存 | OOM 降批/驱逐/竞争/negative cache |
| 14 | manifest.py + artifacts.py | InferenceManifest + 发布 | provenance 字段/renderable COG |
| 15 | evaluation.py + evaluation_service.py | 评估平台 | IoU/AP oracle/泄漏防护 |
| 16 | reuse.py | reuse cache | 7 类失效/owner 隔离 |
| 17 | extension_adapter.py | 扩展 provider 接入 | worker/in-process 双模 + offload |
| 18 | service.py + modelops_tools.py + __init__ 挂表 | 10 工具 facade | 工具级 integration |
| 19 | 加固：security/perf/文档/ADR-0119 | 加固 + ADR | 全测试 + ruff + 性能结构断言 |
| 20 | Review R1/R2 修复 + rebase + PR | 交付 | 三垂直切片重跑 |

（Epic 建议 24-32 waves 归并为 20 个可验证 wave；每个 wave 覆盖 epic 原子项，无缩水——原子清单见 01-architecture §3/§6。）

## 验证命令
- `python -m pytest tests/unit/modelops tests/integration/modelops -q`（新增域）
- `python -m pytest tests/unit/extensions_platform -q`（seam 不回归）
- `python -m ruff check app/lib/modelops app/services/modelops app/tools/modelops_tools.py`
- heavy 栅格测试依赖 rasterio/numpy（本地已装，等同 CI heavy lane）。

## Subagent 预算
- Subagent-A：架构挑战（本阶段）+ Round 1 correctness review（同一 agent，SendMessage 续跑）。
- Subagent-B：Round 2 perf/GPU/security review。
- 总计 = 2，无第 3 个。
