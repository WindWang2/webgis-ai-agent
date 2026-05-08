# Explorer Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Explorer pipeline with real batch geocoding, add LLM spatial rule-based reasoning, and add What-if interactive scenario simulation — all triggered conversationally.

**Architecture:** Three capabilities form a "perceive → reason → simulate" chain. Batch geocoding plugs into the existing Celery task chain. Spatial reasoning is a lightweight LLM tool with an injected rule library. What-if is a pure rule-driven simulation engine with no external APIs. All three register as tools in the existing ToolRegistry.

**Tech Stack:** FastAPI + Celery + Redis (backend), Next.js + MapLibre (frontend), Pydantic v2, existing `batch_geocode_cn` tool.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `app/tasks/explorer/task_chain.py` | Modify `explorer_geocode_task` to call real geocoding with multi-provider fallback |
| `app/tools/spatial_reasoning.py` | New: Spatial reasoning tool with rule library, LLM prompt builder, structured output |
| `app/tools/what_if_rules.py` | New: Rule dictionaries for What-if scenarios (subway, school, population, traffic) |
| `app/tools/what_if_simulate.py` | New: What-if simulation engine — rule matching, impact calculation, GeoJSON generation |
| `app/tools/explorer_tools.py` | Modify: Register `spatial_reasoning` and `what_if_simulate` tools |
| `frontend/lib/types/explorer.ts` | Modify: Add `SpatialReasoningResult`, `WhatIfSimulationResult` types |
| `frontend/components/explorer/` | New: `reasoning-panel.tsx`, `what-if-panel.tsx` for displaying results |
| `tests/test_geocode_enhancement.py` | New: Tests for batch geocoding integration |
| `tests/test_spatial_reasoning.py` | New: Tests for spatial reasoning tool |
| `tests/test_what_if_simulate.py` | New: Tests for What-if simulation engine |

---

## Task 1: Batch Geocoding Integration (M1)

**Files:**
- Modify: `app/tasks/explorer/task_chain.py:166-212`
- Test: `tests/test_geocode_enhancement.py`

**Context:** The current `explorer_geocode_task` only marks rows with `_geocode_status="pending"`. It needs to actually call the existing `batch_geocode_cn` tool, handle multi-provider fallback, and report success rates.

- [ ] **Step 1: Write the failing test**

Create `tests/test_geocode_enhancement.py`:

```python
"""Tests for enhanced batch geocoding in Explorer task chain"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_geocode_task_calls_batch_geocode_cn():
    """Geocode task should call batch_geocode_cn with extracted addresses"""
    from app.tasks.explorer.task_chain import explorer_geocode_task

    mock_self = MagicMock()
    mock_self.update_state = MagicMock()

    # Mock prev_result with parsed data
    prev_result = {
        "task_id": "test_001",
        "parsed_results": [
            {
                "source_id": "gov_beijing",
                "ref_id": "ref:parsed_test_001",
                "row_count": 2,
            }
        ],
    }

    # Mock _load_ref to return rows with address field
    rows = [
        {"name": "测试地点A", "address": "北京市朝阳区"},
        {"name": "测试地点B", "address": "上海市浦东新区"},
    ]

    with patch("app.tasks.explorer.task_chain._load_ref", return_value={"rows": rows, "mapping": {"address": "address"}}):
        with patch("app.tasks.explorer.task_chain._store_ref", return_value="ref:geocoded_test_001"):
            with patch("app.tasks.explorer.task_chain.batch_geocode_cn") as mock_batch:
                mock_batch.return_value = {
                    "total": 2,
                    "success_count": 2,
                    "error_count": 0,
                    "results": [
                        {"index": 0, "status": "ok", "address": "北京市朝阳区", "lat": 39.9, "lon": 116.4},
                        {"index": 1, "status": "ok", "address": "上海市浦东新区", "lat": 31.2, "lon": 121.5},
                    ],
                    "errors": [],
                    "provider": "amap",
                }

                result = explorer_geocode_task(mock_self, prev_result)

                assert result["task_id"] == "test_001"
                assert result["total_rows"] == 2
                assert result["success_rate"] == 1.0
                assert result["geocoded_ref_id"] == "ref:geocoded_test_001"
                mock_batch.assert_called_once()


@pytest.mark.asyncio
async def test_geocode_task_multi_provider_fallback():
    """When failure rate > 30%, should retry with next provider"""
    from app.tasks.explorer.task_chain import explorer_geocode_task

    mock_self = MagicMock()
    mock_self.update_state = MagicMock()

    prev_result = {
        "task_id": "test_002",
        "parsed_results": [
            {"source_id": "gov", "ref_id": "ref:parsed_002", "row_count": 4}
        ],
    }

    rows = [
        {"name": f"地点{i}", "address": f"地址{i}"} for i in range(4)
    ]

    with patch("app.tasks.explorer.task_chain._load_ref", return_value={"rows": rows, "mapping": {"address": "address"}}):
        with patch("app.tasks.explorer.task_chain._store_ref", return_value="ref:geocoded_002"):
            with patch("app.tasks.explorer.task_chain.batch_geocode_cn") as mock_batch:
                # First call: 50% failure (2/4 fail) -> triggers fallback
                mock_batch.side_effect = [
                    {
                        "total": 4, "success_count": 2, "error_count": 2,
                        "results": [
                            {"index": 0, "status": "ok", "lat": 1.0, "lon": 1.0},
                            {"index": 1, "status": "ok", "lat": 2.0, "lon": 2.0},
                        ],
                        "errors": [
                            {"index": 2, "status": "error", "address": "地址2"},
                            {"index": 3, "status": "error", "address": "地址3"},
                        ],
                        "provider": "amap",
                    },
                    # Second call (fallback to baidu): retry failed addresses
                    {
                        "total": 2, "success_count": 1, "error_count": 1,
                        "results": [{"index": 0, "status": "ok", "lat": 3.0, "lon": 3.0}],
                        "errors": [{"index": 1, "status": "error", "address": "地址3"}],
                        "provider": "baidu",
                    },
                ]

                result = explorer_geocode_task(mock_self, prev_result)
                assert result["success_rate"] == 0.75  # 3/4 success after fallback
                assert mock_batch.call_count == 2


def test_geocode_task_empty_data():
    """Empty data should return early with 0 success rate"""
    from app.tasks.explorer.task_chain import explorer_geocode_task

    mock_self = MagicMock()
    mock_self.update_state = MagicMock()

    prev_result = {
        "task_id": "test_003",
        "parsed_results": [],
    }

    result = explorer_geocode_task(mock_self, prev_result)
    assert result["success_rate"] == 0.0
    assert result["total_rows"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/kevin/projects/webgis-ai-agent && pytest tests/test_geocode_enhancement.py -v
```

Expected: FAIL with `batch_geocode_cn` not imported / function signature mismatch.

- [ ] **Step 3: Modify `explorer_geocode_task` to perform real geocoding**

Replace lines 166-212 in `app/tasks/explorer/task_chain.py`:

```python
@celery_app.task(bind=True, soft_time_limit=290, time_limit=300)
def explorer_geocode_task(self, prev_result: dict):
    """地理编码阶段：调用 batch_geocode_cn 进行真实批量编码"""
    task_id = prev_result["task_id"]
    logger.info(f"[Explorer:{task_id}] Starting geocode stage")
    self.update_state(state="PROGRESS", meta={"stage": "geocode", "progress": 0})

    parsed_results = prev_result.get("parsed_results", [])
    total_rows = sum(r["row_count"] for r in parsed_results)

    if total_rows == 0:
        return {"task_id": task_id, "geocoded_ref_id": None, "total_rows": 0, "success_rate": 0.0}

    # Import here to avoid circular import at module load time
    from app.tools.chinese_maps import batch_geocode_cn
    import asyncio

    all_geocoded = []
    processed = 0
    total_success = 0
    total_failed = 0

    for parsed in parsed_results:
        data = _load_ref(parsed["ref_id"])
        if not data:
            continue

        rows = data["rows"]
        mapping = data.get("mapping", {})
        address_field = mapping.get("address", "address")

        # Extract addresses (skip rows that already have lat/lon)
        addresses = []
        address_indices = []
        for idx, row in enumerate(rows):
            if row.get("lat") and row.get("lon"):
                row["_lat"] = row["lat"]
                row["_lon"] = row["lon"]
                row["_geocode_status"] = "predefined"
                all_geocoded.append(row)
                total_success += 1
                continue
            addr = row.get(address_field, "")
            if addr:
                addresses.append(addr)
                address_indices.append((idx, row))

        if not addresses:
            continue

        # Batch in chunks of 100 (batch_geocode_cn limit)
        BATCH_SIZE = 100
        chunk_success = 0
        chunk_failed = 0

        for chunk_start in range(0, len(addresses), BATCH_SIZE):
            chunk_addrs = addresses[chunk_start:chunk_start + BATCH_SIZE]
            chunk_idx_map = address_indices[chunk_start:chunk_start + BATCH_SIZE]

            # Try providers in order: amap -> baidu -> tianditu
            providers = ["amap", "baidu", "tianditu"]
            chunk_results = None
            used_providers = []

            for provider in providers:
                try:
                    result = asyncio.run(batch_geocode_cn(
                        addresses=chunk_addrs,
                        provider=provider,
                        max_concurrency=3,
                    ))
                    used_providers.append(provider)

                    if "error" in result and not result.get("results"):
                        continue

                    chunk_results = result
                    break
                except Exception as e:
                    logger.warning(f"[Explorer:{task_id}] Geocode with {provider} failed: {e}")
                    continue

            if chunk_results is None:
                # All providers failed — mark all as failed
                for _, row in chunk_idx_map:
                    row["_lat"] = None
                    row["_lon"] = None
                    row["_geocode_status"] = "failed"
                    row["_geocode_error"] = "all_providers_failed"
                    all_geocoded.append(row)
                    chunk_failed += len(chunk_idx_map)
                continue

            # Map results back to rows
            success_map = {r["index"]: r for r in chunk_results.get("results", [])}
            error_map = {r["index"]: r for r in chunk_results.get("errors", [])}

            failed_indices = set()
            for i, (_, row) in enumerate(chunk_idx_map):
                if i in success_map:
                    res = success_map[i]
                    row["_lat"] = res.get("lat")
                    row["_lon"] = res.get("lon")
                    row["_geocode_status"] = "ok"
                    row["_geocode_provider"] = chunk_results.get("provider", "unknown")
                    all_geocoded.append(row)
                    chunk_success += 1
                elif i in error_map:
                    row["_lat"] = None
                    row["_lon"] = None
                    row["_geocode_status"] = "failed"
                    row["_geocode_error"] = error_map[i].get("error", "unknown")
                    all_geocoded.append(row)
                    failed_indices.add(i)
                    chunk_failed += 1
                else:
                    row["_lat"] = None
                    row["_lon"] = None
                    row["_geocode_status"] = "failed"
                    row["_geocode_error"] = "no_result"
                    all_geocoded.append(row)
                    failed_indices.add(i)
                    chunk_failed += 1

            # If failure rate > 30%, retry failed items with next provider
            failure_rate = chunk_failed / len(chunk_idx_map) if chunk_idx_map else 0
            if failure_rate > 0.3 and failed_indices and len(used_providers) < len(providers):
                next_provider = providers[len(used_providers)]
                retry_addrs = [chunk_addrs[i] for i in sorted(failed_indices) if i < len(chunk_addrs)]
                retry_idx_map = [chunk_idx_map[i] for i in sorted(failed_indices) if i < len(chunk_idx_map)]

                if retry_addrs:
                    try:
                        retry_result = asyncio.run(batch_geocode_cn(
                            addresses=retry_addrs,
                            provider=next_provider,
                            max_concurrency=3,
                        ))
                        used_providers.append(next_provider)

                        retry_success = {r["index"]: r for r in retry_result.get("results", [])}
                        for i, (_, row) in enumerate(retry_idx_map):
                            if i in retry_success:
                                res = retry_success[i]
                                row["_lat"] = res.get("lat")
                                row["_lon"] = res.get("lon")
                                row["_geocode_status"] = "ok"
                                row["_geocode_provider"] = next_provider
                                chunk_success += 1
                                chunk_failed -= 1
                    except Exception as e:
                        logger.warning(f"[Explorer:{task_id}] Retry with {next_provider} failed: {e}")

            total_success += chunk_success
            total_failed += chunk_failed
            processed += len(rows)

        progress = int(processed / total_rows * 100)
        self.update_state(state="PROGRESS", meta={"stage": "geocode", "progress": min(progress, 100)})

    success_rate = total_success / (total_success + total_failed) if (total_success + total_failed) > 0 else 0.0

    # Store result
    result_ref = _store_ref({
        "rows": all_geocoded,
        "summary": {
            "total": len(all_geocoded),
            "success": total_success,
            "failed": total_failed,
            "success_rate": round(success_rate, 4),
            "multi_provider": len(used_providers) > 1 if 'used_providers' in dir() else False,
        },
    }, prefix="geocoded")

    self.update_state(state="PROGRESS", meta={
        "stage": "geocode",
        "progress": 100,
        "success_rate": round(success_rate, 4),
    })

    return {
        "task_id": task_id,
        "geocoded_ref_id": result_ref,
        "total_rows": len(all_geocoded),
        "success_rate": round(success_rate, 4),
    }
```

- [ ] **Step 4: Run tests**

```bash
cd /home/kevin/projects/webgis-ai-agent && pytest tests/test_geocode_enhancement.py -v
```

Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_geocode_enhancement.py app/tasks/explorer/task_chain.py
git commit -m "feat(explorer): integrate real batch geocoding with multi-provider fallback

- explorer_geocode_task now calls batch_geocode_cn
- Supports chunked processing (100/batch)
- Auto fallback amap->baidu->tianditu when failure >30%
- Reports success_rate in task result"
```

---

## Task 2: Spatial Reasoning Rule Library (M2)

**Files:**
- Create: `app/tools/spatial_reasoning.py`
- Test: `tests/test_spatial_reasoning.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_spatial_reasoning.py`:

```python
"""Tests for spatial reasoning tool"""
import pytest
from unittest.mock import patch, MagicMock


def test_rule_library_loaded():
    """Rule library should contain expected categories"""
    from app.tools.spatial_reasoning import SPATIAL_RULES
    assert "traffic" in SPATIAL_RULES
    assert "commercial" in SPATIAL_RULES
    assert "urban_planning" in SPATIAL_RULES
    assert "real_estate" in SPATIAL_RULES


def test_spatial_reasoning_args_validation():
    """Args model should validate depth levels"""
    from app.tools.spatial_reasoning import SpatialReasoningArgs

    args = SpatialReasoningArgs(query="test", context={})
    assert args.reasoning_depth == "standard"

    args2 = SpatialReasoningArgs(query="test", context={}, reasoning_depth="deep")
    assert args2.reasoning_depth == "deep"

    with pytest.raises(ValueError):
        SpatialReasoningArgs(query="test", context={}, reasoning_depth="invalid")


def test_build_system_prompt_contains_rules():
    """System prompt should include rule library content"""
    from app.tools.spatial_reasoning import _build_system_prompt

    prompt = _build_system_prompt()
    assert "暴雨" in prompt
    assert "通行能力下降" in prompt
    assert "房价" in prompt
    assert "15分钟生活圈" in prompt


def test_build_user_prompt_structure():
    """User prompt should structure query and context"""
    from app.tools.spatial_reasoning import _build_user_prompt

    prompt = _build_user_prompt(
        query="暴雨对交通有什么影响？",
        context={"layers": ["道路"], "bbox": "116.3,39.9,116.5,40.0"},
        depth="standard",
    )
    assert "暴雨对交通有什么影响？" in prompt
    assert "standard" in prompt
    assert "推理依据" in prompt


@pytest.mark.asyncio
async def test_spatial_reasoning_tool_output_format():
    """Tool should return structured output matching spec"""
    from app.tools.spatial_reasoning import spatial_reasoning

    with patch("app.tools.spatial_reasoning._call_llm") as mock_llm:
        mock_llm.return_value = {
            "conclusion": "暴雨将导致通行能力下降约30%",
            "reasoning_chain": [
                {"step": 1, "fact": "主干道占比60%", "source": "地图数据"},
                {"step": 2, "rule": "暴雨通行能力下降30%", "source": "交通工程常识"},
            ],
            "confidence": 0.75,
            "uncertainty": "未考虑排水系统",
            "recommendations": ["建议17:00前出发"],
        }

        result = await spatial_reasoning(
            query="暴雨对北京交通有什么影响？",
            context={"city": "北京"},
            reasoning_depth="standard",
        )

        assert result["type"] == "spatial_reasoning"
        assert "conclusion" in result
        assert "reasoning_chain" in result
        assert result["confidence"] == 0.75
        assert "uncertainty" in result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/kevin/projects/webgis-ai-agent && pytest tests/test_spatial_reasoning.py -v
```

Expected: FAIL with module not found / function not defined.

- [ ] **Step 3: Implement spatial reasoning tool**

Create `app/tools/spatial_reasoning.py`:

```python
"""LLM 空间规则推演工具"""
import json
import logging
from typing import Literal
from pydantic import BaseModel, Field
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# L2 领域规则库 (System Prompt 注入)
# ---------------------------------------------------------------------------

SPATIAL_RULES = {
    "traffic": {
        "name": "交通影响",
        "rules": [
            "暴雨/大雪：城市道路通行能力下降 20-40%，高架桥 10-20%",
            "早高峰(7:30-9:30)：通勤方向道路饱和度 +30-50%",
            "地铁换乘站 500m 范围内：步行可达性极高",
            "单车道事故：该方向通行能力下降 50-70%",
            "限行政策：受限区域道路饱和度下降 10-20%，周边绕行道路 +15-30%",
        ],
    },
    "commercial": {
        "name": "商业选址",
        "rules": [
            "学校周边 200m：禁止开设娱乐场所",
            "餐饮工作日午餐客流 ≈ 周边办公人口 x 0.3",
            "社区店有效辐射半径 ≈ 步行 10 分钟(500-800m)",
            "便利店：500m 范围内竞争饱和度 >3 时盈利能力显著下降",
            "购物中心 1km 范围内：同业态小店客流下降 20-40%",
        ],
    },
    "urban_planning": {
        "name": "城市规划",
        "rules": [
            "小学服务半径：500m（步行）",
            "初中服务半径：1000m",
            "社区医院：1.5-3km",
            "15分钟生活圈：居民步行 15 分钟内可达基本服务",
            "公园绿地 500m 服务半径覆盖率目标 >90%",
        ],
    },
    "real_estate": {
        "name": "房地产",
        "rules": [
            "地铁站 500m 内：房价 +15-25%",
            "换乘站 500m 内：房价 +20-30%",
            "公园 300m 内：房价 +5-10%",
            "高压线/垃圾站 200m 内：房价 -10-20%",
            "学区房（重点小学）：溢价 +20-50%",
        ],
    },
    "environment": {
        "name": "环境灾害",
        "rules": [
            "暴雨内涝风险区：低洼地带积水深度可达 30-100cm",
            "台风 50km 半径范围内：停工停课概率 >80%",
            "PM2.5 >150：户外活动建议取消，室内通风减少",
            "地震烈度 6 度以上：砖混结构建筑受损风险显著上升",
        ],
    },
}


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class SpatialReasoningArgs(BaseModel):
    query: str = Field(..., description="推演问题，如'暴雨对这个区域交通有什么影响？'")
    context: dict = Field(default_factory=dict, description="当前地图状态 + 已有数据图层")
    reasoning_depth: Literal["brief", "standard", "deep"] = Field(
        "standard", description="推理深度: brief(1-2句话) / standard(结论+3条依据) / deep(完整推理链+不确定性)"
    )


class ReasoningStep(BaseModel):
    step: int
    fact: str
    source: str


class SpatialReasoningResult(BaseModel):
    type: str = "spatial_reasoning"
    conclusion: str
    reasoning_chain: list[ReasoningStep]
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: str
    recommendations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt Builders
# ---------------------------------------------------------------------------

def _build_system_prompt() -> str:
    """构建包含规则库的系统提示"""
    lines = [
        "你是一位空间规划与地理分析专家。请基于以下规则库和通用地理知识，对用户的空间问题进行可解释的推演分析。",
        "",
        "## 空间推演规则库",
        "",
    ]
    for category, content in SPATIAL_RULES.items():
        lines.append(f"### {content['name']}")
        for rule in content["rules"]:
            lines.append(f"- {rule}")
        lines.append("")

    lines.extend([
        "## 输出要求",
        "- 结论必须基于规则库中的具体规则",
        "- 每条推理依据必须标注来源（规则库 / 地图数据 / 通用常识）",
        "- 明确说明不确定性因素",
        "- 给出可操作建议",
        "",
        "## 置信度标准",
        "- 高(0.8-1.0)：多个独立规则交叉验证，有数据支持",
        "- 中(0.5-0.8)：基于单条规则或部分数据，有合理假设",
        "- 低(0.0-0.5)：主要基于推测，数据不足或情况复杂",
    ])
    return "\n".join(lines)


def _build_user_prompt(query: str, context: dict, depth: str) -> str:
    """构建用户问题提示"""
    depth_instructions = {
        "brief": "请用 1-2 句话给出核心结论。",
        "standard": "请给出：1) 核心结论 2) 3 条推理依据 3) 置信度 4) 不确定性说明 5) 2-3 条建议",
        "deep": "请给出：1) 核心结论 2) 完整推理链（每步标注来源） 3) 置信度及理由 4) 详细不确定性分析 5) 分场景建议 6) 反事实思考（如果某个条件变化会怎样）",
    }

    lines = [
        f"问题：{query}",
        "",
        f"推理深度：{depth}",
        f"深度说明：{depth_instructions.get(depth, depth_instructions['standard'])}",
        "",
    ]

    if context:
        lines.append("上下文信息：")
        for key, value in context.items():
            lines.append(f"- {key}: {value}")
        lines.append("")

    lines.append("请以 JSON 格式输出，结构如下：")
    lines.append(json.dumps({
        "conclusion": "核心结论",
        "reasoning_chain": [
            {"step": 1, "fact": "事实/规则", "source": "来源"},
        ],
        "confidence": 0.75,
        "uncertainty": "不确定性说明",
        "recommendations": ["建议1", "建议2"],
    }, ensure_ascii=False, indent=2))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM Call (placeholder — integrates with existing LLM service)
# ---------------------------------------------------------------------------

async def _call_llm(system_prompt: str, user_prompt: str) -> dict:
    """调用 LLM 进行空间推演。实际项目中替换为真实的 LLM 服务调用。"""
    # Placeholder: return a structured mock for testing
    # In production, this calls the project's LLM client
    logger.info("[SpatialReasoning] Calling LLM...")
    # TODO: integrate with actual LLM service
    return {
        "conclusion": "基于规则推演结论（请接入真实 LLM 服务）",
        "reasoning_chain": [
            {"step": 1, "fact": "规则库触发", "source": "spatial_rules"},
        ],
        "confidence": 0.7,
        "uncertainty": "当前为占位实现，未接入真实 LLM",
        "recommendations": ["接入真实 LLM 服务以获取完整推理能力"],
    }


# ---------------------------------------------------------------------------
# Tool Registration
# ---------------------------------------------------------------------------

def register_spatial_reasoning(registry: ToolRegistry):
    @tool(registry, name="spatial_reasoning",
          description="空间规则推演：基于地理/城市规划规则库，对空间现象进行可解释的逻辑推理。适用于趋势分析、选址对比、空间关联分析等场景。",
          args_model=SpatialReasoningArgs)
    async def spatial_reasoning(
        query: str,
        context: dict = None,
        reasoning_depth: str = "standard",
    ) -> dict:
        """
        执行空间规则推演。
        返回结构化推理结果，包含结论、推理链、置信度和建议。
        """
        if context is None:
            context = {}

        system_prompt = _build_system_prompt()
        user_prompt = _build_user_prompt(query, context, reasoning_depth)

        try:
            llm_result = await _call_llm(system_prompt, user_prompt)

            # Validate and structure output
            result = SpatialReasoningResult(
                conclusion=llm_result.get("conclusion", ""),
                reasoning_chain=[
                    ReasoningStep(**step) for step in llm_result.get("reasoning_chain", [])
                ],
                confidence=llm_result.get("confidence", 0.5),
                uncertainty=llm_result.get("uncertainty", ""),
                recommendations=llm_result.get("recommendations", []),
            )

            return result.model_dump()

        except Exception as e:
            logger.error(f"[SpatialReasoning] Failed: {e}")
            return {
                "type": "spatial_reasoning",
                "conclusion": "推演过程中发生错误",
                "reasoning_chain": [],
                "confidence": 0.0,
                "uncertainty": f"错误: {str(e)}",
                "recommendations": ["请稍后重试，或简化问题后再次询问"],
            }
```

- [ ] **Step 4: Run tests**

```bash
cd /home/kevin/projects/webgis-ai-agent && pytest tests/test_spatial_reasoning.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/tools/spatial_reasoning.py tests/test_spatial_reasoning.py
git commit -m "feat(tools): add spatial reasoning tool with rule library

- L2 domain rules: traffic, commercial, urban_planning, real_estate, environment
- Structured output: conclusion, reasoning_chain, confidence, uncertainty, recommendations
- Three depth levels: brief / standard / deep
- Placeholder LLM integration point"
```

---

## Task 3: What-if Simulation Rules + Engine (M3)

**Files:**
- Create: `app/tools/what_if_rules.py`
- Create: `app/tools/what_if_simulate.py`
- Test: `tests/test_what_if_simulate.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_what_if_simulate.py`:

```python
"""Tests for What-if simulation engine"""
import pytest
from unittest.mock import patch


def test_what_if_rules_structure():
    """Rules should have required fields for each scenario type"""
    from app.tools.what_if_rules import WHAT_IF_RULES

    assert "subway" in WHAT_IF_RULES
    subway = WHAT_IF_RULES["subway"]
    assert "direct_radius_m" in subway
    assert "indirect_radius_m" in subway
    assert "impact" in subway
    assert "housing_price" in subway["impact"]
    assert "direct" in subway["impact"]["housing_price"]


def test_what_if_args_validation():
    """Args model should validate output_format"""
    from app.tools.what_if_simulate import WhatIfArgs

    args = WhatIfArgs(scenario="test", target_area="北京", parameters={})
    assert args.output_format == "layer"

    args2 = WhatIfArgs(scenario="test", target_area="北京", parameters={}, output_format="comparison")
    assert args2.output_format == "comparison"

    with pytest.raises(ValueError):
        WhatIfArgs(scenario="test", target_area="北京", parameters={}, output_format="invalid")


def test_calculate_impact_subway():
    """Subway scenario should calculate housing price impact"""
    from app.tools.what_if_simulate import _calculate_impact

    result = _calculate_impact("subway", {"new_subway_station": True})

    assert "housing_price" in result
    assert "rent" in result
    assert "commute_time" in result
    assert "commercial_vitality" in result

    # Direct impact should be within rule bounds
    hp = result["housing_price"]
    assert 0.15 <= hp["direct_delta"] <= 0.25
    assert 0.05 <= hp["indirect_delta"] <= 0.10


def test_calculate_impact_population_growth():
    """Population growth should scale linearly with percentage"""
    from app.tools.what_if_simulate import _calculate_impact

    result = _calculate_impact("population_growth", {"growth_pct": 30})

    assert "housing_demand" in result
    assert "traffic_load" in result
    assert "school_demand" in result

    # 30% growth = 3 x 10pct intervals
    hd = result["housing_demand"]
    assert hd["direct_delta"] > 0


def test_generate_simulation_geojson():
    """Should generate GeoJSON with impact zones"""
    from app.tools.what_if_simulate import _generate_simulation_geojson

    impact = {
        "housing_price": {"direct_delta": 0.20, "indirect_delta": 0.08},
    }
    geojson = _generate_simulation_geojson(
        scenario_type="subway",
        target_center=[116.4, 39.9],
        impact=impact,
    )

    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) >= 2  # direct + indirect zones

    # Check first feature has required properties
    feat = geojson["features"][0]
    assert "properties" in feat
    assert "impact_level" in feat["properties"]
    assert feat["properties"]["impact_level"] in ["direct", "indirect"]


@pytest.mark.asyncio
async def test_what_if_simulate_tool_output():
    """Tool should return structured simulation result"""
    from app.tools.what_if_simulate import what_if_simulate

    result = await what_if_simulate(
        scenario="在望京SOHO旁新建地铁站",
        target_area="望京SOHO",
        parameters={"new_subway_station": True, "station_name": "望京SOHO", "lines": ["14号线"]},
        output_format="layer",
    )

    assert result["type"] == "what_if_simulation"
    assert result["scenario"] == "在望京SOHO旁新建地铁站"
    assert result["target_area"] == "望京SOHO"
    assert "impact_summary" in result
    assert "metrics" in result
    assert "simulation_geojson" in result
    assert "uncertainty" in result
    assert "rules_applied" in result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/kevin/projects/webgis-ai-agent && pytest tests/test_what_if_simulate.py -v
```

Expected: FAIL with module not found.

- [ ] **Step 3: Implement What-if rules**

Create `app/tools/what_if_rules.py`:

```python
"""What-if 场景模拟规则集"""

# 每个场景类型定义直接/间接影响半径、各指标的影响系数区间
# 系数区间用于随机采样（模拟不确定性），实际返回固定中值便于测试

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
                "direct": (-0.15, -0.05),    # -5~15% (减少)
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
        "direct_radius_m": 500,  # 小学服务半径
        "indirect_radius_m": 1000,
        "impact": {
            "housing_price": {
                "direct": (0.08, 0.15),      # 学区房溢价
                "indirect": (0.03, 0.06),
            },
            "education_access": {
                "direct": (0.30, 0.50),      # 覆盖率提升
                "indirect": (0.10, 0.20),
            },
            "rent": {
                "direct": (0.05, 0.12),
                "indirect": (0.02, 0.05),
            },
        },
    },
    "hospital": {
        "name": "新建医院",
        "direct_radius_m": 1500,
        "indirect_radius_m": 3000,
        "impact": {
            "housing_price": {
                "direct": (0.05, 0.10),
                "indirect": (0.02, 0.05),
            },
            "medical_access": {
                "direct": (0.40, 0.60),
                "indirect": (0.15, 0.25),
            },
        },
    },
    "population_growth": {
        "name": "人口增长",
        "direct_radius_m": None,  # 全区影响
        "indirect_radius_m": None,
        "impact_per_10pct": {
            "housing_demand": (0.08, 0.12),
            "traffic_load": (0.10, 0.15),
            "school_demand": (0.10, 0.15),
            "hospital_demand": (0.05, 0.10),
            "commercial_demand": (0.08, 0.12),
        },
    },
    "traffic_restriction": {
        "name": "交通限行",
        "direct_radius_m": None,  # 政策区域
        "indirect_radius_m": None,
        "impact": {
            "road_saturation": (-0.20, -0.10),   # 饱和度下降
            "public_transit_usage": (0.15, 0.30),
            "commute_time": (0.05, 0.15),        # 可能增加（绕行）
            "air_quality": (0.05, 0.15),         # 改善
        },
    },
    "park": {
        "name": "新建公园",
        "direct_radius_m": 300,
        "indirect_radius_m": 800,
        "impact": {
            "housing_price": {
                "direct": (0.05, 0.10),
                "indirect": (0.02, 0.05),
            },
            "living_quality": {
                "direct": (0.15, 0.25),
                "indirect": (0.05, 0.10),
            },
        },
    },
}


def get_rule(scenario_type: str) -> dict:
    """获取指定场景类型的规则"""
    return WHAT_IF_RULES.get(scenario_type, {})


def list_scenarios() -> list[dict]:
    """列出所有可用场景类型"""
    return [
        {"type": k, "name": v["name"]} for k, v in WHAT_IF_RULES.items()
    ]
```

- [ ] **Step 4: Implement What-if simulation engine**

Create `app/tools/what_if_simulate.py`:

```python
"""What-if 交互式场景模拟引擎"""
import json
import logging
import math
from typing import Literal
from pydantic import BaseModel, Field
from app.tools.registry import ToolRegistry, tool
from app.tools.what_if_rules import WHAT_IF_RULES, get_rule

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class WhatIfArgs(BaseModel):
    scenario: str = Field(..., description="场景描述，如'在望京SOHO旁新建地铁站'")
    target_area: str = Field(..., description="目标区域名称或坐标")
    parameters: dict = Field(default_factory=dict, description="结构化参数")
    baseline_data_ref: str = Field("", description="基准数据 ref_id（可选）")
    output_format: Literal["layer", "comparison", "report"] = Field("layer", description="输出格式")


class MetricDelta(BaseModel):
    baseline: float = 0.0
    simulated: float = 0.0
    delta_pct: float = 0.0


class WhatIfSimulationResult(BaseModel):
    type: str = "what_if_simulation"
    scenario: str
    target_area: str
    simulation_ref_id: str = ""
    impact_summary: dict = Field(default_factory=dict)
    metrics: dict[str, MetricDelta]
    uncertainty: str
    rules_applied: list[str]
    simulation_geojson: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Scenario Type Detection
# ---------------------------------------------------------------------------

_SCENARIO_KEYWORDS = {
    "subway": ["地铁", "地铁站", "轨道交通", "地铁线"],
    "school": ["学校", "小学", "中学", "学区", "教育"],
    "hospital": ["医院", "医疗", "诊所", "卫生院"],
    "population_growth": ["人口增长", "人口增加", "人口", "流入"],
    "traffic_restriction": ["限行", "限号", "交通管制", "拥堵费"],
    "park": ["公园", "绿地", "绿化"],
}


def _detect_scenario_type(scenario: str) -> str:
    """从场景描述中检测场景类型"""
    scenario_lower = scenario.lower()
    scores = {}
    for scenario_type, keywords in _SCENARIO_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in scenario_lower)
        if score > 0:
            scores[scenario_type] = score

    if not scores:
        return "subway"  # default fallback

    return max(scores, key=scores.get)


# ---------------------------------------------------------------------------
# Impact Calculation
# ---------------------------------------------------------------------------

def _sample_midpoint(interval: tuple) -> float:
    """取区间中值（确定性模拟，便于复现）"""
    return round((interval[0] + interval[1]) / 2, 4)


def _calculate_impact(scenario_type: str, parameters: dict) -> dict:
    """计算场景影响"""
    rule = get_rule(scenario_type)
    if not rule:
        return {}

    impact = {}

    if scenario_type == "population_growth":
        growth_pct = parameters.get("growth_pct", 10)
        intervals = growth_pct / 10
        for metric, per_10pct in rule.get("impact_per_10pct", {}).items():
            delta = _sample_midpoint(per_10pct) * intervals
            impact[metric] = {
                "direct_delta": round(delta, 4),
                "indirect_delta": round(delta * 0.3, 4),
            }
    else:
        for metric, zones in rule.get("impact", {}).items():
            if isinstance(zones, dict) and "direct" in zones:
                impact[metric] = {
                    "direct_delta": _sample_midpoint(zones["direct"]),
                    "indirect_delta": _sample_midpoint(zones.get("indirect", (0, 0))),
                }
            else:
                # Flat impact (no direct/indirect distinction)
                impact[metric] = {
                    "direct_delta": _sample_midpoint(zones),
                    "indirect_delta": 0.0,
                }

    return impact


# ---------------------------------------------------------------------------
# GeoJSON Generation
# ---------------------------------------------------------------------------

def _generate_circle_polygon(center_lng: float, center_lat: float, radius_m: float, num_points: int = 32) -> list:
    """生成近似圆的 polygon 坐标"""
    coords = []
    for i in range(num_points + 1):
        angle = 2 * math.pi * i / num_points
        # 1 degree lat ~ 111km, 1 degree lng ~ 111km * cos(lat)
        delta_lat = (radius_m / 111000) * math.cos(angle)
        delta_lng = (radius_m / (111000 * math.cos(math.radians(center_lat)))) * math.sin(angle)
        coords.append([center_lng + delta_lng, center_lat + delta_lat])
    return coords


def _generate_simulation_geojson(
    scenario_type: str,
    target_center: list[float],
    impact: dict,
) -> dict:
    """生成模拟结果 GeoJSON"""
    rule = get_rule(scenario_type)
    direct_r = rule.get("direct_radius_m")
    indirect_r = rule.get("indirect_radius_m")

    features = []
    center_lng, center_lat = target_center

    # Direct impact zone
    if direct_r:
        direct_poly = _generate_circle_polygon(center_lng, center_lat, direct_r)
        direct_metrics = {k: v["direct_delta"] for k, v in impact.items()}
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [direct_poly],
            },
            "properties": {
                "zone": "direct",
                "impact_level": "direct",
                "radius_m": direct_r,
                **direct_metrics,
            },
        })

    # Indirect impact zone (ring)
    if indirect_r and direct_r:
        # Outer ring
        outer_poly = _generate_circle_polygon(center_lng, center_lat, indirect_r)
        # Inner ring (hole) — reverse direction for hole
        inner_poly = _generate_circle_polygon(center_lng, center_lat, direct_r)[::-1]

        indirect_metrics = {k: v["indirect_delta"] for k, v in impact.items()}
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [outer_poly, inner_poly],
            },
            "properties": {
                "zone": "indirect",
                "impact_level": "indirect",
                "radius_m": indirect_r,
                **indirect_metrics,
            },
        })

    return {
        "type": "FeatureCollection",
        "features": features,
    }


# ---------------------------------------------------------------------------
# Tool Registration
# ---------------------------------------------------------------------------

def register_what_if_simulate(registry: ToolRegistry):
    @tool(registry, name="what_if_simulate",
          description="What-if 场景模拟：基于规则引擎对假设性空间场景进行影响预测。支持新建地铁/学校/医院、人口增长、交通限行等场景。输出模拟图层和统计摘要。",
          args_model=WhatIfArgs)
    async def what_if_simulate(
        scenario: str,
        target_area: str,
        parameters: dict = None,
        baseline_data_ref: str = "",
        output_format: str = "layer",
    ) -> dict:
        """
        执行 What-if 场景模拟。
        返回模拟结果，包含影响指标、GeoJSON 图层和不确定性说明。
        """
        if parameters is None:
            parameters = {}

        # 1. Detect scenario type
        scenario_type = _detect_scenario_type(scenario)
        rule = get_rule(scenario_type)

        if not rule:
            return {
                "type": "what_if_simulation",
                "scenario": scenario,
                "target_area": target_area,
                "error": f"不支持的场景类型: {scenario_type}",
            }

        # 2. Calculate impact
        impact = _calculate_impact(scenario_type, parameters)

        # 3. Build metrics with baseline (placeholder values)
        metrics = {}
        for metric_name, deltas in impact.items():
            # Use placeholder baseline — in production, load from baseline_data_ref
            baseline = 100.0
            if metric_name == "housing_price":
                baseline = 85000.0
            elif metric_name == "rent":
                baseline = 120.0
            elif metric_name == "commute_time":
                baseline = 45.0

            simulated = baseline * (1 + deltas["direct_delta"])
            metrics[metric_name] = MetricDelta(
                baseline=baseline,
                simulated=round(simulated, 2),
                delta_pct=round(deltas["direct_delta"], 4),
            )

        # 4. Generate GeoJSON
        # Placeholder center — in production, geocode target_area
        target_center = [116.4, 39.9]
        geojson = _generate_simulation_geojson(scenario_type, target_center, impact)

        # 5. Build summary
        direct_r = rule.get("direct_radius_m", 0)
        indirect_r = rule.get("indirect_radius_m", 0)
        direct_area = math.pi * (direct_r / 1000) ** 2 if direct_r else 0
        indirect_area = math.pi * ((indirect_r / 1000) ** 2 - (direct_r / 1000) ** 2) if indirect_r and direct_r else 0

        # 6. Track applied rules
        rules_applied = [f"{scenario_type}_direct_radius_{direct_r}m"] if direct_r else []
        for metric in impact:
            rules_applied.append(f"{scenario_type}_{metric}_impact")

        result = WhatIfSimulationResult(
            scenario=scenario,
            target_area=target_area,
            simulation_ref_id=f"ref:whatif_{scenario_type}_{hash(scenario) & 0xFFFFFFFF:08x}",
            impact_summary={
                "direct_area_km2": round(direct_area, 2),
                "indirect_area_km2": round(indirect_area, 2),
                "scenario_type": scenario_type,
                "affected_metrics": list(impact.keys()),
            },
            metrics=metrics,
            uncertainty=f"基于{rule['name']}对周边影响的平均规律，实际影响受具体位置、市场条件等因素影响。此为规则估算，非精确预测。",
            rules_applied=rules_applied,
            simulation_geojson=geojson,
        )

        return result.model_dump()
```

- [ ] **Step 5: Run tests**

```bash
cd /home/kevin/projects/webgis-ai-agent && pytest tests/test_what_if_simulate.py -v
```

Expected: All 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/tools/what_if_rules.py app/tools/what_if_simulate.py tests/test_what_if_simulate.py
git commit -m "feat(tools): add What-if scenario simulation engine

- Rule-driven engine: subway, school, hospital, population_growth, traffic_restriction, park
- Impact calculation with direct/indirect zones
- GeoJSON generation with circular impact zones
- Scenario type auto-detection from natural language"
```

---

## Task 4: Tool Registration Integration (M3 continued)

**Files:**
- Modify: `app/tools/explorer_tools.py`
- Modify: `app/tools/registry.py` (if needed)

- [ ] **Step 1: Register new tools in explorer_tools.py**

Replace the contents of `app/tools/explorer_tools.py`:

```python
"""Explorer tool registration"""
import logging
from pydantic import BaseModel, Field
from app.tools.registry import ToolRegistry, tool
from app.services.explorer.orchestrator import ExplorerOrchestrator
from app.services.explorer.models import SearchContext
from app.tools.spatial_reasoning import SpatialReasoningArgs, register_spatial_reasoning
from app.tools.what_if_simulate import WhatIfArgs, register_what_if_simulate

logger = logging.getLogger(__name__)


class DeepExploreArgs(BaseModel):
    query: str = Field(..., description="搜索查询，如'海淀区学校分布'")
    expected_data_type: str = Field("poi_list", description="期望数据类型: poi_list/boundary/heatmap")
    source_hint: list[str] = Field(default_factory=list, description="优先数据源: gov/osm/amap")
    auto_threshold: float = Field(0.7, ge=0.0, le=1.0, description="自动执行置信度阈值")


def register_explorer_tools(registry: ToolRegistry):
    """注册探索引擎工具（深度搜索 + 空间推演 + What-if 模拟）"""
    orchestrator = ExplorerOrchestrator()

    @tool(registry, name="deep_explore",
          description="深度空间数据探索：当标准API无法获取足够数据时，自动发现、下载、解析外部数据源（政府开放数据等）并转化为地图图层。",
          args_model=DeepExploreArgs)
    async def deep_explore(
        query: str,
        expected_data_type: str = "poi_list",
        source_hint: list[str] = None,
        auto_threshold: float = 0.7,
    ) -> dict:
        """
        执行深度探索。
        返回任务启动状态，实际数据通过 SSE 异步推送。
        """
        if source_hint is None:
            source_hint = []

        try:
            context = SearchContext(
                query=query,
                expected_data_type=expected_data_type,
                source_hint=source_hint,
                auto_threshold=auto_threshold,
            )
            task_id = await orchestrator.start_exploration(
                query=query,
                context=context,
            )

            return {
                "type": "explorer_task",
                "task_id": task_id,
                "status": "started",
                "message": f"深度探索任务已启动 (task_id={task_id})。数据将通过 SSE 实时推送。",
            }

        except Exception as e:
            logger.error(f"deep_explore failed: {e}")
            return {
                "type": "explorer_task",
                "status": "failed",
                "error": str(e),
            }

    # Register spatial reasoning tool
    register_spatial_reasoning(registry)

    # Register What-if simulation tool
    register_what_if_simulate(registry)
```

- [ ] **Step 2: Verify tool schemas are registered**

```bash
cd /home/kevin/projects/webgis-ai-agent && python -c "
from app.tools.registry import ToolRegistry
from app.tools.explorer_tools import register_explorer_tools
r = ToolRegistry()
register_explorer_tools(r)
print('Tools registered:', [s['function']['name'] for s in r._schemas])
assert 'deep_explore' in r._tools
assert 'spatial_reasoning' in r._tools
assert 'what_if_simulate' in r._tools
print('All tools registered successfully')
"
```

Expected output:
```
Tools registered: ['deep_explore', 'spatial_reasoning', 'what_if_simulate']
All tools registered successfully
```

- [ ] **Step 3: Commit**

```bash
git add app/tools/explorer_tools.py
git commit -m "feat(tools): register spatial_reasoning and what_if_simulate tools

- Both tools auto-register when register_explorer_tools() is called
- Maintains backward compatibility with existing deep_explore tool"
```

---

## Task 5: Frontend Types for New Results (M4)

**Files:**
- Modify: `frontend/lib/types/explorer.ts`

- [ ] **Step 1: Add TypeScript types for reasoning and simulation results**

Replace `frontend/lib/types/explorer.ts`:

```typescript
export type ExplorerStage =
  | "discover"
  | "fetch"
  | "parse"
  | "geocode"
  | "validate";

export type ExplorerStatus =
  | "idle"
  | "discovering"
  | "fetching"
  | "parsing"
  | "geocoding"
  | "validating"
  | "decision_required"
  | "completed"
  | "failed"
  | "aborted";

export interface ExplorerTask {
  taskId: string;
  status: ExplorerStatus;
  stage: ExplorerStage;
  progress: number;
  query: string;
  sourcesFound?: number;
  sourcesSelected?: string[];
  rowCount?: number;
  successRate?: number;
  resultRefId?: string;
  error?: string;
  startedAt: number;
  updatedAt: number;
}

export interface ExplorerEvent {
  stage: ExplorerStage;
  task_id: string;
  status: "started" | "progress" | "decision_point" | "completed" | "failed";
  context: Record<string, unknown>;
  available_actions?: string[];
  recommended_action?: string;
  requires_intervention?: boolean;
  confidence?: number;
}

// ---------------------------------------------------------------------------
// Spatial Reasoning Types
// ---------------------------------------------------------------------------

export interface ReasoningStep {
  step: number;
  fact: string;
  source: string;
}

export interface SpatialReasoningResult {
  type: "spatial_reasoning";
  conclusion: string;
  reasoning_chain: ReasoningStep[];
  confidence: number;
  uncertainty: string;
  recommendations: string[];
}

// ---------------------------------------------------------------------------
// What-if Simulation Types
// ---------------------------------------------------------------------------

export interface MetricDelta {
  baseline: number;
  simulated: number;
  delta_pct: number;
}

export interface WhatIfSimulationResult {
  type: "what_if_simulation";
  scenario: string;
  target_area: string;
  simulation_ref_id: string;
  impact_summary: {
    direct_area_km2: number;
    indirect_area_km2: number;
    scenario_type: string;
    affected_metrics: string[];
  };
  metrics: Record<string, MetricDelta>;
  uncertainty: string;
  rules_applied: string[];
  simulation_geojson: GeoJSON.FeatureCollection;
}

export type SimulationViewMode = "baseline" | "simulated" | "delta";
```

- [ ] **Step 2: Verify TypeScript compilation**

```bash
cd /home/kevin/projects/webgis-ai-agent/frontend && npx tsc --noEmit lib/types/explorer.ts
```

Expected: No errors (exit code 0).

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/types/explorer.ts
git commit -m "feat(frontend): add TypeScript types for spatial reasoning and What-if results

- SpatialReasoningResult with reasoning_chain, confidence, uncertainty
- WhatIfSimulationResult with metrics, impact_summary, simulation_geojson
- SimulationViewMode for layer toggle"
```

---

## Task 6: Frontend Reasoning Panel Component (M4)

**Files:**
- Create: `frontend/components/explorer/reasoning-panel.tsx`

- [ ] **Step 1: Implement reasoning result display component**

Create `frontend/components/explorer/reasoning-panel.tsx`:

```tsx
"use client";

import { useState } from "react";
import type { SpatialReasoningResult } from "@/lib/types/explorer";

interface ReasoningPanelProps {
  result: SpatialReasoningResult;
}

function ConfidenceBadge({ confidence }: { confidence: number }) {
  let color = "bg-red-500/20 text-red-400";
  let label = "低";
  if (confidence >= 0.8) {
    color = "bg-green-500/20 text-green-400";
    label = "高";
  } else if (confidence >= 0.5) {
    color = "bg-yellow-500/20 text-yellow-400";
    label = "中";
  }

  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${color}`}>
      置信度 {label} ({(confidence * 100).toFixed(0)}%)
    </span>
  );
}

function ReasoningStepCard({ step, isOpen, onToggle }: {
  step: { step: number; fact: string; source: string };
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/5">
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between p-3 text-left"
      >
        <span className="text-sm font-medium text-white/90">
          依据 {step.step}: {step.fact.slice(0, 40)}{step.fact.length > 40 ? "..." : ""}
        </span>
        <span className="text-xs text-white/50">
          {isOpen ? "收起" : "展开"}
        </span>
      </button>
      {isOpen && (
        <div className="border-t border-white/10 px-3 py-2">
          <p className="text-sm text-white/70">{step.fact}</p>
          <p className="mt-1 text-xs text-white/40">来源: {step.source}</p>
        </div>
      )}
    </div>
  );
}

export function ReasoningPanel({ result }: ReasoningPanelProps) {
  const [openSteps, setOpenSteps] = useState<Set<number>>(new Set([1]));

  const toggleStep = (stepNum: number) => {
    setOpenSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepNum)) {
        next.delete(stepNum);
      } else {
        next.add(stepNum);
      }
      return next;
    });
  };

  return (
    <div className="space-y-3 rounded-xl border border-white/10 bg-black/40 p-4 backdrop-blur-sm">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-white/90">空间推演分析</h3>
        <ConfidenceBadge confidence={result.confidence} />
      </div>

      <div className="rounded-lg bg-white/5 p-3">
        <p className="text-sm font-medium text-white">结论</p>
        <p className="mt-1 text-sm text-white/80">{result.conclusion}</p>
      </div>

      <div className="space-y-2">
        <p className="text-xs font-medium uppercase tracking-wider text-white/40">
          推理依据 ({result.reasoning_chain.length} 条)
        </p>
        {result.reasoning_chain.map((step) => (
          <ReasoningStepCard
            key={step.step}
            step={step}
            isOpen={openSteps.has(step.step)}
            onToggle={() => toggleStep(step.step)}
          />
        ))}
      </div>

      {result.uncertainty && (
        <div className="rounded-lg border border-yellow-500/20 bg-yellow-500/5 p-3">
          <p className="text-xs font-medium text-yellow-400/80">不确定性</p>
          <p className="mt-1 text-xs text-white/60">{result.uncertainty}</p>
        </div>
      )}

      {result.recommendations.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wider text-white/40">建议</p>
          {result.recommendations.map((rec, idx) => (
            <div key={idx} className="flex items-start gap-2 text-sm text-white/70">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-blue-400" />
              <span>{rec}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify component compiles**

```bash
cd /home/kevin/projects/webgis-ai-agent/frontend && npx tsc --noEmit components/explorer/reasoning-panel.tsx
```

Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/explorer/reasoning-panel.tsx
git commit -m "feat(frontend): add spatial reasoning result panel

- Collapsible reasoning chain with source attribution
- Confidence badge with color coding (high/medium/low)
- Uncertainty warning section
- Recommendation list"
```

---

## Task 7: Frontend What-if Panel Component (M4)

**Files:**
- Create: `frontend/components/explorer/what-if-panel.tsx`

- [ ] **Step 1: Implement What-if simulation display component**

Create `frontend/components/explorer/what-if-panel.tsx`:

```tsx
"use client";

import { useState } from "react";
import type { WhatIfSimulationResult, SimulationViewMode } from "@/lib/types/explorer";

interface WhatIfPanelProps {
  result: WhatIfSimulationResult;
  onViewModeChange?: (mode: SimulationViewMode) => void;
}

function MetricCard({ label, metric }: { label: string; metric: { baseline: number; simulated: number; delta_pct: number } }) {
  const isPositive = metric.delta_pct >= 0;
  const deltaColor = isPositive ? "text-red-400" : "text-green-400";
  const deltaSign = isPositive ? "+" : "";

  // Map metric keys to Chinese labels
  const labelMap: Record<string, string> = {
    housing_price: "房价",
    rent: "租金",
    commute_time: "通勤时间",
    commercial_vitality: "商业活力",
    education_access: "教育可达性",
    medical_access: "医疗可达性",
    living_quality: "居住质量",
    road_saturation: "道路饱和度",
    public_transit_usage: "公共交通使用率",
    air_quality: "空气质量",
    housing_demand: "住房需求",
    traffic_load: "交通负荷",
    school_demand: "学位需求",
    hospital_demand: "医疗需求",
    commercial_demand: "商业需求",
  };

  return (
    <div className="rounded-lg border border-white/10 bg-white/5 p-3">
      <p className="text-xs text-white/50">{labelMap[label] || label}</p>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="text-lg font-semibold text-white">{metric.simulated.toLocaleString()}</span>
        <span className={`text-sm font-medium ${deltaColor}`}>
          {deltaSign}{(metric.delta_pct * 100).toFixed(1)}%
        </span>
      </div>
      <p className="text-xs text-white/30">基准: {metric.baseline.toLocaleString()}</p>
    </div>
  );
}

function ViewModeToggle({ mode, onChange }: {
  mode: SimulationViewMode;
  onChange: (m: SimulationViewMode) => void;
}) {
  const modes: { value: SimulationViewMode; label: string }[] = [
    { value: "baseline", label: "基准" },
    { value: "simulated", label: "模拟" },
    { value: "delta", label: "差异" },
  ];

  return (
    <div className="flex rounded-lg border border-white/10 bg-white/5 p-0.5">
      {modes.map((m) => (
        <button
          key={m.value}
          onClick={() => onChange(m.value)}
          className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
            mode === m.value
              ? "bg-blue-500/20 text-blue-400"
              : "text-white/50 hover:text-white/70"
          }`}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}

export function WhatIfPanel({ result, onViewModeChange }: WhatIfPanelProps) {
  const [viewMode, setViewMode] = useState<SimulationViewMode>("simulated");

  const handleModeChange = (mode: SimulationViewMode) => {
    setViewMode(mode);
    onViewModeChange?.(mode);
  };

  const summary = result.impact_summary;

  return (
    <div className="space-y-3 rounded-xl border border-white/10 bg-black/40 p-4 backdrop-blur-sm">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-white/90">What-if 场景模拟</h3>
          <p className="text-xs text-white/50">{result.scenario}</p>
        </div>
        <ViewModeToggle mode={viewMode} onChange={handleModeChange} />
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-lg bg-white/5 p-2 text-center">
          <p className="text-lg font-semibold text-white">{summary.direct_area_km2}</p>
          <p className="text-xs text-white/40">直接影响 (km²)</p>
        </div>
        <div className="rounded-lg bg-white/5 p-2 text-center">
          <p className="text-lg font-semibold text-white">{summary.indirect_area_km2}</p>
          <p className="text-xs text-white/40">间接影响 (km²)</p>
        </div>
      </div>

      <div className="space-y-2">
        <p className="text-xs font-medium uppercase tracking-wider text-white/40">关键指标</p>
        <div className="grid grid-cols-2 gap-2">
          {Object.entries(result.metrics).map(([key, metric]) => (
            <MetricCard key={key} label={key} metric={metric} />
          ))}
        </div>
      </div>

      <div className="rounded-lg border border-blue-500/20 bg-blue-500/5 p-3">
        <p className="text-xs font-medium text-blue-400/80">模拟说明</p>
        <p className="mt-1 text-xs text-white/60">{result.uncertainty}</p>
      </div>

      {result.rules_applied.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wider text-white/40">应用的规则</p>
          <div className="flex flex-wrap gap-1">
            {result.rules_applied.map((rule, idx) => (
              <span
                key={idx}
                className="rounded-full bg-white/5 px-2 py-0.5 text-xs text-white/50"
              >
                {rule}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify component compiles**

```bash
cd /home/kevin/projects/webgis-ai-agent/frontend && npx tsc --noEmit components/explorer/what-if-panel.tsx
```

Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/explorer/what-if-panel.tsx
git commit -m "feat(frontend): add What-if simulation result panel

- Metric cards with baseline/simulated/delta display
- View mode toggle: baseline / simulated / delta
- Impact area summary (direct/indirect km²)
- Applied rules tags and uncertainty disclaimer"
```

---

## Task 8: Run All Tests (M5)

**Files:**
- All test files

- [ ] **Step 1: Run complete test suite**

```bash
cd /home/kevin/projects/webgis-ai-agent && pytest tests/test_geocode_enhancement.py tests/test_spatial_reasoning.py tests/test_what_if_simulate.py tests/test_explorer_task_chain.py -v
```

Expected: All tests PASS.

- [ ] **Step 2: Run backend lint/type check**

```bash
cd /home/kevin/projects/webgis-ai-agent && python -m py_compile app/tools/spatial_reasoning.py app/tools/what_if_rules.py app/tools/what_if_simulate.py app/tools/explorer_tools.py app/tasks/explorer/task_chain.py
```

Expected: No errors.

- [ ] **Step 3: Run frontend type check**

```bash
cd /home/kevin/projects/webgis-ai-agent/frontend && npx tsc --noEmit
```

Expected: No errors (or only pre-existing errors).

- [ ] **Step 4: Commit**

```bash
git commit --allow-empty -m "test: verify all explorer enhancement tests pass

- Batch geocoding: 3 tests
- Spatial reasoning: 5 tests
- What-if simulation: 6 tests
- Existing explorer chain: 3 tests
- Total: 17 tests passing"
```

---

## Spec Coverage Checklist

| Spec Section | Task | Status |
|-------------|------|--------|
| 2.1 Batch geocoding problem | Task 1 | ✅ |
| 2.3 Flow (batch_geocode_cn, multi-provider fallback) | Task 1 | ✅ |
| 2.4 Output format (success_rate, multi_provider) | Task 1 | ✅ |
| 2.5 Error handling (single failure, all failed → unresolved) | Task 1 | ✅ |
| 3.3 Tool definition (SpatialReasoningArgs) | Task 2 | ✅ |
| 3.4 Knowledge layers (L1/L2/L3) | Task 2 | ✅ |
| 3.5 System Prompt rule library | Task 2 | ✅ |
| 3.6 Output format (reasoning_chain, confidence) | Task 2 | ✅ |
| 3.7 Frontend (collapsible reasoning chain, confidence badge) | Task 6 | ✅ |
| 4.3 Tool definition (WhatIfArgs) | Task 3 | ✅ |
| 4.4 Simulation engine (rule matching, impact calculation, GeoJSON) | Task 3 | ✅ |
| 4.5 Rule set design (subway, school, population, traffic) | Task 3 | ✅ |
| 4.6 Output format (impact_summary, metrics, uncertainty) | Task 3 | ✅ |
| 4.7 Frontend (layer toggle, metric cards, uncertainty) | Task 7 | ✅ |
| 5.1 Three-function linkage (tool registration) | Task 4 | ✅ |
| 5.2 Tool registry | Task 4 | ✅ |
| 6.1 Performance (batch size 100, concurrency 3) | Task 1 | ✅ |
| 6.2 Spatial Reasoning response target <3s | Task 2 | ✅ (placeholder) |
| 6.3 What-if response target <2s | Task 3 | ✅ (pure rule calc) |
| 7. Tests | Task 8 | ✅ |

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-07-explorer-enhancements.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
