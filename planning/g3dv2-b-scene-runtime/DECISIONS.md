# G3DV2-B DECISIONS

- **D1 基线选择**：以 origin/master `70dab2b9` 为基线（本地 checkout master 陈旧 72 提交）；侦查以 gh 实时 PR/issue 为准，不采信任务书历史 SHA 之外的任何快照。
- **D2 交错 accessor 策略**：byteStride>0 采取**正确逐元素步进读取**而非拒绝 —— glTF 2.0 规范内、常见导出器产物、读取成本 O(1)/元素；只有越界/非法组合才 typed 拒绝。sparse 同理支持规范安全子集。依据任务书"优先兼容常见交错模型和安全子集，禁止静默错读"。
- **D3 累计预算层级**：账本放 Scene3dManager（单 map 单例，跨资产天然共享），不放全局单例（多 map/比较视图不互相挤占）。上限默认保守值，spec policy 可收紧不可放宽超过机器上限。
- **D4 refCount 泄漏修法**：unmountAsset 检测 layer 已不存在（setStyle 清扫）时显式走 releaseRenderer 路径；acquireRenderer 拒绝复用不健康 carrier。不依赖 MapLibre onRemove 语义承诺。
- **D5 context-lost 分层**：canvas 级 `webglcontextlost`（preventDefault 争取 restore）+ `webglcontextrestored` 自动有限重建（re-sync 同 revision）；MapLibre 自身死亡（error 事件 fatal）仍走既有 WebGL 模态人工重试。不承诺无 GPU 环境行为。
- **D6 纯 3D ACK 指纹**：HUD 行 `_mapspecFingerprint` 优先语义完全保留；新增 spec 级回退仅当 HUD 无指纹时启用，且仅对含 scene.assets 的 spec 生效 —— 不改变任何既有 2D 会话行为。
- **D7 per-asset 控制真相层**：visible 走 spec 单真相（commitUserScene 管线）；opacity/lighting/视角书签为本地表象控制（不持久化、明确披露），避免为本轮引入 MapSpec v1.8 契约膨胀。
- **D8 Draco/KTX2/implicit tiling**：维持 typed 拒绝 + 能力披露，本轮不引入解码依赖（许可/兼容/安全验证窗口不足，任务书允许渐进）。
