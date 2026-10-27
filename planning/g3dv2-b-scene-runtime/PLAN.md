# G3DV2-B PLAN — 实施计划

## 总体
在 #1834/#1830 内核上做**增量演进**（不重写、不建第二渲染器/Cesium），按"契约协商先行、内核严格性、资源治理、生命周期、工作台、验收"六个里程碑推进。每个里程碑：测试先红 → 实现 → 本地门禁 → 原子 commit。

## 里程碑
- **M1（契约，G1）**：纯 3D 真 ACK。
  - 前端：use-cartographic-observation 指纹门扩展 —— spec 级指纹回退（applied spec 自身可携带/计算指纹，不再依赖 HUD 行代指纹；有 HUD 行时优先 HUD，语义不变）。
  - 后端（最小 additive）：`chat_schema.py` DTO 增可选有界 `scene3d_assets` + validator；`render_work_envelope.py` 增 `scene_work[]`（additive 字段，不改既有字段语义）；ACK 侧 scene 资产对账（sources 词表内 model3d 已有，补 scene 级 applied/stale 证据位）。
  - corpus：经 `tests/cartography/render_work_corpus/generate.py` 再生成（禁手改 golden）；contract_manifest TS 镜像同步。
- **M2（内核严格性，G2+G6）**：glTF/3D Tiles 严格解析。
  - byteStride：**正确读取交错 accessor**（bufferView.byteStride + accessor.byteOffset 逐元素步进），越界 typed 拒绝 —— 消除静默错读。
  - sparse accessor：支持安全子集（indices/values bufferView + componentType 词表 + count 边界），非法 typed 拒绝。
  - normalized：从 accessor 读取标志，完整词表 5120/5121/5122/5123（÷127/255/32767/65535）；非 normalized 整数 POSITION/NORMAL 维持拒绝但 code 明确。
  - Tiles：refine 非法值 typed 拒绝 + 父子继承；URI 策略统一走 resource.isAllowedUrl 语义。
- **M3（资源治理，G3）**：跨资产累计预算。
  - manager 级 `ResourceBudgetLedger`：geometry bytes / texture bytes / triangles / CPU decoded bytes 累计账本 + 上限（默认值 + spec 可覆盖）；超限先 evict LRU 非可见 tile，仍超 → typed `degraded budget_exceeded`（入 status ring，per-asset 可观测）。
  - 全局 in-flight 下载信号量（跨资产共享并发上限）；abort/unmount/setStyle 必须归还预算且重复 style reload 不累积（结合 G5 修复验证）。
- **M4（生命周期，G4+G5）**：
  - status ring 增 `cancelled`、`context_lost`（additive；telemetry 快照透传有界）。
  - 修 refCount 泄漏：layer 已被 setStyle 清扫时 unmount 路径显式 releaseRenderer；acquireRenderer 校验 carrier 健康度。
  - context lost/restored：canvas 级监听；lost → 停调度/abort in-flight/资产标 context_lost/释放 THREE 侧；restored → 有限自动重建（re-sync），超时或 MapLibre 死亡 → 现有 WebGL 模态（用户可操作）。MapLibre error 事件路径保持。
- **M5（工作台，G7）**：
  - SceneController 增 per-asset 区块：visible 开关（走 commitUserScene 单真相管线，若 mutation 词表不含 asset 级则做最小 additive 词表）、opacity 本地表象控制（明确披露不持久化）、lighting 预设（manager 本地）、视角保存/恢复（本地书签，不进 spec）。
  - i18n：scene-disclosure COPY 迁移 messages；拾取卡双语；a11y：Escape/focus trap/方向键 radio；j9 定位器修正。
- **M6（验收，G9）**：
  - j9 扩展：纯 3D（零 2D 图层）journey 断言 cartographic-observation POST 含 scene3d_assets 与 scene_work ACK 证据；既有断言不放松。
  - 本地门禁：vitest 全量（--maxWorkers=2）、tsc 双配置、eslint --max-warnings 0、定向 pytest --no-cov（cartography/scene 相关 + corpus drift）、ruff。
  - next build 逐 chunk before/after（flock 串行）；perf 计数（调度/GLB 挂载/预算执行）附机器环境 ≥3 轮。
  - 独立 review（未参与实现的 subagent）→ 修复 → 复验 → PR。

## 里程碑与 B 任务映射
B0=侦查（已完成，BASELINE/GAP/重叠矩阵）；B1=M1+M4；B2=M2；B3=M3；B4=诚实披露+既有近似（不改内核深度策略，披露增强）；B5=M5；B6=M6。

## 明确不做（本轮）
- Draco/KTX2/meshopt/implicit tiling 解码引入（无依赖许可/兼容验证窗口）；保持 typed 拒绝 + 能力披露。
- 真实 GPU 声明（无硬件，SwiftShader 诚实标注）；terrain 逐像素深度精确化（披露近似不变）。
- 2D 图层管理改造、C 方向算法、A 方向 DEM 供给。
