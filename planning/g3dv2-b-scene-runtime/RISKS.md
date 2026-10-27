# G3DV2-B RISKS

| # | 风险 | 等级 | 缓解/披露 |
| --- | --- | --- | --- |
| R1 | 无 GPU 真机：WebGL 结论仅 SwiftShader 软渲染 | 中 | PERF/EVIDENCE 明示 not_evaluated；不声明真 GPU 帧率；j9 断言不依赖帧率绝对值 |
| R2 | WebGL context restored 自动重建依赖 MapLibre 存活；MapLibre 自身死亡时仍走人工模态 | 中 | D5 分层设计：canvas 事件管 scene3d 侧，fatal error 路径保留；j9 未覆盖真实 context lost（软渲染无法触发）——单测覆盖管理器状态机 |
| R3 | 本地表象层（显隐/透明度/光照/视角）不持久、不进 spec | 低 | UI 内显式披露文案（assetsLocalNote）；spec 可见性优先收窄（本地 true 不能复活 spec 隐藏资产）；换代重挂即复位 |
| R4 | 信封指纹回退对非 3D 会话也生效（revision 对齐时） | 低 | 与既有 cursor 回退同构（fp 见证 committed spec；服务端 stale 门兜底）；hook 测试锁定 2D 无指纹行为不变（无信封时跳过） |
| R5 | scene_work 进 work_fingerprint → 全量 corpus work 指纹变化 | 低 | generate.py 再生（禁手改）；Python/TS 双侧重放测试绿；消费方（ACK echo）以 work_id/指纹整体对账，无逐字节外部锁定 |
| R6 | OpenAPI 快照再生成吸收 master 历史欠账：#1830 两端点 + storymap 1.0→1.1 + set_scene/SetSceneBody/visible_layer_ids（master 代码已 1.1 而快照停在 1.0） | 低 | additive（无 breaking；test_api_compatibility 通过）；PR 描述逐项披露；**与开放 PR #1835 同改 openapi.json —— 后合并方需机械再生成一次**（评审 R1-P3-9） |
| R7 | 既有 master 红灯 2 例（catalogue 幽灵命令 / mutation parity） | 中 | 干净 origin/master 检出复跑取证（EVIDENCE）；归属 #1829/#1830 审查域不越界；若 CI 因其红为本 PR 报红，属上游遗留非本分支引入 |
| R8 | journeys 并行负载抖动（j9/j7 各一次、单跑均过） | 低 | j9 拾取超时提至 12s；全量回归以单跑复验为准；CI lane 承担正式回归 |
| R9 | terrain 深度遮挡仍为近似（shared depth buffer，非逐像素 terrain depth） | 中 | #1834 既有边界未变；B4 按任务书以披露为主，未宣称精确融合；真实改进需 MapLibre depth texture 支持，超出本轮 |
| R10 | 3D Tiles 扩展（Draco/KTX2/implicit tiling）仍 typed 拒绝 | 低 | D8：许可/兼容/安全验证窗口不足，渐进引入；能力矩阵在 capability 声明 partial |
