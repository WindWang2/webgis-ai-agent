# WebGIS AI Agent — 自主空间数据探索引擎 (Spatial Explorer)

**版本**: Phase 5 — 无限边界的主动式空间搜索行动力  
**日期**: 2026-05-07  
**状态**: 设计阶段  
**关联任务**: B011 后续 / 7.3 愿景路线

---

## 1. 项目背景

当前 WebGIS AI Agent 已具备：
- 标准地图 API 层（高德/百度/OSM/天地图）的 POI 查询、地理编码、路径规划
- 基础网络爬虫（DuckDuckGo + LLM 提取）
- MCP 增强层（预留）
- Celery + Redis 异步任务执行集群
- V2 UI 玻璃拟态界面与 SSE 实时流

这些能力覆盖了**结构化数据查询**场景。但用户在实际分析中经常遇到"数据深水区"：政府开放数据平台的教育/医疗/人口统计表格、社交媒体带地理标签的内容、新闻事件中的空间信息等非标准化空间数据。现有工具无法有效覆盖。

Phase 5 方向 3 "无限边界的主动式空间搜索行动力" 旨在构建一个**自主空间数据探索引擎**，让 Agent 能够主动发现、抓取、解析、融合外部非结构化数据，将其转化为可用的空间图层。

## 2. 设计目标

### 2.1 核心目标

| 目标 | 说明 |
|------|------|
| **自主触发** | Agent 在推理循环中主动判断是否需要深度搜索，无需用户显式指令 |
| **多源联邦** | 统一协调上传数据、RAG 知识库、标准 API、深度探索四层数据源 |
| **质量感知** | 每个数据源都有五维质量评分，Agent 基于评分智能抉择 |
| **过程可控** | Agent 在探索全过程中拥有干预权，低置信度时提请用户确认 |
| **高性能** | 大体积数据走 ref_id 引用，SSE 只传元数据，任务链异步不阻塞 |

### 2.2 非目标

- 不替代现有标准 API 工具（高德/OSM 等），而是作为补充
- 不做通用网页爬虫（已有 DuckDuckGo 工具），专注结构化/半结构化空间数据（如政府数据平台的 CSV/Excel 下载）
- 本期不接入 RAG，但预留完整接口
- 不处理实时流数据（如交通卡口），那是 7.2 数字孪生路线

## 3. 系统架构

### 3.1 四层数据联邦架构

```
┌─────────────────────────────────────────────────────────────────┐
│ Layer 1: 用户主权层 (User Sovereignty)                          │
│   - 上传数据 (GeoJSON/CSV/Shapefile)                            │
│   - 会话历史资产 (已加载图层)                                    │
│   - 优先级：最高，用户主动提供 = 最可信                          │
├─────────────────────────────────────────────────────────────────┤
│ Layer 2: 私有知识层 (Private Knowledge) — RAG 预留               │
│   - 向量数据库中的组织私有文档                                    │
│   - 规划文本、历史项目、内部标准                                  │
│   - 优先级：中高，非公开但权威                                   │
├─────────────────────────────────────────────────────────────────┤
│ Layer 3: 结构化 API 层 (Structured API)                         │
│   - 高德/百度/OSM/天地图 POI 查询                                │
│   - 遥感/空间分析服务                                           │
│   - 优先级：中，快速结构化但覆盖有限                             │
├─────────────────────────────────────────────────────────────────┤
│ Layer 4: 深度探索层 (Deep Exploration) — 本期核心                │
│   - 政府开放数据 (GovDataAdapter)                               │
│   - 网络爬虫 (WebCrawler)                                       │
│   - 社交媒体/新闻 (SocialAdapter/NewsAdapter) — 预留            │
│   - 优先级：动态，取决于质量评估结果                             │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 整体模块架构

```
frontend/
  components/explorer/
    explorer-progress-panel.tsx   # 深度搜索进度面板

app/
  services/explorer/
    intent_detector.py            # 意图识别器
    orchestrator.py               # 探索任务编排器
    quality_engine.py             # 质量评估引擎
    decision_engine.py            # 多源抉择引擎

  tasks/explorer/
    task_chain.py                 # Celery 任务链定义

  adapters/
    base.py                       # 适配器抽象基类
    gov/
      gov_data_adapter.py         # 政府开放数据适配器 (MVP)
    web/
      web_crawler_adapter.py      # 网络爬虫适配器
    rag/
      rag_adapter.py              # RAG 预留接口

  models/explorer/
    perception_event.py           # 感知事件模型
    data_package.py               # 统一数据包模型
    quality_score.py              # 质量评分模型
```

## 4. Agent 三阶感知介入模型

Agent 不是"调用者"，而是整个探索链条的**主导者**，通过三个感知阶主动介入。

### 4.1 一阶：自适应输入感知介入

Agent 在每一轮推理前主动扫描：
- **地图状态感知**：当前图层数量、空间范围、数据覆盖度
- **意图缺口感知**：用户查询所需数据类型与当前库存的匹配度
- **历史搜索记忆**：该主题是否已搜索过？数据时效？上次覆盖率？

基于扫描结果，Agent 自主决定是否启动 `deep_explore` 工具调用，并填写参数：
```json
{
  "query": "北京海淀区学校分布",
  "expected_data_type": "poi_list",
  "auto_threshold": 0.7,
  "source_hint": ["gov", "osm"]
}
```

### 4.2 二阶：过程感知介入

Explorer 每个阶段向 Agent 推送结构化感知事件，Agent 可执行干预动作：

| 阶段 | Agent 可干预动作 |
|------|----------------|
| discover | `select_source(idx)` / `expand_search(query)` / `filter_source(criteria)` |
| fetch | `retry_fetch(provider)` |
| parse | `accept_field_mapping` / `override_field_mapping` / `request_human_mapping` |
| geocode | `retry_geocode(provider)` / `accept_partial_result` |
| validate | `register_layer` / `trigger_supplement_search` / `abort` |

感知事件协议：
```python
class ExplorerPerceptionEvent(BaseModel):
    stage: Literal["discover", "fetch", "parse", "geocode", "validate"]
    task_id: str
    status: Literal["started", "progress", "decision_point", "completed", "failed"]
    context: dict                       # 决策上下文
    available_actions: list[str]        # 可执行干预动作
    recommended_action: str             # 推荐动作
    requires_intervention: bool         # 是否需立即介入
    confidence: float = Field(ge=0.0, le=1.0)
```

### 4.3 三阶：输出感知介入

Agent 收到最终结果后，执行输出质量感知评估：
- **覆盖度感知**：数据量是否满足分析需求？
- **精度感知**：坐标精度是否足够？模糊点位需标注
- **时效性感知**：数据是否过时？向用户提示局限性
- **分析准备度感知**：数据是否可直接用于空间分析？推荐后续操作

## 5. 数据源质量评估体系

### 5.1 五维质量评分模型

```python
class DataSourceQualityScore(BaseModel):
    temporal_score: float       # 时效性：按数据类型语义半衰期计算
    thematic_score: float       # 主题匹配度：LLM 语义 + 关键词 + 字段完备
    spatial_score: float        # 空间覆盖度：数据范围与用户关注区域重叠
    field_score: float          # 字段完整度：关键字段缺失率
    precision_score: float      # 坐标精度：地理编码精度分布
    overall: float              # 加权综合分
    details: dict               # 各维度明细
```

### 5.2 时效性语义半衰期

不同数据类型有不同衰减曲线：

| 数据类型 | λ (每月) | 半衰期 | 说明 |
|---------|---------|--------|------|
| 教育设施 | 0.03 | ~23月 | 学校变化极慢 |
| 医疗机构 | 0.05 | ~14月 | 医院变化较慢 |
| 交通设施 | 0.10 | ~7月 | 地铁/公交变化中等 |
| 商业 POI | 0.30 | ~2.3月 | 餐厅/商店变化快 |
| 人口统计 | 0.02 | ~35月 | 普查数据可用多年 |
| 房价数据 | 0.50 | ~1.4月 | 房价变化极快 |
| 事件/舆情 | 2.00 | ~0.35月 | 只关心最近一周 |

公式：`score = exp(-λ * Δt)`，Δt 为数据发布至今的月数。

### 5.3 主题匹配度双层评估

- **层1（权重 0.6）**：LLM 向量语义匹配，用户意图 vs 数据集标题+描述
- **层2（权重 0.4）**：结构化关键词覆盖，检查 dataset_tags + dataset_fields

关键字段推断：Agent 根据用户意图推断所需关键字段，检查数据集是否包含。

### 5.4 多源冲突处理

| 冲突场景 | 处理策略 |
|---------|---------|
| 坐标相同，属性不同 | 属性合并 + conflict 标记，Layer 1/2 优先 |
| 坐标相近，同一实体 | 空间聚类去重，保留坐标精度更高的 |
| 覆盖范围不同 | 补全而非替换，核心区 + 边缘区合并 |
| 时效不同 | 新数据覆盖坐标，旧数据保留历史属性 |
| 精度不同 | 精确点放上层，模糊区域用热力图表示 |

## 6. 多源智能抉择

### 6.1 动态优先级规则

优先级不是固定数字，而是**意图条件函数**：

```python
PRIORITY_RULES = {
    "user_upload_exact_match": {
        "condition": lambda ctx: ctx.has_uploaded_data and ctx.upload_matches_intent,
        "action": "FORCE_LAYER_1",
    },
    "rag_private_knowledge": {
        "condition": lambda ctx: ctx.rag_enabled and ctx.query_type in {"policy_analysis", "planning_review"},
        "action": "PROMOTE_LAYER_2",
    },
    "realtime_poi_needed": {
        "condition": lambda ctx: ctx.query_type == "poi_search" and ctx.freshness_required,
        "action": "PROMOTE_LAYER_3",
    },
    "deep_research_needed": {
        "condition": lambda ctx: ctx.query_type == "research" and ctx.coverage_required > 0.8,
        "action": "ACTIVATE_LAYER_4",
    },
    "cross_validation_needed": {
        "condition": lambda ctx: len(ctx.available_layers) > 0 and ctx.confidence_required > 0.9,
        "action": "MERGE_LAYERS",
    },
}
```

### 6.2 统一数据包契约

所有 Layer 返回的数据统一包装为 `DataPackage`：

```python
class DataPackage(BaseModel):
    source_layer: Literal["L1_upload", "L1_session", "L2_rag",
                          "L3_api", "L3_spatial", "L4_gov", "L4_web", "L4_social"]
    source_name: str
    source_url: str = ""
    quality: DataSourceQualityScore
    geojson: Optional[dict] = None
    features_count: int = 0
    temporal_range: Optional[tuple[datetime, datetime]] = None
    spatial_bbox: Optional[str] = None
    available_fields: list[str] = Field(default_factory=list)
    is_fusion_result: bool = False
    fusion_sources: list[str] = Field(default_factory=list)
    has_conflicts: bool = False
    conflict_fields: list[str] = Field(default_factory=list)
```

## 7. Explorer Task Chain (Celery)

### 7.1 任务链定义

```python
explorer_chain = chain(
    explorer_discover_task.s(),   # 数据发现：探测适配器，质量预评估
    explorer_fetch_task.s(),      # 内容抓取：下载原始数据，存 Redis
    explorer_parse_task.s(),      # 结构化：字段映射，低置信度时暂停等决策
    explorer_geocode_task.s(),    # 地理编码：批量编码，并发控制，熔断
    explorer_validate_task.s(),   # 质量验证：评分，注册图层或通知 Agent
)
```

### 7.2 关键设计

- **任务间只传 ref_id**，大数据始终存 Redis，避免内存复制
- **Redis 大对象压缩**：msgpack + zlib，>100KB 自动压缩
- **批量地理编码**：batch_size=200，concurrency=3，熔断器保护
- **断点续传**：任务状态持久化到 Redis，Worker 崩溃可恢复

### 7.3 各阶段超时与重试

| 阶段 | soft_limit | hard_limit | 重试 |
|------|-----------|-----------|------|
| discover | 30s | 30s | 2 次，指数退避 |
| fetch | 55s | 60s | 1 次 |
| parse | 55s | 60s | 0 次（失败即暂停等决策） |
| geocode | 290s | 300s | 0 次（单条失败记录，整批切换备用） |
| validate | 25s | 30s | 0 次 |

单任务总超时：10 分钟。

## 8. 性能与稳定性

### 8.1 传输优化

- **SSE 只传元数据**：大 GeoJSON 走 `ref_id` 引用，前端按需拉取
- **SSE 心跳保活**：15s 间隔，45s 无心跳自动重连，携带 last_event_id 断点恢复
- **进度事件节流**：同类型事件最小间隔 500ms，批量合并

### 8.2 内存与资源管理

- 单任务最大内存：512MB
- 单任务最大下载：50MB
- 接近内存上限时触发流式处理
- 接近超时时保存当前进度，返回"部分完成"

### 8.3 监控指标

- 任务总耗时、各阶段耗时
- 数据量：原始大小、解析条数、编码成功率
- 资源使用：峰值内存、CPU 时间
- 告警阈值：失败率>30%、编码成功率<60%、平均耗时>3min

## 9. 适配器扩展架构

### 9.1 抽象基类

```python
class BaseDataAdapter(ABC):
    name: str
    supported_query_types: list[str]

    @abstractmethod
    async def discover(self, query: str, context: SearchContext) -> list[DataSource]:
        """发现匹配的数据源"""

    @abstractmethod
    async def quick_assess(self, query: str, source: DataSource) -> DataSourceQualityScore:
        """快速质量预评估（不下载完整数据）"""

    @abstractmethod
    async def fetch(self, source: DataSource) -> RawContent:
        """下载原始内容"""

    @abstractmethod
    async def parse(self, raw: RawContent) -> StructuredData:
        """解析为结构化数据"""

    @abstractmethod
    async def get_field_schema(self, raw: RawContent) -> list[FieldInfo]:
        """获取字段结构，用于自动映射"""
```

### 9.2 MVP 适配器：GovDataAdapter

首期实现政府开放数据适配器，支持：
- 探测各地政务数据开放平台（北京、上海、广东等）
- 按关键词搜索数据集
- 下载 CSV/Excel 格式数据
- 自动编码检测（GBK/UTF-8）
- 字段自动识别与映射

后续通过新增适配器类即可扩展新数据源类型，零改动核心代码。

## 10. RAG 预留接口

RAG 知识库当前未接入，但已预留完整接口：

```python
class RAGAdapter(BaseDataAdapter):
    """RAG 知识库适配器 —— 预留，未来接入时零改动"""

    async def discover(self, query, context):
        # 预留：vector_db.similarity_search(query, top_k=5)
        return []

    async def fetch(self, source):
        # 预留：获取 Chunk 文本
        pass

    async def parse(self, raw):
        # 预留：文本 → 结构化（LLM 提取地理信息）
        pass
```

RAG 接入后自动纳入 Layer 2，参与多源抉择。

## 11. 安全与边界

### 11.1 安全防护

- 下载文件大小限制：50MB，防止恶意大文件
- URL 白名单：GovDataAdapter 只允许访问已知政务平台域名
- 敏感信息过滤：解析阶段自动过滤身份证号、手机号等 PII
- 频率限制：单 IP 每小时最多 10 次深度搜索

### 11.2 用户确认边界

混合策略的触发条件：
- **自动执行**（无需确认）：单源、标准格式、预估<2min、quality_score>0.7
- **征求同意**（需确认）：多源融合、非标准格式、预估>5min、quality_score<0.7
- **用户指令优先**：用户明确说"深度搜索"时跳过判断直接执行

## 12. 前端设计

### 12.1 ExplorerProgressPanel

新增玻璃拟态进度面板，集成到侧边栏：

- 显示当前探索阶段（discover/fetch/parse/geocode/validate）
- 进度条动画（CSS transition，不依赖高频事件）
- 已发现数据源列表（可展开查看质量评分）
- 决策点弹窗（需要确认时显示选项）
- 完成后显示结果摘要（来源、条数、时效、质量）

### 12.2 与现有 UI 的融合

- 探索任务启动时，聊天面板显示"正在深度搜索..."状态
- 进度面板可收起/展开，不遮挡地图
- 暗色/亮色主题自适应

## 13. 测试策略

### 13.1 单元测试

- `IntentDetector`：各种查询意图的决策正确性
- `QualityEngine`：五维评分的计算准确性
- `DecisionEngine`：多源冲突处理逻辑
- `BaseDataAdapter` 子类：各适配器的 discover/fetch/parse

### 13.2 集成测试

- 完整任务链：从发现到注册的端到端流程
- Celery 任务链的断点续传
- SSE 事件流的正确性与保活
- 多源融合的数据一致性

### 13.3 性能测试

- 10000 条记录的批量地理编码耗时
- 大文件（>10MB CSV）的流式处理内存占用
- SSE 高并发连接下的稳定性

## 14. 里程碑

| 阶段 | 内容 | 预计工时 |
|------|------|---------|
| **M1** | 基础框架：ExplorerOrchestrator + Celery 任务链 + SSE 推送 | 2d |
| **M2** | GovDataAdapter：政府开放数据发现、下载、解析、字段映射 | 3d |
| **M3** | Agent 集成：IntentDetector + 三阶感知 + 干预动作 | 3d |
| **M4** | 多源抉择：质量评估 + 冲突处理 + 数据融合 | 2d |
| **M5** | 前端面板：ExplorerProgressPanel + 决策 UI | 2d |
| **M6** | 性能优化 + 测试 + 文档 | 2d |
| **合计** | | **14d** |

## 15. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 政府平台 API 不稳定 | 数据源不可用 | 实现多平台 fallback（北京→上海→国家平台） |
| 地理编码 QPS 限制 | 大批量任务超时 | batch + 并发控制 + 多服务商切换 |
| 字段映射低置信度 | Agent 频繁暂停等决策 | 预置常见数据集映射规则库 |
| 大文件内存溢出 | Worker 崩溃 | 流式处理 + 内存限制 + 断点续传 |
| Agent 过度触发搜索 | 资源浪费 + 用户体验差 | IntentDetector 阈值调优 + 用户可配置 |

---

## 附录 A：术语表

| 术语 | 说明 |
|------|------|
| **Spatial Explorer** | 自主空间数据探索引擎，本期核心功能 |
| **三阶感知** | Agent 在输入、过程、输出三个阶段的主动介入 |
| **DataPackage** | 跨数据层的统一数据契约 |
| **语义半衰期** | 不同数据类型的时效衰减速度 |
| **ref_id** | 后端数据引用标识，避免 SSE 传输大对象 |
