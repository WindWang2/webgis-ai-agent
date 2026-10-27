# G3D02 INDEPENDENT REVIEW — 两轮独立评审与清偿记录

评审者：两个未参与实现的独立 agent（不同上下文）；范围：全分支 diff、测试、科学/许可假设。

## 轮 1（GIS 科学正确性 + 前后端 ABI/契约 + 数据诚信）

### P0（已修）
| # | 问题 | 修复 |
|---|---|---|
| P0-1 | `boundingVolume.region` 单位按度解释，OGC 规范是**弧度** —— 真实数据集偏数千公里、自研 fixture 把 bug 烤进断言 | `tileset.ts` regionToEnuSphere 全面改弧度（METERS_PER_RADIAN）；fixture 改弧度值 |
| P0-2 | 3D Tiles 的 glTF 内容未施加 y-up→z-up 运行时旋转（违背自家 DECISIONS D3；CesiumJS 施加 Y_UP_TO_Z_UP） | `loadTile` 挂载矩阵 = localToEnu · gltfToEnuFix() |
| P0-3 | ECEF 根 transform 缺锚点平移扣减（geodeticToEcef 是死代码）—— 配准 tileset 偏 ~6371km | localToEnu = R_enu←ecef · (T_root − T_ecef(anchor))；`translationSub` |

### P1/P2（已修）
- P1-1 垂直 datum 词表（egm96/local）消费端零披露 → mountAsset 记录 `vertical_datum_<d>_rendered_as_ellipsoid_height` 降级披露（不换算、不静默）。
- P1-2 `set_map_scene` 旁路携带 assets 绕过域校验 → `_handle_setscene` 对 assets 键复用 SCENE_ASSET_* blocking 裁决（回归测试锁定）。
- P1-3 scale=Infinity 放行 / int(order) Overflow → finite 门 + float 比较；纬度域收口 ±85.0511（Web Mercator 世界边界）。
- P2：rollDeg/enuFromEcefRotation 注释方向、semantic-checks 契约 v3 版本注记、空表清空 + 无座不造 3d 档（回归测试）。
- 未采纳为代码改动的记录：regionToEnuSphere 平面近似（中纬 ~0.4%）与 cz 未扣锚点 altitude —— 会话级 culling 用途误差可接受，P2 备忘。

### 轮 1 明确核过无问题
mercator 投影逐位一致（node 独立复算）、gltfToEnuFix 等价 Cesium、schema v1.7 纯 additive、TS 投影、三重锁全链、handler COW 语义、测试真实性抽查 5 例无恒真。

## 轮 2（渲染正确性 + 资源生命周期/取消/泄漏 + 安全 + 并发）

### P0（已修）
| # | 问题 | 修复 |
|---|---|---|
| P0-1 | 恶意 glTF 两类放大炸弹：①多 primitive 共享巨型 accessor（500×56MB ≈ 28GB，POSITION/三角预算永不触发）②无 bufferView 谎报 count（200B 文件先分配 1.2GB） | `gltf-parser.ts` 解码预算前置：单 accessor 展开后 ≤64MB、资产累计 ≤256MB、count ≤ 20M，全部在 `new Float32Array` **之前**检查；两个回归测试（bomb/谎报） |
| P0-2 | tile 卸载/LRU 驱逐 `tiles.delete(entry)` —— 调度器后续选中时 `tiles.get` 为空静默跳过，相机拉回即永久破洞（3D Tiles 主路径在正常操作下必坏） | unload/evict 只释放 model、保留 entry（真 LRU 语义） |

### P1（已修）
- P1-1 basemap setStyle（非 diff 回退路径）抹 custom layer 且不回调 onRemove → sync 增加 `map.getLayer('scene3d-<id>')` 存活性校验，层缺失强制重挂（回归测试 + fake map 记账）。
- P1-2 content-length 可伪造/省略 → `readBodyWithBudget` 流式累计（reader.read 循环，超限 cancel），超时覆盖 body 读取全程；已 aborted signal 预检。
- P1-4 tile 加载完成不核对当前 render 集 → 完成时核对 prevRender，过期不入 group（记 `tile_expired` 披露）。
- P1-6 tile 失败无退避（200ms tick 永久重打 404）→ `failedAt` + 30s TTL 退避。
- P1-3（3D Tiles 无聚合预算）/P1-5（context lost 事件面）为真实缺口，规模超出本轮收口 —— 记入 EVIDENCE 残余限制（防御层：per-parse 5M 三角/64MB/256MB 预算 + LRU cap 仍在上界内）。

### 轮 2 明确核过无问题
sync 幂等竞态（mountAsset 首个 await 前同步 set）、generation 守卫覆盖全部 await 点、unmount 清理完整性、loadInFlight finally 归位、dispose/timer 竞态、tileset 节点预算时机、status 环有界、GL state（maplibre setCustomLayerDefaults + three resetState 双侧失效化）、拾取坐标换算链。

## 修复后验证（全绿）
- Python：cartography + scene 全域 2912 passed（含新增旁路门/Infinity/极纬/空表语义回归）；ruff 干净。
- 前端：scene3d 65 passed（含 bomb/谎报/setStyle 恢复回归）；typecheck 双配置；eslint 0 warning；j9 journey 1 passed（真 WebGL）。

## 总体结论（两轮合并）
初版在契约/事务/幂等面扎实，但 3D Tiles 数据消费存在三处规范违背（被自研 fixture 掩盖）与两处可由小文件触发的资源缺陷 —— 全部 P0 已修复并有回归测试锁定；修后两轮评审人均未再提出阻塞项，达到可合并状态。残余 P1/P2 缺口如实在 EVIDENCE 披露。
