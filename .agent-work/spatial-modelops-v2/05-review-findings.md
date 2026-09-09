# ModelOps V2 — 05 Review Findings

## Round 0 — Subagent-A 架构挑战（2026-09-10，verdict=revise，全部采纳）

| 级别 | ID | 摘要 | 处置 |
|---|---|---|---|
| BLOCKER | B1 | input_content_sha256 语义未绑定，RasterMetadata.fingerprint 是"identity 非缓存键" | 修订：唯一口径 = DataObject merkle / 全内容 sha256；禁 metadata fingerprint；已写入 fingerprint.py 契约 |
| CRITICAL | C1 | provider_ref 动态 import = 任意代码执行 | 修订：provider_ref 只解析为 ProviderRegistry 已注册实例 id；未注册 typed 拒绝 |
| CRITICAL | C2 | 重投影无执行层归属（隐藏重投影风险） | 修订：显式 ReprojectStage（Qualifier 后/Planner 前），warp 参数+warp 内容摘要进指纹 |
| CRITICAL | C3 | edge pad 与 read_window 越界硬拒冲突 | 修订：TilePlan 携带 core_window+read_window(clamp)+pad+fill_value；pad 只在 numpy 层 |
| MAJOR | M1 | ModelDescriptor 与 chat 域撞名 | 修订：更名 GeoModelDescriptor |
| MAJOR | M2 | artifact kind 封闭集无 modelops 产物位 | 修订：新增 "modelops_artifact" kind（单行跨文件契约改动，ADR 声明） |
| MAJOR | M3 | fingerprint 缺 5 字段（provider 身份/种子策略/环境/nodata/统计来源） | 修订：字段表落定 + 逐字段失效单测 |
| MAJOR | M4 | loaded cache 缺 single-flight/驱逐-引用交互/负缓存毒化 | 修订：single-flight + refcount==0 驱逐 + 负缓存按 permanent/transient 分流 |
| MAJOR | M5 | worker 通道 68MiB 帧上限 + 同步 call 取消延迟 | 修订：extension 批预算 min(预算,64MiB)；独立池+信号量；cancel 上界=call_timeout |
| MAJOR | M6 | 不变式2被 in-process extension 路径证伪 | 修订：两信任域措辞 + adapter 静态架构测试 |
| MAJOR | M7 | chunk.py 无 overlap，"复用 window 语义"不成立 | 修订：TilePlanner 为推理专属分区权威；经典迭代器回归断言 |
| MAJOR | M8 | fake server 与 SSRF 硬门冲突；client 选型未定 | 修订：allowlist 定义性权力 + httpx follow_redirects=False 逐跳重校验 |
| MINOR | m1 | 工具 capability id 词表缺口 | 复用既有 id（image_segmentation 等），不新增 |
| MINOR | m2 | VRAM 漂移/外部强制域/unload 失败终态 | PerfCounters 漂移字段 + externally_enforced 标注 + poisoned |
| MINOR | m3 | Windows 退化点（select/RLIMIT/pytest timeout） | 测试平台门标注；OOM 仅 mock_gpu；worker 依赖 call_timeout |
| MINOR | m4 | .npy object array = pickle 执行；嵌套 archive | load_weights_array 唯一消费口 + 嵌套 archive 拒绝（已实现） |
| MINOR | m5 | blend/nodata/context 语义契约缺失 | 概率空间累加 + nodata 权重置零 + context 裁剪 + nodata 接缝 oracle |

## Round 1 — Subagent-A correctness review（待实现完成后）

（占位）

## Round 2 — Subagent-B perf/GPU/security review（待 Round 1 修复后）

（占位）
