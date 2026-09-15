# GeoPrompt Artifact 用户/Agent 契约（Platform 11 · ADR-0198）

GeoPrompt artifact 是**版本化、内容寻址**的地理提示：一次"点/框/折线/
多边形/参考图层/mask sidecar/文本"提示的完整授权与互换载体。本页是
面向使用者与 agent 的操作契约；字段级 schema 见
`.agent-work/geoai-promptable-foundation-platform-11/SCHEMA.md` 与
`app/lib/modelops/geo_prompt.py`。

## 心智模型

```text
GeoPromptArtifact（作者层：身份/CRS/时间/绑定/provenance）
   │ compile_geo_prompt（确定性编译 + 注入式 IO + fail-closed）
   ▼
PromptSpec（运行时：窗口像素坐标 + 先验数组） ──► provider 能力门
   ▼
掩膜 GeoJSON/栅格 +（可选）多候选质量分 ──► accept → refine（候选为先验重跑）
```

## 快速开始（agent 工具）

- `geoai_prompt_artifact_inspect`：校验 + 身份 + 编译 dry-run（不推理）；
- `geoai_run_promptable`：artifact（或 points/boxes）→ 分割；`return_candidates`
  取全部候选（分数/来源/窗口）；
- `geoai_prompt_refine`：选定候选 → 内容寻址先验 → 重跑；
- `geoai_embed`：逐窗特征 + cache 观测；
- `geoai_semantic_zero_shot`：类别原型 + 余弦映射（无 encoder = typed 拒绝）。

## 快速开始（HTTP / UI）

- `GET /api/v1/geoai/models`、`GET /api/v1/geoai/status`
- `GET /api/v1/geoai/preview?source_uri=…`（数据目录内；PNG base64 + 地理元数据）
- `POST /api/v1/geoai/prompt-segment`（artifact/points/boxes；候选参数）
- `POST /api/v1/geoai/prompt-refine`（候选路径 + 下标）
- UI：`/geoai` 面板（点击=点提示，Shift 拖拽=框提示，候选分色预览，
  接受→精化，撤销，批次队列）

## 坐标语义（防错要点）

- `crs: null` ⇒ 几何为**目标栅格像素坐标**；声明 CRS ⇒ 地图坐标，
  编译期仿射逆变换 + 往返容差（1e-6 px）断言，退化变换拒绝；
- mask sidecar / reference-layer 必须与目标栅格**同网格**（shape 对齐）；
- 多边形=像元中心包含；折线=触及像元；参考层=nonzero/threshold（可 invert）；
- anchor（编译派生）只影响窗口放置，不是语义 box prompt。

## 诚实边界

- 质量分是**排序代理**（heuristic 来源显式标注），不是校准置信度；
- 无文本 encoder 时语义面 typed 拒绝（stub 仅离线验证，结果标注 stub=true）；
- embedding cache 只服务确定性模型（seed policy 门），digest 失配自动驱逐。
