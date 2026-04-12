# WebGIS AI Agent 技术架构设计文档 (V2.0)
> 版本：v2.0 | 日期：2026-04 | 状态：深度落地

## 1. 架构概述与设计原则
本项目定位于“具备深层自主决策与 GPU 级流式渲染的空间数据科学家”。
从 V1.0 到 V2.0，工程底座经历了彻底的重构重组，严格贯彻以下工业级防崩坏原则：
- **极致的计算隔离**：FastAPI 主路由只负责搬运状态，任何涉及到 GeoPandas 裁剪、数万坐标转换的阻塞性算子，强制下放至 Celery 军团。
- **内存防雪崩传输 (Fetch-on-Demand)**：大模型不读矢量源码、SSE 不推巨量坐标。大数据传输压缩为全局 `ref_id` 提货券流转。
- **GPU 原生释压**：抛弃后端栅格化生图，全面转由前端 MapLibre 掌管 Vector Tile 与 Shader 原生着色。
- **Exception 级自组织**：失败不抛错，化作伪系统提示语交办大模型完成逻辑缝合。

---

## 2. 整体立体分层流转架构

```mermaid
graph TD
    A[用户态 User] -->|自然语言/上传文件| B(前端渲染枢纽 Next.js)
    
    subgraph 前端渲染枢纽 [前端边界 (Next.js + MapLibre)]
    B1[React Chat 对话树] --> |解析拦截| B2[MapPanel 原生渲染层]
    B2 --> |GPU Shader 补帧| B3[热力图/聚类层]
    end
    B --> B1
    
    subgraph 边界网关 [非阻塞 API 护城河 (FastAPI)]
    C{SSE 流推网关} -.-> |保活检测 :keep-alive| B1
    C1[Chat 路由进程] --> C
    C2[空间取件路由 fetch] --> B2
    end
    B1 -->|POST /chat/stream| C1
    
    subgraph 大脑中枢 [AI Agent 调度层]
    D[Orchestrator 编排器] --> |Tool 调用指令| D1(Claude 3.5+ API)
    D1 --> |生成 JSON 架子| D
    end
    C1 --> D
    
    subgraph 重算隔离区 [Celery 分布式超算群]
    E[Worker: GeoPandas 交集计算] 
    E1[Worker: 遥感掩膜获取]
    end
    D -->|异步投递算子| E
    
    subgraph 数据弹药库 [海量时空与流存储]
    F[(Redis 集群)] --> |暂存超大 GeoJSON 提货券| C2
    F1[(PostGIS / SQLite)] --> |落盘长期存储| E
    end
    E --> F
    E --> F1
```

---

## 3. 核心流链路解析

### 3.1 Fetch-on-Demand (按需提件流)
在传统的 LLM+GIS 应用中，由于大模型需要输出计算结果，常常会导致上下文被 50MB 的 GeoJSON 所撑爆。
**V2.0 解决方案**：
1. **Tool 层封箱**：当后端 Python 函数运行完毕获得大尺寸 `FeatureCollection` 时，生成一个唯一随机签名，如 `geojson_09a8b7c`。数据本体被悄然封存在 Redis 中，过期时间设为 1 小时。
2. **LLM 传输层**：大模型仅看到 `{"layer_id": "geojson_09a8b7c", "render_type": "heatmap"}` 这样的虚壳签名，立刻返回给主路由。
3. **SSE 极简下发**：网关以每秒数十个 Token 的速度推流，包含提货码。
4. **前端提货**：客户端 React 拦截器一旦拼装出完整 `layer_id` 且带有 `geojson_` 协议头，立刻通过 `/api/v1/layer/{id}/data` 路由发起独立的 HTTP 拉取任务。
5. **挂载**：获取的巨量点位直接绕过 React State，注入 `mapRef` 实例底层源生绘制。

### 3.2 SSE Keep-Alive 心跳保活阵列
在进行动辄数分钟的全国级道路网相交测算时，前端与 FastAPI 极易因为长时间无响应而发生 `ERR_CONNECTION_RESET`。
**V2.0 解决方案**：
在 `chat_engine.py` 的主异步生成器中，植入了一组独立看门狗循环。当测算被丢给 Celery 且进入阻塞等待时，看门狗每隔 15 秒向传输层丢弃一个透明的注释型数据框（如 `data: [HEARTBEAT]\n\n` 或空字段）。此机制从硬件网关（Nginx等）层面维系了通道常开。

---

## 4. 异常自理机制 (Exception As Thought)

GIS 时空框架的冲突是编程中极难枚举完的边界问题。
**防死磕策略：**
```python
# Tool Use 外包装的伪代码规范
try:
    result = perform_heavy_spatial_cut(gdf_a, gdf_b)
    return success_pack(result)
except Exception as e:
    # 决不能 raise Http500!
    error_trace = f"Tool Execution Failed. Reason: {str(e)}. Please consider call 'fix_crs' tool or change parameters."
    return as_pseudo_user_message(error_trace)
```
系统截获这类物理异常后，将其打包为“下一步该怎么走”的建议文本，再次输送给大模型的会话历史栈。由大模型经过反思（Reflection）决定是否纠错重跑，从而缔造一种“永不宕机”的自驱动观感。

---

## 5. 项目部署架构体系

- **单机实验级 (Local Dev)**: 使用 `/scratch` 或 `sqlite` 进行极轻量降级挂载。
- **标准容器级 (Docker Compose)**: 推荐形态。一键拉起 `Web` (FastAPI), `Worker` (Celery), `Redis`, `DB`。
- **无限伸缩级 (Kubernetes)**: 
  - `Ingress` 处理万级客户端 SSE 长连接黏性路由。
  - `Worker Pods` 依据高密计算列队的堆积厚度完成自动弹性（HPA）扩展。 
  - `PostGIS` 做主从高可用解构读写分离。

---

## 6. 工具与模块扩展纪律 (Contributing Guide)

未来所有新增的空间算子、爬虫组件必须遵循以下红线：
1. **Pydantic Type Guard**：必须使用最严苛的 `pydantic.Field` 进行 Tool args 强约束验证。
2. **Zero Big Data in Context**：绝对禁止 `return { "type": "Feature", ... }` 交往大模型脑端。
3. **Celery First**：但凡使用到 `pd.read_csv`, `gpd.sjoin`, `rasterio.open` 的接口，必须打上 `@celery_task` 修饰印记，扔出主干道外执行。 
4. **No Raster Push**：不要再尝试后端生图片！向前端投递纯净的源数据特征，配以规范的 `metadata.color_ramp`，在前端运用 `MapLibre` 原生能力完成极致渲染。
