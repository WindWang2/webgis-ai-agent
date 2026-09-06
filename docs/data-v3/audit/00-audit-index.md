# Data V3 — Phase 0 审计索引

> 六路只读审计（master@222a994f），为 V3 设计提供证据基础。
> 综合结论见 `docs/data-plane/v3-foundation/architecture.md`。

| # | 审计 | 关键结论 |
|---|------|---------|
| 01 | Data Model | 四套 artifact 体系 + 三套 dataset 体系并存；ref id 随机不内容寻址；`RefDescriptor.content_hash` 恒 None；扩展点在 `app/lib/data/` + 既有 registry |
| 02 | Ingest | 上传格式 GeoJSON/SHP(zip)/KML/GPKG/CSV/GeoTIFF；GeoJSON 缺 CRS 静默 4326、栅格缺 CRS 接受；无内容哈希去重；上传清理纪律好；SSRF 缺口在 RemoteRasterSource；openpyxl 未声明 |
| 03 | Artifact Flow | 最大血缘缺口：dispatch 调 `register_tool_artifact` 未传 inputs（cursor_sink 钩子已在 registry 中存在未接线）；workflow/plan 层传原始 dict；export 无关联 |
| 04 | Cache | `app/lib/data/fingerprints.compute_reuse_fingerprint` 已存在但零消费者；tool_cache/analysis_reuse 键不含内容证据；三套 singleflight 已有；`_suffix_output_path` 泄漏临时文件；project_artifacts 无容量上限 |
| 05 | Workspace | project 打开不恢复任何状态；storage_ref/run outputs 指向 4h TTL 会话 ref；conversations 无 project_id；annotations/exports 无服务端存储；迁移规范 0026 ← 0025 |
| 06 | Scale | perf 规范 `-m perf --no-cov` + baselines.json 中位数 7 次；全量剖析路径 10 万要素级崩溃（DatasetProfile 零扫描路径安全）；无 GIN 索引；slim_tool_result 已守住 LLM 上下文 |

## 环境注意（本地验证）

- 本机 VPN fake-IP DNS 将一切域名解析到 198.18.0.0/15，触发 `app/core/config.py` SSRF 校验。
  本地跑测试需：`export OVERPASS_API_URL="https://1.2.3.4/api/interpreter" NOMINATIM_URL="https://1.2.3.5/search"`。
- pytest-cov 未装：`python -m pytest -o addopts=""`。
