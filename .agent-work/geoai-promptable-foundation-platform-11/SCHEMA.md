# SCHEMA — GeoPrompt Artifact v1（`app/lib/modelops/geo_prompt.py`）

JSON 形态（`GeoPromptArtifact.to_payload()`；`from_payload` 校验并验身份）：

```jsonc
{
  "schema_version": 1,                  // 必须 == 1（其他 → typed 拒绝）
  "crs": null,                          // null ⇒ 像素坐标；否则 CRS 字符串
                                        //（"EPSG:xxxx"/WKT；声明即要求编译期
                                        //  提供可逆仿射 + 容差断言）
  "points":  [[x, y], ...],             // 每类 ≤64；坐标语义由 crs 决定
  "boxes":   [[x, y, w, h], ...],       // w/h > 0（地图单位或像素）
  "polylines": [[ [x, y], ... ], ...],  // 每线 ≥2 顶点；编译为触及像元先验
  "polygons":  [[ [x, y], ... ], ...],  // 每环 ≥3 顶点；自动闭合；像元中心
                                        // 包含栅格化；自交环 buffer(0) 修复，
                                        // 零面积 → 拒绝
  "text": "a phrase",                   // 仅 provider 声明 text_prompt 时可用
  "mask_ref": {                         // 与 reference_layer 互斥
    "path": "/abs/or/mask_root/rel.tif",
    "sha256": "<64-hex>",               // 内容寻址：加载后 digest 校验，
                                        // 失配 → typed 拒绝
    "band": 1
  },
  "reference_layer": {
    "uri": "file:///... 或图层 uri",    // services 层有界解析
    "band": 1,
    "strategy": "nonzero | threshold",
    "threshold": 0.5,                   // threshold 战略必填（有限）
    "invert": false
  },
  "time": {                             // 三者至少一项；ISO-8601
    "acquisition": "2026-09-15T00:00:00Z",
    "valid_from": "...", "valid_until": "..."
  },
  "target": {                           // advisory 绑定；冲突 → typed 拒绝
    "model_id": "sam-like-x",
    "model_version": "1.0",
    "band_names": ["B04", "B03", "B02"]
  },
  "combine": "union | intersect",
  "labels": [1, 2],
  "artifact_id": "<sha256>",            // from_payload 时声明值必须与重算一致
  "provenance": {                       // 元数据：不进 artifact_id
    "created_by": "geoai-panel",
    "note": "...",
    "source_refs": ["layer:12"]
  }
}
```

## 身份规则

- `artifact_id = sha256(canonical_json(identity_payload))`，排序键、紧凑分隔符、
  UTF-8；**provenance 不进身份**（出处不改变推理语义）。
- 推理身份：`InferenceRequest.prompt_artifact_id`（条件进 fingerprint/postprocess）
  + 先验内容 digest（`prompt_prior_digest`，条件字段——旧请求保持字节级同 key）。

## 坐标/栅格化语义（known-answer 锁定）

- 地图→像素：仿射逆变换；往返误差 ≤ `PROMPT_ROUNDTRIP_TOL_PX`（1e-6 px）；
  行列式 0 → `PlanningError`。
- 多边形：像元中心包含（rasterio `all_touched=False`）。
- 折线：触及像元（`all_touched=True`，名义 1px）。
- reference-layer：`nonzero`（非零且有限）/ `threshold`（≥ 阈值且有限），
  可 `invert`。
- mask/reference 与目标栅格网格（shape）必须对齐，否则 typed 拒绝。
- anchor：全部几何（含派生掩膜包围盒）的半开整数包围盒 → `PromptSpec.
  anchor_box`（窗口放置专用；大 anchor 行主序网格分窗，每窗 ≤4096px）。

## 上限（typed 拒绝，绝不无界）

- 每类几何 ≤ 64；先验编译像素总量 ≤ 256M px（`MAX_COMPILED_MASK_PIXELS`）。
- 满幅 sidecar/reference 读取前先做像素上限检查（services 层 + lib 层双门）。

## 审计（`GeoPromptAudit`，进 manifest `prompt_audit` 节）

`artifact_id / crs / coordinate_space(pixel|map→pixel) / roundtrip_max_error_px /
derived_mask_source / derived_mask_pixels / anchor_box / time / target`。
