# ModelOps V2 — 03 Progress

- Baseline: `8a33e3a5` → 分支 `feat/spatial-modelops-v2`
- 实现提交: `2027e6f1`（docs）/ `8f474397`（waves 1-8 主体）/ 本提交（修复+测试+加固）

## 完成状态（对照 02-plan 20 waves）

| 模块 | 状态 | 测试 |
|---|---|---|
| lib/modelops errors/capabilities/descriptor/fingerprint/metrics/resources | ✅ | unit |
| package_security（checksum/traversal/symlink/nested-archive/allow_pickle 双门） | ✅ | unit 14 用例 |
| registry（scope/revision/碰撞/C1 provider_ref 门/parity/持久化） | ✅ | unit 7 |
| loaded_cache（single-flight/refcount 驱逐/负缓存分流/unload 失败） | ✅ | unit 6 |
| providers：tiny_reference / tiny_detection / mock_gpu / promptable / temporal | ✅ | integration |
| remote_client（allowlist 定义性权力/逐跳重校验/bounded）+ fake_remote fixture | ✅ | integration |
| extension_adapter（独立池+信号量/超时 typed/不触 host 私有面） | ✅ | integration |
| compatibility（10 类 typed 失败 + allow_reproject 显式开关 R1-C2） | ✅ | unit 15 |
| planning（core/read 分离 R1-C3、确定性、metadata-only） | ✅ | unit 11 |
| preprocess（band select/归一化/nodata fill/pad；禁逐景统计） | ✅ | unit |
| stitching（概率空间 blend/nodata 权重置零/NMS/实例/嵌入 R1-m5） | ✅ | unit oracle |
| evaluation（IoU/F1/AP-11点/ECE/spatial-blocked/泄漏审计） | ✅ | unit oracle 9 |
| engine（ReprojectStage/有界批循环/取消/deadline/OOM 降批/memmap 融合/manifest/reuse） | ✅ | integration |
| reuse（fingerprint 精确匹配/owner 隔离/失效矩阵） | ✅ | integration |
| service facade + 10 个 modelops_* 工具 + 注册表挂表 | ✅ | integration |
| data_object kind +modelops_artifact（R1-M2 单行契约） | ✅ | 集成隐式 |

## 测试
- `tests/unit/modelops`: **98 passed**
- `tests/integration/modelops`: **37 passed**（含 3 条 vertical slices）
- ruff: All checks passed
- 回归 `tests/unit/extensions_platform`: 2383 passed / 5 failed —— **5 失败与 stashed baseline 完全一致**（win32 RLIMIT/socket 平台预存），零回归。

## 已知平台门（诚实边界）
- win32：extension worker RLIMIT 缺失 → 相关资源语义仅在 in-process mock 验证；
- remote JSON 协议 chip ≤128px（协议上限，如实声明）；
- temporal reference 为单窗口路径（tile-by-time 超出 typed PlanningError）。
