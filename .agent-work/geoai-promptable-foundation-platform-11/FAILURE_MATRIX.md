# FAILURE_MATRIX — 新增路径的失败语义（Platform 11）

全部 typed（ModelOpsError 家族），带 correction_hint；禁止静默降级/假成功。

| 路径 | 失败条件 | 错误 | 行为 |
|---|---|---|---|
| artifact 构造 | schema 版本/几何非法/CRS 词法/mask+reference 并存 | PromptArtifactError | 构造期拒绝 |
| 编译 | crs 声明但无 transform / 退化仿射 / 往返超容差 | PlanningError | 拒绝（不产漂移几何） |
| 编译 | mask sidecar digest 失配 / 网格不对齐 / 像素超上限 | PromptArtifactError | 拒绝 |
| 编译 | reference 解析失败/策略非法 | PromptArtifactError | 拒绝 |
| 编译 | 派生先验为空且无点/框 | PromptArtifactError | 拒绝（“select nothing”） |
| engine | artifact 目标模型绑定 ≠ 运行模型 | PlanningError | 拒绝（先验推理前） |
| engine | 候选请求但 provider 未声明 mask_candidates | CompatibilityError(PROMPT_CANDIDATES) | 拒绝 |
| engine | candidate_selection 词表外 / index 越界 | ModelOpsError / PromptArtifactError | 拒绝（不夹取） |
| provider | 候选形状/分数界/来源界违反 | ProviderError | validate_for 拒绝 |
| embedding cache | get 时 digest 失配 | （内部）驱逐 + miss | 不产出污染向量 |
| embedding cache | put 原子失败 | False + tmp 清理 | 不留残件 |
| 语义面 | 无 encoder / 维度失配 / 空索引 / 零向量 | MultimodalUnsupported | 拒绝（不伪装理解） |
| refine | 无候选产物 / 候选下标缺席 / 空栅格化 | ModelOpsError | 拒绝 + hint |
| HTTP | source_uri/candidates_path 解析出 DATA_DIR | 400 | 路径门 |
| HTTP | 栅格打不开（preview） | 422 | 不泄栈 |
| UI | fetch 非 2xx | 面板错误条 + 队列 error 项 | 可重试 |

## 取消/超时

- 推理：既有 CancellationToken/deadline/checkpoint 纪律不变；promptable
  逐窗 `checkpoint()`；候选/嵌入路径不新增无界等待。
- embedding cache 路径：miss 子批推理前 `checkpoint()`；OOM 降级为逐张
  （单张仍 OOM = typed 失败；已命中窗口不受影响）。
