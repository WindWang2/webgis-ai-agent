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

## Round 1 — Subagent-A correctness review（2026-09-10，HEAD 599dc144，verdict=revise，全部修复）

| 级别 | ID | 摘要 | 处置 |
|---|---|---|---|
| CRITICAL | C-1 | MODELOPS_* env 未钉 conftest → 全量套件 parity 红 | conftest `_ENV_BASELINE` +8 键（parity 测试绿） |
| CRITICAL | C-2 | prompt/temporal 不进 reuse key → 异 prompt 命中同缓存 | postprocess_payload 增 prompt_geometry/temporal 具名字段 + 失效单测 |
| CRITICAL | C-3 | 重投影用源 CRS bounds ÷ 目标米分辨率 → 1px 产物；地理 CRS 度当米 | default-transform 先行 + 目标 bounds 重算；`_meters_per_pixel` 地理 CRS → 0 |
| CRITICAL | C-4 | 检测全局坐标用 core 原点（错 half_ctx）+ 批粒度错配 | merge 用 read_window 原点；engine 按 batch_index 展开 per-tile；多 tile 手算 oracle |
| CRITICAL | C-5 | acquire 后 warmup/mkdir 在 try 外 → refcount 泄漏 | try 上移覆盖全部后置路径 + monkeypatch 回归 |
| CRITICAL | C-6 | scope 值无白名单（穿越）+ 跨 owner 同身份碰撞使 registry DoS | 值 charset 正则（registry+reuse）；身份键 = (scope,id,version)；parity/重载用例 |
| MAJOR | M-1 | instance 批粒度丢 chip + 零覆盖 | per-chip 展开 + TinyInstanceProvider + seeds + 端到端 |
| MAJOR | M-2 | promptable 窗口产物 georef 错位 | write_raster_output window_origin + georef oracle（122/40） |
| MAJOR | M-3 | mask prompt 静默忽略 | provider 读 extras["prompt_mask_arrays"]（窗口切片数组）+ 全 False prior 测试 |
| MAJOR | M-4 | 生产 accumulator 与 oracle 两套语义（m5 未兑现） | 共享 _core_weights + nodata 权重置零 + 概率导出 + uncovered=255 |
| MAJOR | M-5 | 默认取消键=model_id 并发串台 | run_key 注入 request（run_id=取消键），冲突 typed；工具层去 model_id 键 |
| MAJOR | M-6 | remote 先缓冲后查字节上限 | client.stream 逐块累计 + 中途超限即断 |
| MAJOR | M-7 | M6/M7 声明的静态验证缺失 | adapter 隔离源码扫描 + 经典迭代器零耦合断言 |
| MINOR | m-1 | padding_mode 声明被忽略 | np.pad 三模式实装（reflect 超 dim-1 typed 降级 constant） |
| MINOR | m-2 | band_order 有声明无解析 | build_plan 按源波段名解析索引；engine 读 descriptions |
| MINOR | m-3 | "最新版本"按字典序 | seq 全局单调，resolve 按 seq |
| MINOR | m-4 | 重投影中间产物无清理 | run finally best-effort unlink |
| MINOR | m-5 | 测试卫生/键锁驻留/概率抽验缺失/伪 ECE | 全项修复（ECE 仅 confidence_path 提供时计算）

## Round 2 — Subagent-B perf/GPU/security review（待 Round 1 修复后）

（占位）
