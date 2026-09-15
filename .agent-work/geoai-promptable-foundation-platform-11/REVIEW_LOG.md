# REVIEW_LOG — 独立 adversarial review 与 remediation（Phase 7）

三个只读 reviewer subagent（架构/权威重复；数值/CRS/provenance；并发/安全/测试可信度），
从 `origin/master...HEAD` 全 diff 审。以下为全部 finding 的 disposition。
修复均随附回归测试（`tests/integration/modelops/test_geoai_review_fixes.py` 等）。

## P0（1）

| # | Finding | Disposition |
|---|---|---|
| R3-1 / R1-1 / R2-1 | embedding cache 路径忽略 `roi_origin`：ROI run 读错窗口 + key 用 ROI 本地窗口与全幅 run 冲突（错向量 + 跨 run 污染） | **Fixed**：`_embedding_batch_via_cache` 接收 `roi_origin`，读取平移 `(roi_dx, roi_dy)`，key 用**绝对**窗口。回归：`test_embedding_roi_cache_correct_and_no_cross_contamination`（同 ROI cache-on 重放 == 冷向量；不相交 ROI 向量不同） |

## P1（7 → 全修）

| # | Finding | Disposition |
|---|---|---|
| R3-2 | `EmbeddingCache.put` 同键二次写：先 `os.replace` 覆写后再 `_remove_files_quietly(old)` 删新文件并 `stat` 崩溃（并发默认配置可达） | **Fixed**：改「先从索引摘除旧条目（不动文件）→ replace 原子覆盖 → 预取 size」。回归：`test_embedding_cache_double_put_same_key` |
| R2-2 | text 内容与 labels 只进 presence 不进指纹（reference provider 按 sha256(text) 选带 → 同 key 不同结果） | **Fixed**：`geometry_payload` 条件加入 `prompt_text`/`prompt_labels`。回归：`test_geometry_payload_carries_text_and_labels` + `test_text_content_differentiates_reuse` |
| R2-3 / R1-（同族） | polygon/polyline 先验与 mask_ref/reference 并存时被静默覆盖（丢几何 + anchor 错位） | **Fixed**：artifact 层 typed 拒绝该组合（"cannot combine"）。回归：`test_polygon_with_sidecar_rejected` |
| R3-3 / R1-5 | artifact 内嵌 `mask_ref.path`/`reference_layer.uri` 绕过 DATA_DIR 门（任意本地栅格读取 + sha256 oracle；错误文本回显实际 digest） | **Fixed**：`compile_geo_prompt(allowed_roots=…)`（HTTP 面传 DATA_DIR+registry；tools 面受信不传）；错误文本不再回显实际 digest。回归：`test_artifact_reference_path_gate` |
| R1-3 / R3-7 | `/geoai/models` 无 session 时 `normalize_scope` 抛错 → 500（面板 load models 必挂）；compile 在 try 外 → 畸形 artifact 500 | **Fixed**：models 路由 scope 可选；prompt_segment 的 compile 移入 `ModelOpsError→422` 保护。回归：`test_models_route_without_session_ok` |
| R1-4 / R3-6 | `geoai_prompt_refine` 工具在事件循环上跑同步重推理（冻结全部协程） | **Fixed**：`asyncio.to_thread` 卸载（与 HTTP 路由同口径） |
| R2-4 | 前端 `screenToMap` 对 `crs=null` 预览仍做 y 翻转 → 负/翻转像素坐标（未地理参考栅格必错） | **Fixed**：`crs=null` 分支走源像元坐标（无翻转），`mapToScreen` 反向一致；新增 null-CRS 测试 |
| R1-2 | 新 capability 无 algorithm 注册 → `AlgorithmRegistry.validate()` 报错、既有 `test_component_composition::test_algorithm_taxonomy` 失败（capability↔algorithm parity 契约） | **Fixed**：新增 `model.inference.promptable_segmentation` AlgorithmDescriptor + 任务映射改指新 capability；更新既有 graph 断言为拆分语义（master 测试编码旧映射，ADR-0198 显式变更）。59 passed |

## P2（修复/裁定）

| # | Finding | Disposition |
|---|---|---|
| R1-12 | provider 声明 mask_candidates 但本次返回 None → 静默退化/空候选发布 | **Fixed**：引擎 fail-closed（ProviderError）。回归：`test_provider_returning_no_candidates_fail_closed` |
| R1-7 | seeds descriptor `provider_semantic_version` 仍 1.0.0 vs provider 1.1.0（双真值） | **Fixed**：seeds 对齐 1.1.0 |
| R1-8 | `as_dict()` 无条件加 `mask_candidates` → 全系统 reuse key 一次性失效 | **Fixed**：条件发射（仅 True 时出现），旧 provider payload 字节稳定 |
| R2-5 | preview 分位拉伸未掩蔽 nodata/NaN（NaN → 全黑；填充值压垮分位） | **Fixed**：finite+nodata 掩蔽后拉伸，nodata 固定黑 |
| R2-6 | 屏幕↔地图用边缘约定（半降采样块系统偏移） | **Fixed**：像元中心约定 `(px+0.5)/w`（测试手算同步更新） |
| R2-7 | embed cache key 缺 software_env 与 provider_id（跨 numpy/GDAL 升级或同语义不同实现会错命中） | **Fixed**：key payload 加 `provider_id` + `software_env_digest` |
| R2-8 | mask-only 启发式 = 纯紧凑度 → 空候选得满分 | **Fixed**：先验存在时覆盖（IoU）主导 × 紧凑度 |
| R1-9 / R2-13 | `prompts_to_pixel`/`window_local_prompts` 丢弃 anchor_box | **Fixed**：anchor 随仿射同变换 + 窗口局部平移 |
| R1-10 | `run_prompt_refine` 读任意路径 candidates（跨 owner） | **Fixed**：限定 registry 数据域；HTTP 面另有 DATA_DIR 门 |
| R3-4 | refine 栅格化无像素上限；prompt_refs 随 run 数线性增长 | **Fixed**：`MAX_COMPILED_MASK_PIXELS` 门 + sidecar 以**掩膜内容**寻址（同内容复用文件） |
| R3-5 | cache tmp 残件只在 OSError 清理，且启动扫描不匹配 tmp 名 | **Fixed**：BaseException 清理（取消类上抛）+ 启动扫描清 `*.tmp*` 与无效键 sidecar |
| R1-11 / R3-7 | 路由错误映射不一致（compile/KeyError/rasterio 错误 → 500） | **Fixed**：compile 入 try；refine 校验 feature.geometry 并把 JSON/rasterio 错误包装为 ModelOpsError |
| R1-6 | `SemanticClassMap` 进程级全局、跨会话互踩 | **Fixed（措辞）**：`replace=True` 语义与无 encoder 拒绝保留；单例 + 显式 replace 是平台级原型注册的既定语义，owner 维度扩展列入 follow-up（PR_BODY known limitation）。|

## P3（修复或记录）

| # | Finding | Disposition |
|---|---|---|
| R2-9 | `int()` 向零截断（负坐标半开 bbox 偏差 1px） | Fixed（floor） |
| R2-11 | 容差不能检测病态（近奇异）变换，docstring 言过其实 | Fixed（措辞收紧为退化 + 浮点残差；det==0 门保留） |
| R2-12 | `derived_mask_pixels` 报画布尺寸而非真值数 | Fixed（sum；测试同步） |
| R2-10 | 旋转仿射下 box 两角点法错 | Fixed（北向上门：b≠0 或 d≠0 时 box typed 拒绝） |
| R2-14 | 2 波段栅格 preview 形状错 | Fixed（复制末波段） |
| R2-15 / R3-13 | 弱断言（candidate-2 空转、正交性 0.5 过松、框最小足迹缺失） | Fixed |
| R1-18 / R3-12 | 前端：队列无界、多窗候选只读窗 0、多边形无渲染上限、模型拉取无 scope、导航入口缺失 | Fixed（队列/渲染上限、全窗聚合、session scope）；导航入口 → follow-up（不新增 sidebar 共享文件改动） |
| R1-13 | `to_payload/from_payload` 无 anchor（审计可读性） | Fixed（`prompt_audit.anchor_box` 已在 manifest；payload 层 anchor 属窗口放置元数据——裁定不做，避免 provider 契约扩展） |
| R1-14 | `from_payload` 嵌套构造抛裸 TypeError | Fixed（typed 包装 + inspect 广谱捕获） |
| R1-15 | geo_prompt docstring "lib 不 import numpy" 与实现矛盾 | Fixed（措辞：懒加载） |
| R1-16 / R3-8/9 | cache get 双读 + 全锁内 IO + 每 put fsync；last_used 非原子；扫描上限遗留** | 裁定：正确性优先于吞吐（v1）；记录为后续优化（PR_BODY）。 |
| R1-17 | `MODELOPS_TEXT_ENCODER` 非法值静默禁用 | Fixed（warning 日志，不 crash 启动） |
| R3-10 | `/geoai/artifact-geojson` DATA_DIR 内任意 JSON 可读 | 裁定：DATA_DIR 视为同信任域（与 raster.png 路由同口径）；路径已 resolve+相对检查。follow-up：按产出 run 的 owner 收窄 |
| R3-11 | 请求体无显式上限（artifact/points/boxes） | Fixed（pydantic max_length=64 于 points/boxes；artifact 几何上限在服务端构造期 64/类） |
| R2-16 | stub encoder 依赖 numpy Generator stream 稳定性 | 裁定：stub 语义版本已标注；仅离线验证用。记录 |

## 汇总

- P0：1/1 fixed；P1：7/7 fixed；P2：12 fixed / 1 裁定；P3：12 fixed / 4 裁定/记录。
- 既有 master 测试变更 1 处（`test_capability_graph_v8`），语义随 ADR-0198 词表拆分显式更新（非跳过/删除）。
- 复跑：`tests/unit/modelops + tests/integration/modelops` → 341 passed；`tests/unit/gis_harness` 全量（1707+）中唯一失败即 parity，已修；前端 geoai 7 passed。
