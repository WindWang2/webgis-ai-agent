# CAPABILITY_MATRIX — before/after 能力矩阵

图例：`implemented`（本地可复现证据）/ `not-supported`（typed 拒绝）/
`degraded`（诚实降级路径）/ `planned`（本 track 计划）。

## Prompt 面

| 能力 | before（`faa453a8`） | after（本 track 目标） |
|---|---|---|
| point/box prompt（像素/地理坐标） | implemented | implemented（不变） |
| prior mask prompt（数组直传） | implemented | implemented（不变） |
| text prompt | implemented（stub，显式标注） | implemented（stub + 真 seam：TextEncoder 协议 + 原型检索） |
| polyline prompt | not-supported（无词表） | implemented（栅格化为 1px 掩膜先验，确定性） |
| polygon prompt | not-supported | implemented（rasterio.features 栅格化 → prior mask） |
| reference-layer prompt | not-supported | implemented（引用图层 → 有界读 → prior mask；解析失败 typed 拒绝） |
| prompt artifact（版本/身份/CRS 身份/time/band-model/provenance） | not-supported（PromptSpec 内联，无身份） | implemented（GeoPromptArtifact v1 + sidecar mask） |
| CRS↔pixel 往返容差契约 | 部分（仿射精确，无声明容差/known-answer） | implemented（显式容差 + 双向 known-answer） |
| 多 mask 候选 + 质量分 + 选择 | not-supported（单掩膜 argmax） | implemented（候选集契约 + 参考实现 3 候选 + refine 工具） |
| prompt 复用/重放 | 部分（fingerprint 含几何） | implemented（artifact → 同 identity 重放 = 同指纹） |

## Embedding 面

| 能力 | before | after |
|---|---|---|
| chip embedding 推理 | implemented（tiny_reference） | implemented（不变） |
| 跨 run embedding cache | not-supported | implemented（键控磁盘 cache + LRU 上界 + 部分失效 + resume + digest 校验） |
| cache 观测（命中率/字节/驱逐） | not-supported | implemented（stats 面 + manifest 记录） |
| text embedding | not-supported | seam + 参考 stub（显式 stub 语义；无 provider = typed 拒绝） |
| 类别原型/zero-shot 映射 | not-supported | implemented（原型索引 + 余弦映射；无 encoder = typed 拒绝） |

## Surface 面

| 能力 | before | after |
|---|---|---|
| agent 工具（推理/检视/评估/lineage） | implemented（12 个） | implemented（不变 + 新增 geoai_* 只读/操作工具） |
| prompt artifact inspect/explain 工具 | not-supported | implemented |
| embedding 工具（带 cache 统计） | not-supported | implemented |
| prompt_refine 工具（候选选择→精化重跑） | not-supported | implemented |
| geoai HTTP API | not-supported | implemented（自包含新 route，最小面） |
| geoai UI 面板（点框提示/候选预览/接受撤销/队列） | not-supported | implemented（独立组件 + 路由，不触碰 map/* 共享文件） |

## 验证面（WP-H）

| 验证 | before | after |
|---|---|---|
| 合成几何 known-answer（IoU=1 期望） | 部分（georeference 测试有） | implemented（多种几何 + NoData + 多窗口） |
| tile seam 回归 | not-supported（promptable 锚定窗口无 seam 断言） | implemented |
| 大 prompt 批逻辑规模 | 部分（64/kind 上限有） | implemented（窗口数×prompt 数逻辑规模 + 上界断言） |
| cancel/OOM 在新路径 | 部分（engine 级有） | implemented（新路径逐点） |
| provider absence typed refusal | implemented（prompt mode 门） | implemented（扩展到 text/encoder/cache 缺席） |
| provenance replay | 部分（fingerprint） | implemented（artifact→重放→指纹一致断言） |
