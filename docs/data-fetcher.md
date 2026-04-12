# 空间数据引擎与 Fetch-on-Demand (按需拉取) 架构 V2.0

## 概述
在 V2.0 架构中，数据流引擎完成了颠覆性重构。为了彻底解决几十 MB 的 GeoJSON 撑爆大语言模型（LLM）上下文，以及导致 SSE (Server-Sent Events) 单向长流崩溃卡死的问题，我们引入了业界标准的 **Fetch-on-Demand (按需拉取与引用剥离)** 架构。

## 核心架构：引用剥离 (Reference Stripping)

### 1. 痛点与旧模式 (V1.0)
以前，无论数据多大（如 5 万个相交运算后的建筑物多边形），后端 Tool 均全量返回给 LLM。LLM 接纳完整的 FeatureCollection 后，再通过 SSE 全量吐回给前端，这导致了：
- **Token 暴涨**：极大概率触发 LLM 截断，成本飙升。
- **网络熔断**：数分钟的纯文本输出引发 Nginx 或底层 TCP 的 `ERR_CONNECTION_RESET`。
- **解析卡死**：前端 React 强行 `JSON.parse` 超大流片段，直接崩库。

### 2. V2.0 按需拉取工作流
| 阶段 | 动作节点 | 原理描述 |
|---|---|---|
| **Step 1: 沙盒生成** | 后端 (Tools / Celery) | 当地理工具抓取或计算得到全量空间数据（GeoJSON 或 GeoDataFrame）时，不向函数外抛出明文实体。 |
| **Step 2: 压缩落床** | 缓存层 (`session_data.py`) | 调用中央暂存方法，将巨型负载通过 Redis 落盘，生成一个全局唯一签名（例如：`layer_id: "custom-osm-1776000551"`）。 |
| **Step 3: 轻量通信** | 大模型 (Claude) & SSE 通道 | 工具仅向大模型返回摘要与签名壳：`{"layer_id": "custom-osm-1776000551", "count": 50000}`。大模型以此组装回复流，瞬间推至前端。 |
| **Step 4: 前端取件** | 前端 (`MapPanel` 等拦截器) | React 层监听到此特异性 ID 后，绕过核心状态树（State），独立在后台发起并行的 HTTP GET 轮询。 |
| **Step 5: 原生注入** | MapLibre (GPU) | 获取到真实的实体 Payload 后，直接作为 DataSource 喂给底层的 MapLibre GL 对象原生绘制。 |

## API 交互范式

### 1. 前端获取凭证
前端在解析 Agent 推送的工具调用结果卡片时，会收到诸如下方结构：
```json
{
  "layer_id": "custom-heatmap_data-1776000548570",
  "render_type": "heatmap",
  "metadata": {
    "center": [116.39, 39.9],
    "count": 4820
  }
}
```

### 2. 前端发起真实拉取 GET `/api/v1/layer/{layer_id}/data`
一旦 UI 组件挂载，并行执行获取指令。

#### 响应
直接抛回 `application/json` 类型的原始 GeoJSON，或在未来拓展抛出 `mvt` (Mapbox Vector Tile) 以适应亿级渲染。

## 存储媒介隔离

- **实时会话层 (In-Memory / Redis)**: 用于存储单次生命周期内的临时抓取（如和风天气、临时 POI 爬虫）。过期时间 (TTL) 设定为 1-2 小时。即使丢失，也能通过 Agent 重试复原。
- **重度基建层 (PostGIS + MinIO)**: 用于落底用户主动上传的文件（如极其复杂的区域控规图 `shapefile` 等）。在获取阶段，同样生成 ID 指向底座，实现存算分离。

## 异常兜底 (Graceful Degradation)
- **404 击穿防护**：如果前端持有的 `ref_id` 因长时间未操作在 Redis 中过期失效，API 应当返回 `404/410`。前端拦截后将渲染出特殊的占位态，并向 Chat 对话框推送隐式提问 `"数据凭证已过期失效，请为我重新发起检索"`，唤起大模型的重新计算补抓循环。
