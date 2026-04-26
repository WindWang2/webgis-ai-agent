---
name: skills-system
description: Claude-style .md skill files for domain workflow automation
date: 2026-04-27
status: approved
---

# Skills System Design

## Overview

Add a Claude Code-style skill system to the WebGIS AI Agent. Skills are `.md` files in `app/skills/` that contain free-form Markdown instructions for the LLM. When a skill is activated, its body is injected into the chat as a system message, and the LLM autonomously follows the instructions using existing tools.

## Skill Format

Each skill is a Markdown file with YAML frontmatter:

```markdown
---
name: urban-planning
description: 用于城市规划综合评估，包含人口、设施、交通分析
---

# 城市规划评估

引导用户完成城市区域的综合规划评估。

## 执行步骤

1. 先询问用户要分析的城市区域，可以引导用户在地图上框选
2. 使用 `spatial_query` 分析该区域人口密度分布
3. 使用 `search_poi` 搜索学校、医院、公园等关键设施
4. 如果用户关注交通，使用 `multi_ring_buffer` 计算设施服务覆盖范围
5. 使用 `generate_report` 汇总生成城市规划评估报告

## 注意

- 人口数据不可用时，改用 POI 密度估算
- 研究区域超过 50km² 时提醒用户缩小范围
- 每步完成时主动向用户汇报中间结果
```

### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Unique skill identifier (kebab-case) |
| `description` | yes | One-line summary for discovery/matching |

### Body

Free-form Markdown. The LLM reads and interprets the instructions autonomously. No structured parsing, no step engine, no parameter schema. The LLM asks the user for input naturally through conversation.

## Backend

### File Changes

| File | Change |
|------|--------|
| `app/skills/*.md` | New skill definition files |
| `app/tools/skills.py` | Extend: scan `.md` files, parse frontmatter, expose skill list |
| `app/api/routes/chat.py` | New `GET /skills` endpoint, extend `ChatRequest` with optional `skill_name` |
| `app/services/chat_engine.py` | Extend `chat_stream()`: when `skill_name` provided, inject skill body as system message |

### Discovery

On startup, scan `app/skills/` directory:

- `.md` files → parse YAML frontmatter (name, description) → keep body as instruction text
- `.py` files → existing `register_skills()` pattern unchanged

Store in-memory dict: `{name: {description, body, filename}}`

### API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/v1/skills` | List all skills: `[{name, description}]` |
| `GET /api/v1/skills/{name}` | Get single skill metadata + body |
| `POST /api/v1/chat/stream` | Existing, extended with optional `skill_name` field |

### Skill Activation Flow

1. Frontend sends `POST /chat/stream` with `skill_name: "urban-planning"`
2. `chat_stream()` looks up skill by name
3. Injects skill body as system message before user message: `[Skill指令]\n{skill.body}`
4. LLM reads instructions and autonomously calls tools via existing tool dispatch
5. SSE stream proceeds normally — no new event types needed

### Chat-triggered Activation

The system prompt includes a summary of available skills:

```
可用技能 (Skills):
- urban-planning: 用于城市规划综合评估
- disaster-risk: 灾害风险评估分析

当用户的请求匹配某个技能时，回复: {"action": "run_skill", "skill": "<name>"}
```

When the LLM returns this JSON action, the backend auto-activates the skill and re-injects.

## Frontend

### File Changes

| File | Change |
|------|--------|
| `frontend/components/hud/skill-launcher.tsx` | New: skill list popup triggered from Dynamic Island |
| `frontend/components/hud/dynamic-island.tsx` | Add skill launcher button |
| `frontend/lib/api/skills.ts` | New: `getSkills()` API call |
| `frontend/app/page.tsx` | Wire skill activation into `handleSend` |

### UX Flow

1. User clicks skill button (lightning icon) in Dynamic Island
2. Popup shows available skills as cards: `[name] description`
3. User clicks a skill → sends `streamChat(message, sessionId, mapState, signal)` with `skill_name` in request body
4. Existing chat SSE stream handles the rest — LLM follows skill instructions, tools execute, layers render

### Chat-triggered

When user types something matching a skill (e.g., "帮我做城市规划评估"), the LLM may activate the skill automatically. Frontend detects the skill activation SSE event and shows a brief skill indicator.

## Example Skills

### `app/skills/disaster-risk.md`

```markdown
---
name: disaster-risk
description: 地质灾害风险评估，包含坡度、植被、人口暴露分析
---

# 灾害风险评估

对指定区域进行地质灾害风险综合评估。

## 分析流程

1. 询问用户要评估的区域和灾害类型（滑坡、泥石流、洪水）
2. 使用 `terrain_analysis` 获取坡度和坡向数据
3. 使用 `ndvi_analysis` 分析植被覆盖状况
4. 使用 `search_poi` 统计区域内居民点分布
5. 综合以上数据，使用 `generate_report` 生成风险评估报告

## 注意

- 坡度大于 25° 的区域标记为高滑坡风险
- NDVI 低于 0.3 的区域标记为植被退化高风险
- 报告需包含风险等级地图和疏散建议
```

### `app/skills/site-selection.md`

```markdown
---
name: site-selection
description: 选址分析，根据多条件筛选最优位置
---

# 选址分析

根据用户定义的条件进行多因素选址分析。

## 分析流程

1. 询问用户选址目标（如：新建医院、学校选址、商业选址）
2. 了解约束条件（面积要求、交通便利、人口密度、竞品距离等）
3. 在目标区域内使用 `search_poi` 查找现有同类设施
4. 使用 `buffer_analysis` 计算已有设施的服务范围
5. 使用 `kde_surface` 分析候选区域的热力分布
6. 综合打分后，使用 `generate_report` 生成选址建议报告

## 注意

- 优先考虑交通便利和人口密集区域
- 注意避开已存在同类设施的服务范围
- 给出至少 3 个候选位置并对比优劣
```

## What This Does NOT Include

- No step engine / pipeline parser
- No parameter form rendering from schema
- No skill marketplace / remote sync
- No skill versioning beyond file-level git
- No sandboxing beyond existing AST validation for `.py` skills

These can be added incrementally later without breaking the base system.
