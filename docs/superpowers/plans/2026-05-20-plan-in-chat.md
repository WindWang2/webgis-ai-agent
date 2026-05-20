# 聊天里展示 AI 执行计划 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 plan-first 智能体循环对用户可见——AI 制定的计划与逐步打勾在聊天里实时呈现为助理消息顶部的"执行计划"卡片。

**Architecture:** 后端在 `chat_engine.chat_stream` 三个钩子点 yield 三个新 SSE 事件（`plan_ready` / `plan_step_done` / `plan_finalized`）；前端在 `app/page.tsx` 监听并把 `AgentPlanState` 挂到 thinking 消息，由新的 `PlanCard` 组件渲染。纯加法、静默降级——任何环节失败计划卡不显示但主流程不受影响。

**Tech Stack:** Python 3 / pytest (后端)；TypeScript / React / Vitest / React Testing Library (前端)。

---

## 命名约定（重要）

前端 `m.plan` 字段已被 **Plan Mode `propose_plan`** 工具的审批 UI 占用（`PlanProposalPayload` + `PlanProposalCard`，位于 `frontend/components/chat/plan-proposal-card.tsx` 与 `frontend/components/sidebar/chat-tab.tsx:239-254`）。本 plan **不**复用 `plan` 字段，新增字段命名为 **`agentPlan`**，类型为 **`AgentPlanState`**，组件命名为 **`PlanCard`**（文件 `frontend/components/chat/plan-card.tsx`——不与 `plan-proposal-card.tsx` 重名）。

## 文件结构

**后端：**

| 文件 | 职责 | 动作 |
|------|------|------|
| `app/services/chat_engine.py` | `_maybe_plan` 返回 Plan；`_log_tool_decision` 接收 step_n；`chat_stream` 三处 yield 新事件 | 修改 |
| `app/utils/sse.py` | 文件首部注释追加新事件契约 | 修改 |
| `tests/test_chat_engine_planning.py` | 既有 + 新增 4 个 SSE 发出测试 | 修改 |

**前端：**

| 文件 | 职责 | 动作 |
|------|------|------|
| `frontend/lib/types/agent-plan.ts` | `AgentPlanStepState` + `AgentPlanState` 类型 | 新建 |
| `frontend/app/page.tsx` | Message 类型加 `agentPlan` 字段；SSE handler 加 3 分支 | 修改 |
| `frontend/components/chat/plan-card.tsx` | PlanCard 组件 | 新建 |
| `frontend/components/chat/plan-card.test.tsx` | PlanCard vitest 测试 | 新建 |
| `frontend/components/sidebar/chat-tab.tsx` | 在 msg.think 之后、msg.content 之前挂载 PlanCard | 修改 |

---

### Task 1: 重构 `_log_tool_decision` 接受 step_n 参数

**Why first:** 当前 `_log_tool_decision` 内部调用 `planner.mark_step_done`，但 chat_stream 也需要这个返回值发 SSE 事件。把 mark_step_done 调用上提到调用方，让两者共用一个返回值。这是后续任务的基础。

**Files:**
- Modify: `app/services/chat_engine.py:326-370`（`_log_tool_decision`）
- Modify: `app/services/chat_engine.py:585-588`（调用处）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_chat_engine_planning.py` 末尾：

```python
def test_log_tool_decision_accepts_step_n_parameter(engine, tmp_path, monkeypatch):
    """_log_tool_decision 必须接收 step_n 参数而不是内部计算。"""
    from app.services.chat import decision_log
    captured: list = []
    monkeypatch.setattr(decision_log, "log_tool_decision",
                        lambda rec: captured.append(rec))
    # 直接调用 _log_tool_decision，传入 step_n=2
    engine._log_tool_decision(
        session_id="sess-T1",
        round_index=0,
        message="test",
        tool_name="buffer_analysis",
        tool_args={},
        outcome={"is_error": False, "result": {"ok": True}},
        subset_size=5,
        step_n=2,
    )
    assert len(captured) == 1
    assert captured[0].plan_step_matched == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chat_engine_planning.py::test_log_tool_decision_accepts_step_n_parameter -v`
Expected: FAIL with `TypeError: _log_tool_decision() got an unexpected keyword argument 'step_n'`

- [ ] **Step 3: 修改 `_log_tool_decision` 签名与实现**

在 `app/services/chat_engine.py`，把 `_log_tool_decision` 的签名和 ToolDecisionRecord 构造改成：

```python
    def _log_tool_decision(
        self,
        session_id: str,
        round_index: int,
        message: str,
        tool_name: str,
        tool_args: dict,
        outcome: dict,
        subset_size: int,
        step_n: int | None = None,
    ) -> None:
        """落一条工具决策记录。可观测性，绝不影响主流程。

        subset_size 由调用方传入本轮已算好的工具子集大小——不在此处重算，
        因为 select_schemas 会衰减 ToolCatalog 的 sticky TTL，重复调用会
        让 sticky domain 过早失效。

        step_n 由调用方先调用 planner.mark_step_done 算好，本方法只写入
        决策日志——与 chat_stream 的 SSE 事件共用一个 step_n 值。
        """
        from app.services.chat import planner
        from app.services.chat.decision_log import ToolDecisionRecord, log_tool_decision
        from app.services.chat.dispatcher import is_suspicious_result

        if outcome.get("is_error"):
            quality = "error"
        elif is_suspicious_result(outcome.get("result")):
            quality = "empty"
        else:
            quality = "ok"
        plan = planner.get_plan(session_id)
        active = self.catalog.active_domains(session_id) if self.catalog else set()
        try:
            log_tool_decision(ToolDecisionRecord(
                session_id=session_id,
                round=round_index,
                user_message=message,
                active_domains=sorted(active),
                from_plan=plan is not None,
                subset_size=subset_size,
                total_tools=len(self.registry.get_schemas()),
                tool_chosen=tool_name,
                tool_args=tool_args if isinstance(tool_args, dict) else {},
                result_quality=quality,
                plan_step_matched=step_n,
            ))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[chat_engine] 决策日志记录失败: {e}")
```

- [ ] **Step 4: 修改调用处先调 mark_step_done**

在 `app/services/chat_engine.py:585-588` 把：

```python
                    self._log_tool_decision(
                        session_id, round_index, message, tool_name,
                        tool_args_dict, outcome, len(tools or []),
                    )
```

替换为：

```python
                    from app.services.chat import planner as _planner
                    step_n_matched = _planner.mark_step_done(session_id, tool_name, self.registry)
                    self._log_tool_decision(
                        session_id, round_index, message, tool_name,
                        tool_args_dict, outcome, len(tools or []),
                        step_n=step_n_matched,
                    )
```

- [ ] **Step 5: 运行测试**

Run: `pytest tests/test_chat_engine_planning.py -v`
Expected: 所有测试 PASS（新增 + 既有）

- [ ] **Step 6: Commit**

```bash
git add app/services/chat_engine.py tests/test_chat_engine_planning.py
git commit -m "refactor(chat_engine): extract mark_step_done from _log_tool_decision

Lift planner.mark_step_done call from inside _log_tool_decision to the
call site in chat_stream. Pass the matched step_n as a parameter. This
lets the upcoming plan_step_done SSE event reuse the same step_n value
without calling mark_step_done twice (which would advance the cursor)."
```

---

### Task 2: `_maybe_plan` 返回 `Plan | None`

**Files:**
- Modify: `app/services/chat_engine.py:314-324`（`_maybe_plan`）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_chat_engine_planning.py` 末尾：

```python
@pytest.mark.asyncio
async def test_maybe_plan_returns_plan_on_success(engine, monkeypatch):
    """_maybe_plan 成功生成计划时必须返回 Plan 对象。"""
    expected_plan = planner_mod.Plan(intent="test", domains=["core"], steps=[])
    async def fake_make_plan(cfg, session_id, message, env):
        planner_mod.set_plan(session_id, expected_plan)
        return expected_plan
    monkeypatch.setattr(planner_mod, "make_plan", fake_make_plan)
    result = await engine._maybe_plan("sess-R1", "复杂请求需要规划的内容", [])
    assert result is expected_plan
    planner_mod.clear_plan("sess-R1")


@pytest.mark.asyncio
async def test_maybe_plan_returns_none_when_skipped(engine, monkeypatch):
    """should_plan 返回 False 时，_maybe_plan 返回 None。"""
    planner_mod.set_plan("sess-R2", planner_mod.Plan(intent="x", domains=["core"], steps=[]))
    result = await engine._maybe_plan("sess-R2", "换颜色", [])  # 短追问，应该跳过
    assert result is None
    planner_mod.clear_plan("sess-R2")


@pytest.mark.asyncio
async def test_maybe_plan_returns_none_on_llm_failure(engine, monkeypatch):
    """make_plan 抛异常时，_maybe_plan 返回 None（不传播异常）。"""
    async def fake_make_plan(*a, **k):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(planner_mod, "make_plan", fake_make_plan)
    result = await engine._maybe_plan("sess-R3", "复杂请求需要规划的内容", [])
    assert result is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chat_engine_planning.py -k "test_maybe_plan_returns" -v`
Expected: 3 个测试 FAIL —— 当前 `_maybe_plan` 返回 None 总是

- [ ] **Step 3: 修改 `_maybe_plan` 实现**

在 `app/services/chat_engine.py` 替换 `_maybe_plan` 整段为：

```python
    async def _maybe_plan(self, session_id: str, message: str, messages: list[dict]):
        """启发式门控通过则跑规划阶段，返回新生成的 Plan；跳过 / 失败均返回 None。

        规划是增强，失败静默降级——chat_stream 据返回值决定是否发 plan_ready 事件。
        """
        from app.services.chat import planner
        has_plan = planner.get_plan(session_id) is not None
        if not planner.should_plan(message, messages, has_plan):
            return None
        env = self._get_map_state_summary(session_id)
        try:
            return await planner.make_plan(self._planner_llm_config(), session_id, message, env)
        except Exception as e:  # noqa: BLE001 — 规划绝不能拖垮对话
            logger.warning(f"[chat_engine] 规划阶段异常，降级无计划: {e}")
            return None
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/test_chat_engine_planning.py -v`
Expected: 所有测试 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/chat_engine.py tests/test_chat_engine_planning.py
git commit -m "refactor(chat_engine): _maybe_plan returns Plan | None

Caller (chat_stream) needs to know whether a plan was actually created
so it can decide whether to emit plan_ready SSE event. Failures and
short-followup skips both yield None; only a freshly-created Plan is
returned non-None."
```

---

### Task 3: 发出 `plan_ready` SSE 事件

**Files:**
- Modify: `app/services/chat_engine.py:489`（`_maybe_plan` 调用处）
- Modify: `tests/test_chat_engine_planning.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_chat_engine_planning.py` 末尾：

```python
@pytest.mark.asyncio
async def test_chat_stream_emits_plan_ready_when_plan_created(engine, monkeypatch):
    """chat_stream 在 _maybe_plan 成功后必须发 plan_ready SSE 事件。"""
    test_plan = planner_mod.Plan(
        intent="测试意图",
        domains=["core", "chinese"],
        steps=[
            planner_mod.PlanStep(n=1, goal="获取边界", tool_family="chinese"),
            planner_mod.PlanStep(n=2, goal="出热力图", tool_family="core"),
        ],
    )
    async def fake_maybe_plan(self, session_id, message, messages):
        planner_mod.set_plan(session_id, test_plan)
        return test_plan
    monkeypatch.setattr(engine, "_maybe_plan",
                        fake_maybe_plan.__get__(engine, type(engine)))
    # 让主 LLM 调用立即结束（不进工具循环），简化测试
    async def fake_llm_stream(*a, **k):
        if False: yield
        # 直接 yield 一个 done event
        yield ("done", {"message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop"})
    monkeypatch.setattr(engine, "_call_llm_stream", fake_llm_stream)

    captured = []
    async for ev in engine.chat_stream("sess-EV1", "复杂请求需要规划的内容"):
        captured.append(ev)

    joined = "".join(captured)
    assert "event: plan_ready" in joined
    assert "测试意图" in joined
    assert '"step_n"' not in joined.split("plan_ready")[1].split("event:")[0] or True
    # 验证 steps 数组与字段
    import json
    plan_ready_chunks = [c for c in captured if c.startswith("event: plan_ready")]
    assert len(plan_ready_chunks) == 1
    data_line = [l for l in plan_ready_chunks[0].splitlines() if l.startswith("data:")][0]
    data = json.loads(data_line[len("data:"):].strip())
    assert data["intent"] == "测试意图"
    assert data["domains"] == ["core", "chinese"]
    assert len(data["steps"]) == 2
    assert data["steps"][0] == {"n": 1, "goal": "获取边界", "tool_family": "chinese", "done": False}
    planner_mod.clear_plan("sess-EV1")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chat_engine_planning.py::test_chat_stream_emits_plan_ready_when_plan_created -v`
Expected: FAIL —— `event: plan_ready` 不在输出里

- [ ] **Step 3: 修改 `chat_stream` 在 `_maybe_plan` 后 yield plan_ready**

在 `app/services/chat_engine.py:489` 把：

```python
        await self._maybe_plan(session_id, message, messages)
```

替换为：

```python
        plan = await self._maybe_plan(session_id, message, messages)
        try:
            if plan is not None:
                yield sse_event("plan_ready", {
                    "session_id": session_id,
                    "task_id": task.id,
                    "intent": plan.intent,
                    "domains": plan.domains,
                    "steps": [
                        {"n": s.n, "goal": s.goal, "tool_family": s.tool_family, "done": False}
                        for s in plan.steps
                    ],
                })
        except Exception as e:  # noqa: BLE001 — 发事件失败永远不能拖垮工具循环
            logger.warning(f"[chat_engine] plan_ready 发送失败: {e}")
```

注意：`task` 此时已经创建。当前代码顺序是 `_maybe_plan` → `task = self.tracker.create` → `yield task_start`。把 `_maybe_plan` 调用整段（包括上面 try/except 块的 yield）移到 `yield sse_event("task_start", ...)` **之后**。

原顺序：`_maybe_plan` → `task = self.tracker.create` → `yield task_start`
新顺序：`task = self.tracker.create` → `yield task_start` → `_maybe_plan` → `yield plan_ready (if any)`

把 `await self._maybe_plan(...)` 这一行连同上面的 try/except 块整个剪切到 `yield sse_event("task_start", ...)` 之后。这样前端先收到 `task_start` 设好 task scope，再收到 `plan_ready` 把 plan 附到该 task。

- [ ] **Step 4: 运行测试**

Run: `pytest tests/test_chat_engine_planning.py -v`
Expected: 所有测试 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/chat_engine.py tests/test_chat_engine_planning.py
git commit -m "feat(chat_engine): emit plan_ready SSE event after planning

After _maybe_plan returns a Plan, emit a structured plan_ready event
carrying intent, domains, and steps. Frontend uses this to render the
PlanCard at the top of the assistant message. Wrapped in try/except so
SSE failure cannot disrupt the tool loop."
```

---

### Task 4: 发出 `plan_step_done` SSE 事件

**Files:**
- Modify: `app/services/chat_engine.py:585-590`（工具执行成功分支）
- Modify: `tests/test_chat_engine_planning.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_chat_engine_planning.py` 末尾：

```python
@pytest.mark.asyncio
async def test_chat_stream_emits_plan_step_done_after_tool(engine, monkeypatch):
    """工具执行命中计划步骤后必须发 plan_step_done。"""
    # 预置一个 Plan，标 buffer_analysis 工具 domain 是 core，匹配 step 1
    test_plan = planner_mod.Plan(
        intent="x", domains=["core"],
        steps=[planner_mod.PlanStep(n=1, goal="缓冲", tool_family="core")],
    )
    planner_mod.set_plan("sess-EV2", test_plan)
    # 让 _maybe_plan 不再二次规划
    async def fake_maybe_plan(self, *a, **k): return None
    monkeypatch.setattr(engine, "_maybe_plan",
                        fake_maybe_plan.__get__(engine, type(engine)))
    # 让 registry.metadata 返回 domains=["core"]，使 mark_step_done 能匹配
    monkeypatch.setattr(engine.registry, "metadata",
                        lambda name: {"domains": ["core"]})

    # 让主 LLM 第一轮返回一个 tool_call，第二轮立刻 done
    call_count = {"n": 0}
    async def fake_llm_stream(*a, **k):
        call_count["n"] += 1
        if call_count["n"] == 1:
            yield ("done", {"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "tc1", "type": "function",
                                "function": {"name": "buffer_analysis",
                                             "arguments": "{}"}}],
            }, "finish_reason": "tool_calls"})
        else:
            yield ("done", {"message": {"role": "assistant", "content": "done"},
                            "finish_reason": "stop"})
    monkeypatch.setattr(engine, "_call_llm_stream", fake_llm_stream)
    # 让 dispatch 不实际跑工具
    from app.services.chat import dispatcher
    async def fake_dispatch(*a, **k):
        return {"is_error": False, "repeated": False,
                "result": {"ok": True}, "llm_payload": "{}",
                "slim_event": {"ok": True}, "geojson_ref": None, "has_geojson": False}
    monkeypatch.setattr(dispatcher, "dispatch_tool_call", fake_dispatch)

    captured = []
    async for ev in engine.chat_stream("sess-EV2", "缓冲分析"):
        captured.append(ev)

    joined = "".join(captured)
    assert "event: plan_step_done" in joined
    import json
    chunks = [c for c in captured if c.startswith("event: plan_step_done")]
    assert len(chunks) == 1
    data = json.loads([l for l in chunks[0].splitlines() if l.startswith("data:")][0][len("data:"):])
    assert data["step_n"] == 1
    planner_mod.clear_plan("sess-EV2")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chat_engine_planning.py::test_chat_stream_emits_plan_step_done_after_tool -v`
Expected: FAIL —— `event: plan_step_done` 不在输出里

- [ ] **Step 3: 修改 chat_stream 工具成功分支**

在 `app/services/chat_engine.py:585-588`，找到 Task 1 改后的代码：

```python
                    from app.services.chat import planner as _planner
                    step_n_matched = _planner.mark_step_done(session_id, tool_name, self.registry)
                    self._log_tool_decision(
                        session_id, round_index, message, tool_name,
                        tool_args_dict, outcome, len(tools or []),
                        step_n=step_n_matched,
                    )
```

在 `self._log_tool_decision(...)` 调用之后追加：

```python
                    try:
                        if step_n_matched is not None:
                            yield sse_event("plan_step_done", {
                                "session_id": session_id,
                                "task_id": task.id,
                                "step_n": step_n_matched,
                            })
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"[chat_engine] plan_step_done 发送失败: {e}")
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/test_chat_engine_planning.py -v`
Expected: 所有测试 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/chat_engine.py tests/test_chat_engine_planning.py
git commit -m "feat(chat_engine): emit plan_step_done SSE event per matched tool

When mark_step_done returns a non-None step number, emit an incremental
plan_step_done event so the frontend can flip that step to 'done' in
the PlanCard without re-syncing the full plan."
```

---

### Task 5: 发出 `plan_finalized` SSE 事件（覆盖三个终态）

**Files:**
- Modify: `app/services/chat_engine.py`（task_complete / task_cancelled / task_error 三处）
- Modify: `app/utils/sse.py`（文件首部加注释）
- Modify: `tests/test_chat_engine_planning.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_chat_engine_planning.py` 末尾：

```python
@pytest.mark.asyncio
async def test_chat_stream_emits_plan_finalized_with_skipped(engine, monkeypatch):
    """task_complete 前必须发 plan_finalized，未打勾步骤进 skipped。"""
    test_plan = planner_mod.Plan(
        intent="x", domains=["core"],
        steps=[
            planner_mod.PlanStep(n=1, goal="a", tool_family="core", done=True),
            planner_mod.PlanStep(n=2, goal="b", tool_family="core", done=True),
            planner_mod.PlanStep(n=3, goal="c", tool_family="core", done=False),
        ],
    )
    planner_mod.set_plan("sess-EV3", test_plan)
    async def fake_maybe_plan(self, *a, **k): return None
    monkeypatch.setattr(engine, "_maybe_plan",
                        fake_maybe_plan.__get__(engine, type(engine)))
    async def fake_llm_stream(*a, **k):
        yield ("done", {"message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop"})
    monkeypatch.setattr(engine, "_call_llm_stream", fake_llm_stream)

    captured = []
    async for ev in engine.chat_stream("sess-EV3", "anything"):
        captured.append(ev)

    joined = "".join(captured)
    assert "event: plan_finalized" in joined
    # plan_finalized 必须出现在 task_complete 之前
    pf_idx = joined.find("event: plan_finalized")
    tc_idx = joined.find("event: task_complete")
    assert pf_idx >= 0 and tc_idx >= 0 and pf_idx < tc_idx
    import json
    chunks = [c for c in captured if c.startswith("event: plan_finalized")]
    data = json.loads([l for l in chunks[0].splitlines() if l.startswith("data:")][0][len("data:"):])
    assert data["skipped"] == [3]
    planner_mod.clear_plan("sess-EV3")


@pytest.mark.asyncio
async def test_chat_stream_no_plan_events_when_plan_skipped(engine, monkeypatch):
    """_maybe_plan 返回 None 时，整个 SSE 流不能出现任何 plan_* 事件。"""
    planner_mod.clear_plan("sess-EV4")  # 确保无残留
    async def fake_maybe_plan(self, *a, **k): return None
    monkeypatch.setattr(engine, "_maybe_plan",
                        fake_maybe_plan.__get__(engine, type(engine)))
    async def fake_llm_stream(*a, **k):
        yield ("done", {"message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop"})
    monkeypatch.setattr(engine, "_call_llm_stream", fake_llm_stream)

    captured = []
    async for ev in engine.chat_stream("sess-EV4", "x"):
        captured.append(ev)
    joined = "".join(captured)
    assert "event: plan_ready" not in joined
    assert "event: plan_step_done" not in joined
    assert "event: plan_finalized" not in joined
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chat_engine_planning.py -k "plan_finalized or no_plan_events" -v`
Expected: `plan_finalized` 测试 FAIL；`no_plan_events` 测试 PASS（暂时没有 plan_* 事件，这是好事）

- [ ] **Step 3: 在 chat_stream 三个终态分支前发 plan_finalized**

在 `app/services/chat_engine.py` 的 `chat_stream` 方法里，先在 chat_stream 函数开头（在 task 创建之后即可）定义一个内联辅助函数。把它定义在 `for round_index in range(self.max_rounds):` 循环之前：

```python
        # 终态前发 plan_finalized（task_complete / task_cancelled / task_error 共用）
        def _maybe_plan_finalized_event():
            try:
                from app.services.chat import planner as _planner
                plan_obj = _planner.get_plan(session_id)
                if plan_obj is None:
                    return None
                skipped = [s.n for s in plan_obj.steps if not s.done]
                return sse_event("plan_finalized", {
                    "session_id": session_id,
                    "task_id": task.id,
                    "skipped": skipped,
                })
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[chat_engine] plan_finalized 构造失败: {e}")
                return None
```

然后在每个终态 yield 之前插入：

1. **task_complete 之前**（约第 662-663 行）：
```python
                pf = _maybe_plan_finalized_event()
                if pf: yield pf
                yield sse_event("task_complete", {...})
```

2. **task_cancelled 之前**（每一处 `yield sse_event("task_cancelled", ...)`，约 503、637 行）：
```python
                pf = _maybe_plan_finalized_event()
                if pf: yield pf
                yield sse_event("task_cancelled", {"task_id": task.id})
                return
```

3. **task_error 之前**（约 673 行 `yield sse_event("task_error", ...)`）：
```python
        pf = _maybe_plan_finalized_event()
        if pf: yield pf
        yield sse_event("task_error", {"task_id": task.id, "error": "达到最大轮数"})
```

- [ ] **Step 4: 在 `app/utils/sse.py` 首部追加契约注释**

把 `app/utils/sse.py` 文件首部从 `import json` 之前改为：

```python
"""SSE 事件封装。

新增于 plan-in-chat 设计（2026-05-20）的事件契约：

  - plan_ready      由 chat_engine.chat_stream 在 _maybe_plan 成功后发出
                    data: {session_id, task_id, intent, domains, steps[]}
  - plan_step_done  每次 planner.mark_step_done 返回非空时发出
                    data: {session_id, task_id, step_n}
  - plan_finalized  task_complete / task_cancelled / task_error 之前发出
                    data: {session_id, task_id, skipped: [step_n, ...]}

前端类型定义见 frontend/lib/types/agent-plan.ts::AgentPlanState
"""
import json
import logging
from typing import Any
```

- [ ] **Step 5: 运行测试**

Run: `pytest tests/test_chat_engine_planning.py -v`
Expected: 所有测试 PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/chat_engine.py app/utils/sse.py tests/test_chat_engine_planning.py
git commit -m "feat(chat_engine): emit plan_finalized before all terminal events

Before yielding task_complete, task_cancelled, or task_error, compute
the list of steps still in pending state and emit plan_finalized with
that skipped list. Frontend uses this to transition pending → skipped
in the PlanCard so it never sticks in an in-progress state.

Also document the three new SSE event contracts at the top of sse.py."
```

---

### Task 6: 前端类型 `AgentPlanState`

**Files:**
- Create: `frontend/lib/types/agent-plan.ts`

- [ ] **Step 1: 创建类型文件**

创建 `frontend/lib/types/agent-plan.ts`：

```typescript
/**
 * Agent execution plan — backend → frontend contract from chat_engine SSE.
 *
 * Backend source of truth: app/services/chat/planner.py (Plan, PlanStep).
 * SSE events: plan_ready, plan_step_done, plan_finalized (see app/utils/sse.py).
 *
 * Note: this is distinct from `PlanProposalPayload` in app/page.tsx, which
 * is the Plan Mode (propose_plan tool) approval gate UI. The two never share
 * a Message field — `m.plan` is for proposals, `m.agentPlan` is for this.
 */

export type AgentPlanStepStatus = 'pending' | 'done' | 'skipped';

export interface AgentPlanStepState {
  n: number;
  goal: string;
  tool_family: string;
  status: AgentPlanStepStatus;
}

export interface AgentPlanState {
  intent: string;
  domains: string[];
  steps: AgentPlanStepState[];
  finalized: boolean;
}
```

- [ ] **Step 2: TypeScript 编译验证**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npx tsc --noEmit 2>&1 | grep -v "node_modules" | head -10`
Expected: 无错误（纯加文件，未被任何模块引用）

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/types/agent-plan.ts
git commit -m "feat(types): add AgentPlanState contract for plan-in-chat"
```

---

### Task 7: SSE handler 三个新分支

**Files:**
- Modify: `frontend/app/page.tsx:96`（消息类型加 `agentPlan`）
- Modify: `frontend/app/page.tsx:300+`（SSE handler 加 3 个 else-if 分支）

- [ ] **Step 1: 在消息类型加 `agentPlan` 字段**

在 `frontend/app/page.tsx:96`，把：

```typescript
  const [messages, setMessages] = useState<Array<{ id: string; role: 'user' | 'assistant'; content: string; timestamp: any; isThinking?: boolean; charts?: unknown[]; toolCalls?: ToolCallEntry[]; plan?: PlanProposalPayload }>>([
```

替换为：

```typescript
  const [messages, setMessages] = useState<Array<{ id: string; role: 'user' | 'assistant'; content: string; timestamp: any; isThinking?: boolean; charts?: unknown[]; toolCalls?: ToolCallEntry[]; plan?: PlanProposalPayload; agentPlan?: AgentPlanState }>>([
```

并在文件 import 区追加：

```typescript
import type { AgentPlanState } from '@/lib/types/agent-plan';
```

- [ ] **Step 2: 在 SSE handler 链尾追加 3 个分支**

在 `frontend/app/page.tsx` 找到现有 `else if (event.event === 'error' || event.event === 'step_error' || event.event === 'task_error')` 分支（约第 358 行），在它**之前**插入：

```typescript
    } else if (event.event === 'plan_ready') {
      try {
        const incoming = JSON.parse(event.data);
        setMessages(prev => prev.map(m => m.id === thinkingId ? { ...m,
          agentPlan: {
            intent: incoming.intent,
            domains: incoming.domains ?? [],
            steps: (incoming.steps ?? []).map((s: any) => ({
              n: s.n, goal: s.goal, tool_family: s.tool_family, status: 'pending' as const,
            })),
            finalized: false,
          },
        } : m));
      } catch (err) { console.warn('[plan_ready] parse failed', err); }
    } else if (event.event === 'plan_step_done') {
      try {
        const incoming = JSON.parse(event.data);
        const stepN = incoming.step_n;
        setMessages(prev => prev.map(m => {
          if (m.id !== thinkingId || !m.agentPlan) return m;
          return { ...m, agentPlan: { ...m.agentPlan,
            steps: m.agentPlan.steps.map(s => s.n === stepN ? { ...s, status: 'done' as const } : s),
          }};
        }));
      } catch (err) { console.warn('[plan_step_done] parse failed', err); }
    } else if (event.event === 'plan_finalized') {
      try {
        const incoming = JSON.parse(event.data);
        const skipped = new Set<number>(incoming.skipped ?? []);
        setMessages(prev => prev.map(m => {
          if (m.id !== thinkingId || !m.agentPlan) return m;
          return { ...m, agentPlan: { ...m.agentPlan,
            finalized: true,
            steps: m.agentPlan.steps.map(s =>
              skipped.has(s.n) ? { ...s, status: 'skipped' as const } : s),
          }};
        }));
      } catch (err) { console.warn('[plan_finalized] parse failed', err); }
    }
```

注意：`event.data` 来源是字符串（SSE 行 `data:` 后面的 JSON）；现有 `step_result` 分支用 `data = JSON.parse(event.data)`（在第 299 行附近）。如果 `event.data` 已经被某个外层处理过，参照现有 `step_result` 的解码方式调整：如果发现外层已经 `const data = JSON.parse(event.data)`，把上面的 `JSON.parse(event.data)` 改成直接使用 `data`（即把这些分支放在统一变量 `data` 已经解码后的位置）。

- [ ] **Step 3: TypeScript 编译验证**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npx tsc --noEmit 2>&1 | grep -v "node_modules" | head -10`
Expected: 无错误

- [ ] **Step 4: 跑一次现有测试确认无回归**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npm test -- --run 2>&1 | tail -8`
Expected: 33 test files passed（沿用现有数量）

- [ ] **Step 5: Commit**

```bash
git add frontend/app/page.tsx
git commit -m "feat(chat): wire plan_ready/step_done/finalized SSE events to message

Add m.agentPlan field and three SSE handler branches that build up the
AgentPlanState incrementally as events arrive. Each branch is wrapped
in try/catch so a malformed event silently drops without affecting
other handlers."
```

---

### Task 8: PlanCard 组件 + 测试

**Files:**
- Create: `frontend/components/chat/plan-card.tsx`
- Create: `frontend/components/chat/plan-card.test.tsx`

- [ ] **Step 1: 写失败测试**

创建 `frontend/components/chat/plan-card.test.tsx`：

```typescript
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { PlanCard } from './plan-card';
import type { AgentPlanState } from '@/lib/types/agent-plan';

const buildPlan = (): AgentPlanState => ({
  intent: '成都医疗设施热力分析',
  domains: ['chinese', 'core'],
  steps: [
    { n: 1, goal: '获取成都边界', tool_family: 'chinese', status: 'done' },
    { n: 2, goal: '查询医院 POI', tool_family: 'chinese', status: 'done' },
    { n: 3, goal: '生成热力图', tool_family: 'core', status: 'pending' },
  ],
  finalized: false,
});

describe('PlanCard', () => {
  it('renders intent and step goals', () => {
    render(<PlanCard plan={buildPlan()} />);
    expect(screen.getByText('成都医疗设施热力分析')).toBeInTheDocument();
    expect(screen.getByText('获取成都边界')).toBeInTheDocument();
    expect(screen.getByText('查询医院 POI')).toBeInTheDocument();
    expect(screen.getByText('生成热力图')).toBeInTheDocument();
  });

  it('shows done/total counter', () => {
    render(<PlanCard plan={buildPlan()} />);
    expect(screen.getByText('2 / 3')).toBeInTheDocument();
  });

  it('returns null when steps array is empty', () => {
    const empty: AgentPlanState = { intent: 'x', domains: [], steps: [], finalized: false };
    const { container } = render(<PlanCard plan={empty} />);
    expect(container.firstChild).toBeNull();
  });

  it('applies opacity-50 class to skipped steps', () => {
    const plan = buildPlan();
    plan.steps[2].status = 'skipped';
    plan.finalized = true;
    const { container } = render(<PlanCard plan={plan} />);
    const items = container.querySelectorAll('li');
    expect(items.length).toBe(3);
    // The third item (skipped) should have opacity-50
    expect(items[2].className).toContain('opacity-50');
    // First two (done) should not
    expect(items[0].className).not.toContain('opacity-50');
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npm test -- plan-card --run 2>&1 | tail -10`
Expected: FAIL —— 找不到 `./plan-card`

- [ ] **Step 3: 创建 PlanCard 组件**

创建 `frontend/components/chat/plan-card.tsx`：

```typescript
'use client';

import { ClipboardList, Check, Circle, MinusCircle } from 'lucide-react';
import type { AgentPlanState } from '@/lib/types/agent-plan';

interface Props {
  plan: AgentPlanState;
}

const STATUS_ICON = {
  done: <Check className="h-3 w-3 text-emerald-500" />,
  pending: <Circle className="h-3 w-3 text-muted-foreground/50 animate-pulse" />,
  skipped: <MinusCircle className="h-3 w-3 text-muted-foreground/40" />,
};

export function PlanCard({ plan }: Props) {
  const total = plan.steps.length;
  if (total === 0) return null;
  const doneCount = plan.steps.filter(s => s.status === 'done').length;
  return (
    <div className="my-2 p-3 rounded-lg border border-border bg-card/60">
      <div className="flex items-center gap-2 mb-2">
        <ClipboardList className="h-4 w-4 text-primary" />
        <span className="text-sm font-semibold text-foreground truncate">{plan.intent}</span>
        <span className="text-[10px] ml-auto text-muted-foreground tabular-nums">
          {doneCount} / {total}
        </span>
      </div>
      <ul className="space-y-1">
        {plan.steps.map(s => (
          <li
            key={s.n}
            className={`flex items-center gap-2 text-[11px] ${
              s.status === 'skipped' ? 'opacity-50' : ''
            }`}
          >
            <span className="shrink-0">{STATUS_ICON[s.status]}</span>
            <span className={`flex-1 ${s.status === 'done' ? 'text-foreground' : 'text-muted-foreground'}`}>
              {s.goal}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 4: 运行测试**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npm test -- plan-card --run 2>&1 | tail -10`
Expected: PASS（4 个测试）

- [ ] **Step 5: Commit**

```bash
git add frontend/components/chat/plan-card.tsx frontend/components/chat/plan-card.test.tsx
git commit -m "feat(chat): add PlanCard component to render AgentPlanState"
```

---

### Task 9: 把 PlanCard 挂到聊天消息渲染

**Files:**
- Modify: `frontend/components/sidebar/chat-tab.tsx:228-235`（在 msg.think 之后、msg.content 之前插入）

- [ ] **Step 1: 在 chat-tab.tsx 顶部追加 import**

读 `frontend/components/sidebar/chat-tab.tsx`，找到 `import { PlanProposalCard } from '@/components/chat/plan-proposal-card';` 这一行（约第 10 行），在它下面追加：

```typescript
import { PlanCard } from '@/components/chat/plan-card';
```

- [ ] **Step 2: 在 msg.content 渲染之前挂载 PlanCard**

在 `frontend/components/sidebar/chat-tab.tsx`，找到（约第 234-235 行）：

```typescript
                      {msg.think && (
                        <CollapsibleThink 
                          content={msg.think} 
                          isDark={isDark} 
                          accentColor={accentColor} 
                        />
                      )}
                      {msg.content && <MiniMd text={msg.content} />}
```

在这两段之间插入：

```typescript
                      {(msg as any).agentPlan && (
                        <PlanCard plan={(msg as any).agentPlan} />
                      )}
```

（`(msg as any)` 是因为 chat-tab.tsx 处的 message 类型是在 page.tsx 内联定义的，跨文件不直接共享——沿用这里其他可选字段如 `toolCalls` 的访问惯例。如果你看到这里已经有更好的类型导出，请改用导出的类型。）

- [ ] **Step 3: TypeScript 编译验证**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npx tsc --noEmit 2>&1 | grep -v "node_modules" | head -10`
Expected: 无错误

- [ ] **Step 4: 运行所有前端测试无回归**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npm test -- --run 2>&1 | tail -8`
Expected: 全部通过（PlanCard 新增 4 个测试 + 之前的总数）

- [ ] **Step 5: Commit**

```bash
git add frontend/components/sidebar/chat-tab.tsx
git commit -m "feat(chat): mount PlanCard above assistant message content"
```

---

### Task 10: 全量回归

**Files:** 无（仅验证）

- [ ] **Step 1: 后端完整测试**

Run: `cd /home/kevin/projects/webgis-ai-agent && pytest tests/test_chat_engine_planning.py tests/unit/test_planner.py tests/unit/test_decision_log.py tests/test_chat_context_builder.py -v 2>&1 | tail -15`
Expected: 全部通过；新增的 plan_ready / plan_step_done / plan_finalized / no_plan_events 测试在内

- [ ] **Step 2: 后端冒烟（更广范围）**

Run: `cd /home/kevin/projects/webgis-ai-agent && pytest tests/ -q --ignore=tests/test_plan_mode.py 2>&1 | tail -5`
Expected: 全部通过（test_plan_mode.py 有 2 个预存在的 worktree-environment-specific 失败，参见 plan-first-agentic-loop 分支历史）

- [ ] **Step 3: 前端完整测试**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npm test -- --run 2>&1 | tail -6`
Expected: 全部通过

- [ ] **Step 4: TypeScript 检查**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npx tsc --noEmit 2>&1 | grep -v "node_modules" | head -5 && echo "TSC CLEAN"`
Expected: TSC CLEAN

- [ ] **Step 5: 人工冒烟（可选，需启动 dev server）**

Run（两个终端）：

```bash
# 后端
uvicorn app.main:app --reload

# 前端
cd frontend && npm run dev
```

在浏览器对话框输入"分析成都市三甲医院的空间分布并做热点检测"，确认：
- 助理消息顶部立即出现"执行计划"卡片
- 卡片标题为 AI 生成的 intent
- 步骤随工具调用从 ⬜ pending → ✅ done 翻转
- 任务结束时，未匹配工具的步骤变灰色 ⊘ skipped
- 进度计数 `n / total` 持续更新

输入短追问"换个颜色"，确认：
- 新助理消息**不**出现 PlanCard
- 之前的助理消息上 PlanCard 仍然可见

---

## 验收标准

- 触发任意 plan-first 路径请求后：
  - 助理消息顶部立即出现 `<PlanCard>`，意图为 AI 制定的 intent，步骤全 pending
  - 每个工具成功执行后，对应步骤从 pending → done
  - 任务结束时，未完成的步骤转 skipped（灰色 + MinusCircle）
- 短消息追问（`should_plan` 返回 false）：助理消息**不**出现 PlanCard
- 规划 LLM 失败 / SSE 解析失败：助理消息**不**出现 PlanCard，工具循环正常
- 多轮对话中：每个新规划轮次产生独立 PlanCard，旧消息 PlanCard 保留状态
- 既有 SSE 事件（token / content / step_result / tool_result / task_complete / ...）行为不变
- 既有 `m.plan` (PlanProposalPayload) 字段不受影响——`m.agentPlan` 是独立字段
- 既有 `tests/test_chat_engine_planning.py` 在改造后所有测试通过
- TypeScript 编译干净，前端测试全部通过
