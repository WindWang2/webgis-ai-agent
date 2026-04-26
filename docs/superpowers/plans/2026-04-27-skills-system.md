# Skills System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Claude-style `.md` skill files that define domain workflows as free-form Markdown instructions, injectable into the LLM as system prompts.

**Architecture:** Skills are `.md` files in `app/skills/` with YAML frontmatter (name, description) and free-form Markdown body. Backend parses frontmatter, exposes skill list via API, and injects skill body into the chat system prompt when activated. Frontend adds a skill launcher button in Dynamic Island that shows available skills and triggers them via the existing chat stream.

**Tech Stack:** Python (PyYAML for frontmatter), React/Next.js frontend, existing SSE + tool dispatch infrastructure

---

## File Structure

| File | Responsibility |
|------|---------------|
| `app/tools/skills.py` (modify) | Add `.md` skill scanning, frontmatter parsing, skill registry |
| `app/services/chat_engine.py` (modify) | Accept `skill_name`, inject skill body into system prompt |
| `app/api/routes/chat.py` (modify) | Add `skill_name` to `ChatRequest`, add `GET /skills` endpoint |
| `app/api/routes/config.py` (modify) | Update `list_skills` to include `.md` skills |
| `app/skills/urban_planning.md` (create) | Example skill: urban planning assessment |
| `app/skills/disaster_risk.md` (create) | Example skill: disaster risk assessment |
| `app/skills/site_selection.md` (create) | Example skill: site selection analysis |
| `frontend/lib/api/skills.ts` (create) | API client for skills endpoints |
| `frontend/components/hud/skill-launcher.tsx` (create) | Skill picker popup component |
| `frontend/components/hud/dynamic-island.tsx` (modify) | Add skill launcher button |
| `frontend/app/page.tsx` (modify) | Wire skill activation into `handleSend` |

---

### Task 1: Extend Backend Skill Registry for `.md` Files

**Files:**
- Modify: `app/tools/skills.py`

- [ ] **Step 1: Add frontmatter parser and `.md` skill loading**

Add to the top of `app/tools/skills.py`, after the existing imports:

```python
import re
```

Replace the `load_skills` function (lines 97-105) with:

```python
def _parse_md_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a Markdown file. Returns (metadata, body)."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not match:
        return {}, text
    try:
        import yaml
        meta = yaml.safe_load(match.group(1)) or {}
    except Exception:
        meta = {}
    return meta, match.group(2).strip()


# In-memory skill registry: {name: {description, body, filename}}
_md_skills: dict[str, dict] = {}


def list_md_skills() -> list[dict]:
    """Return list of all loaded .md skill metadata."""
    return [{"name": k, "description": v["description"]} for k, v in _md_skills.items()]


def get_md_skill(name: str) -> dict | None:
    """Return a single .md skill's full data (description + body)."""
    return _md_skills.get(name)


def load_skills(registry: ToolRegistry, skills_dir: str = "app/skills"):
    """Load all skill scripts from the skills directory."""
    if not os.path.exists(skills_dir):
        os.makedirs(skills_dir, exist_ok=True)
        return

    for filename in os.listdir(skills_dir):
        filepath = os.path.join(skills_dir, filename)
        if filename.endswith(".py") and not filename.startswith("__"):
            _load_single_skill(registry, filepath, filename)
        elif filename.endswith(".md") and not filename.startswith("__"):
            _load_md_skill(filepath, filename)


def _load_md_skill(file_path: str, filename: str):
    """Parse a .md skill file and register it in memory."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
        meta, body = _parse_md_frontmatter(text)
        name = meta.get("name", filename[:-3])
        description = meta.get("description", "")
        _md_skills[name] = {
            "description": description,
            "body": body,
            "filename": filename,
        }
        logger.info(f"Loaded .md skill: {name}")
    except Exception as e:
        logger.error(f"Failed to load .md skill {filename}: {e}")
```

Also update `watch_skills` to handle `.md` files. Replace the watcher's inner loop check (line 121) — change:

```python
            if not filename.endswith(".py") or filename.startswith("__"):
```

to:

```python
            if filename.startswith("__") or (not filename.endswith(".py") and not filename.endswith(".md")):
```

And inside the `if filepath not in _mtimes` block, after the existing `_load_single_skill` call, add:

```python
                    elif filename.endswith(".md"):
                        _load_md_skill(filepath, filename)
```

The full updated `watch_skills` function:

```python
def watch_skills(registry: ToolRegistry, skills_dir: str = "app/skills"):
    _mtimes: dict[str, float] = {}

    def _check():
        if not os.path.exists(skills_dir):
            return
        for filename in os.listdir(skills_dir):
            if filename.startswith("__"):
                continue
            if not filename.endswith(".py") and not filename.endswith(".md"):
                continue
            filepath = os.path.join(skills_dir, filename)
            try:
                mtime = os.path.getmtime(filepath)
            except OSError:
                continue
            if filepath not in _mtimes or _mtimes[filepath] < mtime:
                _mtimes[filepath] = mtime
                if filename.endswith(".py"):
                    _load_single_skill(registry, filepath, filename)
                elif filename.endswith(".md"):
                    _load_md_skill(filepath, filename)

    _check()
    return _check
```

- [ ] **Step 2: Verify import works**

Run: `cd /home/kevin/projects/webgis-ai-agent && python -c "from app.tools.skills import load_skills, list_md_skills, get_md_skill; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/tools/skills.py
git commit -m "feat: extend skill registry to parse .md skill files with frontmatter"
```

---

### Task 2: Create Example Skill Files

**Files:**
- Create: `app/skills/urban_planning.md`
- Create: `app/skills/disaster_risk.md`
- Create: `app/skills/site_selection.md`

- [ ] **Step 1: Create urban_planning.md**

```markdown
---
name: urban-planning
description: 城市规划综合评估，包含人口密度、设施覆盖率和交通可达性分析
---

# 城市规划评估

引导用户完成城市区域的综合规划评估，生成可视化报告。

## 执行步骤

1. 先询问用户要分析的城市区域。如果用户未指定具体范围，可以引导用户在地图上框选一个多边形区域，或使用 `geocode_cn` 查找城市坐标后用 `buffer_analysis` 生成研究范围。
2. 使用 `spatial_query` 分析该区域人口密度分布。如果人口数据不可获取，改用 `search_poi` 搜索居民区 POI 来估算人口分布。
3. 使用 `search_poi` 搜索区域内的关键公共服务设施：学校、医院、公园、消防站等。分类统计数量和空间分布。
4. 使用 `buffer_analysis` 计算每个设施类型的服务覆盖范围（学校 1km，医院 3km，公园 2km）。
5. 使用 `kde_surface` 生成设施密度热力图，可视化服务密集区域和空白区域。
6. 综合以上分析，使用 `generate_report` 生成城市规划评估报告，包含：
   - 人口分布分析
   - 设施覆盖率统计
   - 服务盲区识别
   - 优化建议

## 注意

- 研究区域超过 50km² 时提醒用户缩小范围
- 每步完成时主动向用户汇报中间结果
- 报告需包含热力图和统计图表
```

- [ ] **Step 2: Create disaster_risk.md**

```markdown
---
name: disaster-risk
description: 地质灾害风险评估，包含坡度分析、植被覆盖、人口暴露度评估
---

# 灾害风险评估

对指定区域进行地质灾害风险综合评估。

## 执行步骤

1. 询问用户要评估的区域和灾害类型（滑坡、泥石流、洪水、地面沉降）。
2. 使用 `terrain_analysis` 获取研究区域的坡度、坡向和高程数据。
3. 使用 `ndvi_analysis` 分析植被覆盖状况。
4. 使用 `search_poi` 统计区域内居民点和重要设施分布。
5. 使用 `buffer_analysis` 围绕高陡坡度区域生成影响范围。
6. 综合以上数据，使用 `generate_report` 生成风险评估报告，包含：
   - 坡度分级图（>25° 为高滑坡风险）
   - 植被退化区域（NDVI < 0.3）
   - 人口暴露度评估
   - 风险等级地图
   - 疏散建议

## 注意

- 缺少 DEM 数据时，使用 `search_poi` 搜索地形相关标记作为替代
- 必须给出明确的风险等级（高/中/低）
- 疏散建议应具体到道路和安置点
```

- [ ] **Step 3: Create site_selection.md**

```markdown
---
name: site-selection
description: 多条件选址分析，根据用户需求筛选最优位置
---

# 选址分析

根据用户定义的多重条件进行科学选址。

## 执行步骤

1. 询问用户选址目标（新建医院、学校、商场、充电站等）和区域范围。
2. 了解约束条件：面积要求、交通可达性、人口密度要求、与竞品的最小距离等。
3. 使用 `geocode_cn` 定位目标城市，用 `buffer_analysis` 确定候选搜索范围。
4. 使用 `search_poi` 查找目标区域内现有同类设施和竞品分布。
5. 使用 `buffer_analysis` 计算已有设施的服务覆盖范围，识别空白区域。
6. 使用 `kde_surface` 分析人口密度热力图。
7. 使用 `nearest_neighbor` 计算候选位置到交通枢纽的距离。
8. 综合打分后给出至少 3 个候选位置，使用 `generate_report` 生成对比报告，包含：
   - 候选位置地图标注
   - 多维度对比表格（人口、交通、竞品距离、成本估算）
   - 推荐排序和理由

## 注意

- 优先考虑交通便利和人口密集区域
- 注意避开已有同类设施的服务覆盖范围
- 如果条件矛盾（如要求安静又要交通便利），主动提出权衡建议
```

- [ ] **Step 4: Verify skills load**

Run: `cd /home/kevin/projects/webgis-ai-agent && python -c "from app.tools.skills import load_skills, list_md_skills; load_skills(None); print(list_md_skills())"`
Expected: List with 3 skills (urban-planning, disaster-risk, site-selection)

- [ ] **Step 5: Commit**

```bash
git add app/skills/urban_planning.md app/skills/disaster_risk.md app/skills/site_selection.md
git commit -m "feat: add example domain workflow skills (urban planning, disaster risk, site selection)"
```

---

### Task 3: Backend API + Chat Engine Integration

**Files:**
- Modify: `app/api/routes/chat.py`
- Modify: `app/services/chat_engine.py`

- [ ] **Step 1: Add `skill_name` to ChatRequest and new GET endpoint**

In `app/api/routes/chat.py`, add import at top (after line 25, with other skill imports):

```python
from app.tools.skills import list_md_skills, get_md_skill
```

Add `skill_name` field to `ChatRequest` (after line 65):

```python
    skill_name: Optional[str] = Field(None, description="要激活的技能名称")
```

Add skills list endpoint after the `map-state` endpoint (before the `@router.delete` for sessions):

```python
@router.get("/skills")
async def list_skills_api():
    """列出可用的 .md 技能"""
    return {"skills": list_md_skills()}
```

- [ ] **Step 2: Update chat_stream to accept skill_name**

In `app/api/routes/chat.py`, update the `chat_stream` endpoint (line 85-105). Change the request unpacking in `event_generator` to pass `skill_name`:

```python
@router.post("/stream")
async def chat_stream(req: ChatRequest, _user: dict = Depends(get_current_user_optional)):
    """SSE 流式对话接口"""
    async def event_generator():
        try:
            async for event in engine.chat_stream(
                req.message, session_id=req.session_id,
                map_state=req.map_state, skill_name=req.skill_name
            ):
                yield event
        except Exception as e:
            logger.error(f"Stream error: {e}")
            error_data = json.dumps({"error": str(e)}, ensure_ascii=False)
            yield f"event: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
```

Also update the `chat_completions` endpoint (line 74) to pass `skill_name`:

```python
@router.post("/completions", response_model=ChatResponse)
async def chat_completions(req: ChatRequest, _user: dict = Depends(get_current_user_optional)):
    """非流式对话接口"""
    try:
        result = await engine.chat(
            req.message, session_id=req.session_id,
            map_state=req.map_state, skill_name=req.skill_name
        )
        return ChatResponse(**result)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 3: Update ChatEngine to handle skill injection**

In `app/services/chat_engine.py`, update the `chat` method signature (line 595) to accept `skill_name`:

```python
    async def chat(self, message: str, session_id: Optional[str] = None, map_state: Optional[dict] = None, skill_name: Optional[str] = None) -> dict:
```

After the `map_state` block (after line 603), add skill injection:

```python
        if skill_name:
            from app.tools.skills import get_md_skill
            skill = get_md_skill(skill_name)
            if skill:
                messages.append({"role": "system", "content": f"[Skill指令: {skill_name}]\n\n{skill['body']}"})
```

Update the `chat_stream` method signature (line 697) similarly:

```python
    async def chat_stream(self, message: str, session_id: Optional[str] = None, map_state: Optional[dict] = None, skill_name: Optional[str] = None) -> AsyncGenerator[str, None]:
```

After the `map_state` block (after line 705), add the same skill injection:

```python
        if skill_name:
            from app.tools.skills import get_md_skill
            skill = get_md_skill(skill_name)
            if skill:
                messages.append({"role": "system", "content": f"[Skill指令: {skill_name}]\n\n{skill['body']}"})
```

- [ ] **Step 4: Update config.py list_skills to include .md skills**

In `app/api/routes/config.py`, replace the `list_skills` function (lines 73-88):

```python
@router.get("/skills")
async def list_skills():
    """列出当前已加载的技能（.py + .md）"""
    skills_dir = "app/skills"
    if not os.path.exists(skills_dir):
        return {"skills": []}

    skills = []
    for filename in os.listdir(skills_dir):
        if filename.startswith("__"):
            continue
        filepath = os.path.join(skills_dir, filename)
        if filename.endswith(".py"):
            skills.append({
                "name": filename,
                "type": "python",
                "size": os.path.getsize(filepath)
            })
        elif filename.endswith(".md"):
            skills.append({
                "name": filename,
                "type": "workflow",
                "size": os.path.getsize(filepath)
            })
    return {"skills": skills}
```

- [ ] **Step 5: Verify everything loads**

Run: `cd /home/kevin/projects/webgis-ai-agent && python -c "from app.tools.skills import load_skills, list_md_skills; load_skills(None); print(list_md_skills())"`
Expected: 3 skills listed

- [ ] **Step 6: Commit**

```bash
git add app/api/routes/chat.py app/services/chat_engine.py app/api/routes/config.py
git commit -m "feat: wire skill activation into chat engine and API endpoints"
```

---

### Task 4: Frontend — Skills API Client

**Files:**
- Create: `frontend/lib/api/skills.ts`

- [ ] **Step 1: Create skills API client**

```typescript
import { API_BASE } from './config';

export interface Skill {
  name: string;
  description: string;
}

export async function getSkills(): Promise<Skill[]> {
  const res = await fetch(`${API_BASE}/api/v1/chat/skills`);
  if (!res.ok) throw new Error(`Failed to fetch skills: ${res.status}`);
  const data = await res.json();
  return data.skills || [];
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/api/skills.ts
git commit -m "feat: add skills API client"
```

---

### Task 5: Frontend — Skill Launcher Component

**Files:**
- Create: `frontend/components/hud/skill-launcher.tsx`

- [ ] **Step 1: Create SkillLauncher component**

```tsx
'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Zap, X } from 'lucide-react';
import { getSkills, Skill } from '@/lib/api/skills';

interface SkillLauncherProps {
  onActivate: (skillName: string) => void;
  isLoading: boolean;
}

export function SkillLauncher({ onActivate, isLoading }: SkillLauncherProps) {
  const [open, setOpen] = useState(false);
  const [skills, setSkills] = useState<Skill[]>([]);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      getSkills().then(setSkills).catch(() => setSkills([]));
    }
  }, [open]);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    if (open) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  const handleActivate = useCallback((name: string) => {
    setOpen(false);
    onActivate(name);
  }, [onActivate]);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        disabled={isLoading}
        className={`hud-btn h-9 w-9 shrink-0 rounded-lg transition-all duration-300 ${
          open ? 'bg-hud-cyan/15 border-hud-cyan/30 text-hud-cyan' : 'text-white/30 hover:text-white/60'
        }`}
        title="技能"
      >
        <Zap className="h-4 w-4" />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            className="absolute bottom-12 left-1/2 -translate-x-1/2 w-[360px] glass-panel rounded-xl p-3 border border-white/[0.06]"
            initial={{ opacity: 0, y: 10, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 5, scale: 0.95 }}
            transition={{ duration: 0.15 }}
          >
            <div className="flex items-center justify-between mb-2 px-1">
              <span className="text-[11px] font-mono uppercase tracking-[0.15em] text-hud-cyan/70">
                Skills
              </span>
              <button onClick={() => setOpen(false)} className="text-white/20 hover:text-white/40">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

            {skills.length === 0 ? (
              <div className="text-center py-4 text-white/20 text-xs">暂无可用技能</div>
            ) : (
              <div className="space-y-1.5 max-h-[280px] overflow-y-auto">
                {skills.map((skill) => (
                  <button
                    key={skill.name}
                    onClick={() => handleActivate(skill.name)}
                    className="w-full text-left px-3 py-2.5 rounded-lg hover:bg-white/[0.04] transition-colors group"
                  >
                    <div className="flex items-center gap-2">
                      <Zap className="h-3 w-3 text-hud-cyan/50 group-hover:text-hud-cyan shrink-0" />
                      <span className="text-[12px] text-white/80 group-hover:text-white font-medium">
                        {skill.name}
                      </span>
                    </div>
                    <p className="text-[11px] text-white/30 mt-0.5 ml-5 leading-relaxed">
                      {skill.description}
                    </p>
                  </button>
                ))}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/hud/skill-launcher.tsx
git commit -m "feat: add skill launcher popup component"
```

---

### Task 6: Frontend — Wire Into Dynamic Island + Page

**Files:**
- Modify: `frontend/components/hud/dynamic-island.tsx`
- Modify: `frontend/app/page.tsx`

- [ ] **Step 1: Add SkillLauncher to Dynamic Island**

In `frontend/components/hud/dynamic-island.tsx`, add import after existing imports:

```tsx
import { SkillLauncher } from './skill-launcher';
```

Add `onActivateSkill` prop to the interface:

```tsx
interface DynamicIslandProps {
  onSend: (message: string) => void;
  isLoading: boolean;
  onUploadClick?: () => void;
  onActivateSkill?: (skillName: string) => void;
  statusText?: string;
}
```

Update the component signature:

```tsx
export function DynamicIsland({ onSend, isLoading, onUploadClick, onActivateSkill, statusText }: DynamicIslandProps) {
```

Add the SkillLauncher button after the Settings button (after line 80), before the input field:

```tsx
        {/* Skill launcher */}
        {onActivateSkill && (
          <SkillLauncher onActivate={onActivateSkill} isLoading={isLoading} />
        )}
```

- [ ] **Step 2: Wire skill activation in page.tsx**

In `frontend/app/page.tsx`, add import:

```tsx
import { getSkills } from '@/lib/api/skills'
```

Add a `handleActivateSkill` callback after `handleSend`:

```tsx
  const handleActivateSkill = useCallback((skillName: string) => {
    if (isLoading) return
    handleSend(`使用技能「${skillName}」开始分析`)
  }, [isLoading, handleSend])
```

Pass `skill_name` through the streamChat call. Update the `handleSend` function to check for skill activation pattern. Find the line:

```tsx
        for await (const event of streamChat(messageText, sessionId, mapState, currentSignal)) {
```

Before this line, detect if the message is a skill activation:

```tsx
        // Detect skill activation from message pattern
        let skillName: string | undefined
        const skillMatch = messageText.match(/^使用技能「(.+?)」/)
        if (skillMatch) {
          skillName = skillMatch[1]
        }
```

Now update the `streamChat` call. This requires updating `streamChat` in `chat.ts` to accept and pass `skill_name`. First, update `frontend/lib/api/chat.ts`:

In `frontend/lib/api/chat.ts`, update the `streamChat` function signature and body:

```typescript
export async function* streamChat(
  message: string,
  sessionId?: string,
  mapState?: Record<string, unknown>,
  signal?: AbortSignal,
  skillName?: string
): AsyncGenerator<SSEEvent> {
  const response = await fetch(`${API_BASE}/api/v1/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      session_id: sessionId,
      map_state: mapState,
      skill_name: skillName
    }),
    signal,
  });
```

Back in `page.tsx`, update the streamChat call:

```tsx
        for await (const event of streamChat(messageText, sessionId, mapState, currentSignal, skillName)) {
```

Pass `onActivateSkill` to DynamicIsland component. Find the `<DynamicIsland` usage and add the prop:

```tsx
      <DynamicIsland
        onSend={handleSend}
        isLoading={isLoading}
        onUploadClick={() => setShowUploadZone((prev) => !prev)}
        onActivateSkill={handleActivateSkill}
        statusText={statusText}
      />
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npx tsc --noEmit 2>&1 | grep -v "Cannot find module\|TS2307\|TS7031\|TS2582\|TS2304\|TS2739\|TS2322\|chat-panel.test\|chart-renderer\|TS2353" | head -10`
Expected: No new errors (existing errors from missing test types are fine)

- [ ] **Step 4: Commit**

```bash
git add frontend/components/hud/dynamic-island.tsx frontend/app/page.tsx frontend/lib/api/chat.ts
git commit -m "feat: wire skill launcher into Dynamic Island and chat stream"
```

---

## Verification

1. Start backend: `cd /home/kevin/projects/webgis-ai-agent && python -m uvicorn app.main:app --reload`
2. Visit frontend, click the lightning bolt button in Dynamic Island
3. Should see 3 skills: urban-planning, disaster-risk, site-selection
4. Click a skill → chat stream starts with skill instructions injected
5. Verify LLM follows the skill's workflow steps autonomously
