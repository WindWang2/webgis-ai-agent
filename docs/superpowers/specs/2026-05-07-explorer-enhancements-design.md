# WebGIS AI Agent — Explorer 增强功能设计规格书

**版本**: Phase 5 补充  
**日期**: 2026-05-07  
**关联设计**: 2026-05-07-spatial-explorer-design.md  
**涵盖功能**: 批量地理编码完善 / LLM 空间规则推演 / What-if 交互式场景模拟

---

## 1. 总体架构

三个功能构成 **"感知 → 推演 → 模拟"** 的递进能力链，全部通过对话式交互触发。

```
用户对话输入
      │
      ├──▶ 批量地理编码 (数据闭环)
      │         └──▶ 真实数据图层 GeoJSON
      │
      ├──▶ 空间规则推演 (逻辑推理)
      │         └──▶ 文字分析 + 推理链
      │
      └──▶ What-if 场景模拟 (假设模拟)
                └──▶ 模拟图层 + 对比分析
                      │
                      └──▶ 联动: What-if + 规则推演深度分析
```

### 1.1 交互原则

- **全部对话式**：用户正常聊天，Agent 自主判断调用哪个工具
- **无专门面板**：What-if 参数通过对话提取，无需前端参数面板
- **结果可视化**：编码和模拟结果以图层形式上地图，推演结果以结构化文字呈现

---

## 2. 功能 1: 批量地理编码完善

### 2.1 问题

Explorer `geocode` 阶段当前只是标记 `_geocode_status="pending"`，未真正调用地理编码服务。

### 2.2 方案

复用现有 `batch_geocode_cn` 工具（高德/百度/天地图，最多 100 条/批），在 Celery task 内部调用。

### 2.3 流程

```
explorer_geocode_task:
  ① 从 parsed 数据中提取 address 字段
     ├─ 单次提取最多 100 条（batch_geocode_cn 上限）
     └─ 若超过 100，分多批次顺序处理

  ② 调用 batch_geocode_cn
     ├─ provider 优先级: amap → baidu → tianditu
     └─ max_concurrency: 3（避免触发限流）

  ③ 结果回写每行数据
     ├─ 成功: _lat, _lon, _geocode_status="ok"
     └─ 失败: _geocode_status="failed", _geocode_error="..."

  ④ 若失败率 > 30%，切换 provider 重试失败项
     ├─ 第二轮: baidu
     └─ 第三轮: tianditu

  ⑤ 生成结果报告
     ├─ total, success, failed
     ├─ success_rate
     └─ multi_provider: bool
```

### 2.4 输出格式

```json
{
  "type": "geocode_result",
  "total": 10440,
  "success": 9876,
  "failed": 564,
  "success_rate": 0.946,
  "multi_provider": true,
  "providers_used": ["amap", "baidu"],
  "result_ref_id": "ref:explorer_xxx_geocoded"
}
```

### 2.5 错误处理

- 单条编码失败不影响整体流程
- 全部 provider 都失败后，标记为 "unresolved" 并通知 Agent
- Agent 收到后可选择：接受部分结果 / 向用户说明 / 尝试其他策略

---

## 3. 功能 2: LLM 空间规则推演 (Spatial Reasoning)

### 3.1 定位

让 Agent 基于地理/城市规划常识和规则库，对空间现象做**可解释的推演**（非数据预测，而是逻辑推理）。

### 3.2 触发场景

| 场景类型 | 示例 |
|---------|------|
| 趋势/影响分析 | "暴雨对这个区域交通有什么影响？" |
| 选址对比 | "北京和上海哪个更适合开奶茶店？" |
| What-if 后追问 | "你预测房价涨 22%，这个预测可靠吗？" |
| 空间关联分析 | "为什么这个区域的学校密度特别高？" |

### 3.3 工具定义

```python
class SpatialReasoningArgs(BaseModel):
    query: str                # 推演问题
    context: dict             # 当前地图状态 + 已有数据图层
    reasoning_depth: str = "standard"  # brief / standard / deep
```

**depth 级别**:
- `brief`: 1-2 句话结论
- `standard`: 结论 + 3 条推理依据（默认）
- `deep`: 结论 + 完整推理链 + 不确定性分析 + 建议

### 3.4 知识分层

| 层级 | 来源 | 说明 |
|------|------|------|
| L1 通用常识 | LLM 内置 | "暴雨导致路面湿滑 → 车速降低 → 拥堵" |
| L2 领域规则 | System Prompt 注入 | 城市规划规范、交通工程公式 |
| L3 本地规范 | RAG 检索（预留） | 上传的城市总体规划、交通规划文本 |

### 3.5 System Prompt 规则库（部分示例）

```
## 空间推演规则库

### 交通影响
- 暴雨/大雪：城市道路通行能力下降 20-40%，高架桥 10-20%
- 早高峰(7:30-9:30)：通勤方向道路饱和度 +30-50%
- 地铁换乘站 500m 范围内：步行可达性极高
- 事故：单车道事故导致该方向通行能力下降 50-70%

### 商业选址
- 学校周边 200m：禁止开设娱乐场所
- 餐饮工作日午餐客流 ≈ 周边办公人口 × 0.3
- 社区店有效辐射半径 ≈ 步行 10 分钟(500-800m)
- 便利店：500m 范围内竞争饱和度 >3 时盈利能力显著下降

### 城市规划
- 小学服务半径：500m（步行）
- 初中服务半径：1000m
- 社区医院：1.5-3km
- 15分钟生活圈：居民步行 15 分钟内可达基本服务

### 房地产
- 地铁站 500m 内：房价 +15-25%
- 换乘站 500m 内：房价 +20-30%
- 公园 300m 内：房价 +5-10%
- 高压线/垃圾站 200m 内：房价 -10-20%
```

### 3.6 输出格式

```json
{
  "type": "spatial_reasoning",
  "conclusion": "暴雨将导致该区域通行能力下降约30%，预计晚高峰延长40分钟",
  "reasoning_chain": [
    {"step": 1, "fact": "该区域主干道占比60%", "source": "地图数据"},
    {"step": 2, "rule": "暴雨时主干道通行能力下降30%", "source": "交通工程常识"},
    {"step": 3, "inference": "60% × 30% = 18% 整体下降", "source": "计算"},
    {"step": 4, "rule": "通行能力下降18% → 排队长度增加约40%", "source": "交通流理论"}
  ],
  "confidence": 0.75,
  "uncertainty": "未考虑实时排水系统状态和事故概率",
  "recommendations": ["建议17:00前出发", "优先选择地铁出行"]
}
```

### 3.7 前端展示

- 推理链以**折叠面板**形式展示（每条依据可展开详情）
- 置信度用**颜色标签**（高=绿色，中=黄色，低=红色）
- 不确定性明确告知用户（"需要注意..."）

---

## 4. 功能 3: What-if 交互式场景模拟

### 4.1 定位

用户提出假设性场景，Agent 基于**规则驱动的模拟引擎**生成空间影响预测，输出模拟图层和统计摘要。

### 4.2 触发方式

用户用"假设/如果/假如"等关键词提问：
- "如果在望京 SOHO 旁边建个地铁站，周边房价会怎样？"
- "假设这个区域人口增长 30%，现有学校够吗？"
- "如果限行扩大到五环，对通勤有什么影响？"

### 4.3 工具定义

```python
class WhatIfArgs(BaseModel):
    scenario: str              # 场景描述（LLM 从用户话中提取）
    target_area: str           # 目标区域名称或坐标
    parameters: dict           # 结构化参数
    # 例: {"new_subway_station": true, "station_name": "望京SOHO", "lines": ["14号线"]}
    baseline_data_ref: str = ""  # 基准数据 ref_id（可选）
    output_format: str = "layer"  # layer / comparison / report
```

### 4.4 模拟引擎（规则驱动）

```
What-if 模拟引擎:
  ① 场景解析
     └─ 匹配 scenario_type: subway / school / hospital / population / policy / ...

  ② 加载对应规则集
     └─ app/tools/what_if_rules.py 中的规则字典

  ③ 空间影响范围计算
     ├─ 直接辐射区（高影响）
     ├─ 间接辐射区（中影响）
     └─ 无关区域（无影响）

  ④ 应用影响系数
     ├─ 每个区域类型有预设的系数区间
     └─ 随机采样区间内一个值（模拟不确定性）

  ⑤ 生成模拟 GeoJSON
     ├─ 每个影响区域一个 Feature
     ├─ properties: {baseline_value, simulated_value, delta_pct, impact_level}
     └─ 支持 choropleth / heatmap 渲染

  ⑥ 统计摘要
     ├─ 受影响区域面积
     ├─ 受影响 POI 数量（按类型）
     ├─ 预估人口影响
     └─ 不确定性说明
```

### 4.5 规则集设计

```python
# app/tools/what_if_rules.py

WHAT_IF_RULES = {
    "subway": {
        "name": "新建地铁站",
        "direct_radius_m": 500,
        "indirect_radius_m": 1500,
        "impact": {
            "housing_price": {
                "direct": (0.15, 0.25),      # +15~25%
                "indirect": (0.05, 0.10),    # +5~10%
            },
            "rent": {
                "direct": (0.10, 0.18),
                "indirect": (0.03, 0.06),
            },
            "commute_time": {
                "direct": (-0.15, -0.05),    # -5~15%
                "indirect": (-0.05, 0.0),
            },
            "commercial_vitality": {
                "direct": (0.20, 0.40),
                "indirect": (0.05, 0.15),
            },
        },
    },
    "school": {
        "name": "新建学校",
        "service_radius_m": 500,  # 小学
        "impact": {
            "housing_price": {
                "direct": (0.08, 0.15),      # 学区房溢价
            },
            "education_access": {
                "direct": (0.30, 0.50),      # 覆盖率提升
            },
        },
    },
    "population_growth": {
        "name": "人口增长",
        "impact_per_10pct": {
            "housing_demand": (0.08, 0.12),
            "traffic_load": (0.10, 0.15),
            "school_demand": (0.10, 0.15),
            "hospital_demand": (0.05, 0.10),
        },
    },
    "traffic_restriction": {
        "name": "交通限行",
        "impact": {
            "road_saturation": (-0.20, -0.10),  # 饱和度下降
            "public_transit_usage": (0.15, 0.30),
            "commute_time": (0.05, 0.15),        # 可能增加（绕行）
        },
    },
}
```

### 4.6 输出格式

```json
{
  "type": "what_if_simulation",
  "scenario": "新建地铁站",
  "target_area": "望京SOHO",
  "simulation_ref_id": "ref:whatif_xxx",
  "impact_summary": {
    "direct_area_km2": 0.79,
    "indirect_area_km2": 7.07,
    "affected_residential": 1200,
    "affected_commercial": 85,
  },
  "metrics": {
    "housing_price": {"baseline": 85000, "simulated": 102000, "delta_pct": 0.20},
    "rent": {"baseline": 120, "simulated": 138, "delta_pct": 0.15},
    "commute_time": {"baseline": 45, "simulated": 38, "delta_pct": -0.16},
  },
  "uncertainty": "基于北京地铁对房价影响的平均规律，实际涨幅受线路重要性、周边存量交通等因素影响",
  "rules_applied": ["subway_direct_radius_500m", "subway_price_impact_15_25pct"]
}
```

### 4.7 前端展示

- **模拟图层**：与基准图层叠加，颜色表示影响强度
- **切换按钮**："显示基准 / 显示模拟 / 显示差异"
- **统计卡片**：关键指标对比（房价、租金、通勤时间）
- **不确定性提示**：明确告知用户这是基于规则的估算，非精确预测

---

## 5. 三功能联动设计

### 5.1 典型场景

**用户**: "如果在海淀黄庄新增地铁站，周边房价会怎样？"

**Agent 推理链**:
```
① IntentDetector → 识别 What-if 场景
② WhatIf 工具 → 生成模拟数据（房价 +20%，租金 +15%）
③ Agent 自动输出初步结论
   "模拟显示：新建地铁站后，周边 500m 内房价预计上涨 15-25%，
    租金上涨 10-18%。"

④ Agent 追问用户：
   "你想深入了解：
    A) 这个预测基于哪些规则？
    B) 和真实地铁站周边涨幅对比？
    C) 对周边商业的影响？"

若用户选 A:
⑤ Spatial Reasoning → 展示推理链
   "依据：北京地铁对房价影响的实证研究显示..."

若用户选 B:
⑥ deep_explore → 搜索真实案例数据
   "搜索北京近年新建地铁站周边房价变化..."
```

### 5.2 工具注册

新增三个工具注册到 `ToolRegistry`:

| 工具名 | 功能 | 注册文件 |
|--------|------|---------|
| `batch_geocode_cn` | 批量地理编码 | 已存在（`app/tools/chinese_maps.py`） |
| `spatial_reasoning` | 空间规则推演 | `app/tools/spatial_reasoning.py`（新增） |
| `what_if_simulate` | What-if 模拟 | `app/tools/what_if_simulate.py`（新增） |

---

## 6. 性能与稳定性

### 6.1 批量地理编码

- 单批 100 条，超时 60s
- 多 provider fallback 最多 3 轮
- 总数据量 >1000 条时，Celery task soft_time_limit 扩展至 600s

### 6.2 Spatial Reasoning

- 轻量 LLM 调用（max_tokens=2048）
- 规则库常驻内存（System Prompt 注入）
- 响应时间目标：<3s

### 6.3 What-if 模拟

- 纯规则计算，无外部 API 调用
- GeoJSON 生成在内存中完成
- 响应时间目标：<2s

---

## 7. 测试策略

| 功能 | 测试类型 | 覆盖点 |
|------|---------|--------|
| 批量地理编码 | 集成测试 | 多批次处理、provider fallback、失败记录 |
| Spatial Reasoning | 单元测试 | 规则库加载、推理链格式、置信度计算 |
| What-if | 单元测试 | 规则匹配、影响计算、GeoJSON 生成 |
| 联动 | 集成测试 | 端到端场景（What-if → Reasoning → Explore） |

---

## 8. 里程碑

| 阶段 | 内容 | 预计工时 |
|------|------|---------|
| **M1** | 批量地理编码：接入 batch_geocode_cn，多 provider fallback | 1d |
| **M2** | Spatial Reasoning：规则库 + 工具注册 + 推理链输出 | 1d |
| **M3** | What-if 模拟：规则集 + 模拟引擎 + GeoJSON 生成 | 1.5d |
| **M4** | 前端集成：模拟图层切换、推理链展示、统计卡片 | 1d |
| **M5** | 测试 + 文档 | 0.5d |
| **合计** | | **5d** |

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 规则库覆盖不足 | What-if/Reasoning 结果不准确 | 初始规则集聚焦高频场景（地铁、学校、人口），后续迭代扩展 |
| 批量编码 QPS 超限 | 任务超时失败 | provider health tracker 自动降级 + 指数退避重试 |
| LLM 推理幻觉 | 给出错误规则引用 | 推理链必须标注规则来源，用户可质疑 |
| 模拟结果误导用户 | 当作精确预测使用 | 前端强制展示不确定性说明 + "模拟估算，非精确预测" 免责声明 |
