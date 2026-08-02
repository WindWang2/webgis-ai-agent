# Research Findings & Design: PiAgentBridge Simulation Seam & 5-Dimensional Metric Definitions

## 1. Executive Summary
Ticket #246 establishes the simulation harness architecture and metric evaluation framework for `PiAgentBridge`. It introduces `PiAgentHarness` (`app/lib/harness/pi_agent_harness.py`), intercepting tool requests, tool execution responses, and SSE event streams. Furthermore, it defines 5 core evaluation metrics measuring tool call accuracy, MapSpec mutation validity, reference cursor resolution rate, step efficiency, and exception recovery.

---

## 2. Codebase Architecture Interception Points

1. **`app/agent_pi_bridge.py`**:
   - `dispatch_tool(request)` receives `PiToolRequest` payloads from the Pi extension callback.
   - `PiAgentHarness.record_tool_call()` intercepts the payload prior to execution.
   - `PiAgentHarness.record_tool_result()` records the returned `PiToolResponse` / `ToolDispatchResult`.

2. **`app/services/chat/pi_event_mapper.py`**:
   - Pure function `map_event_to_sse()` translates raw Pi events into SSE tokens and step messages.
   - `PiAgentHarness.record_sse_event()` captures formatted SSE event payloads for evaluation.

3. **`app/services/chat/execution_engine.py`**:
   - Implements step tracking via `TaskTracker` and decision logging via `log_tool_decision()`.
   - The harness aligns step indexes and task status with `ChatExecutionEngine` telemetry.

---

## 3. Evaluation Metric Mathematical Definitions

### 1. ToolChoiceAccuracy
$$\text{ToolChoiceAccuracy} = \min\left(100\%, \frac{N_{\text{correct\_tool\_invocations}}}{N_{\text{total\_expected\_tools}}} \times 100\%\right)$$
Measures whether the agent selected the expected GIS tools for the user query.

### 2. MapSpecValidity
$$\text{MapSpecValidity} = \frac{N_{\text{valid\_mapspec\_mutations}}}{N_{\text{total\_mapspec\_mutations}}} \times 100\%$$
Measures the percentage of MapSpec mutation tool calls (`webgis_layer_upsert`, `webgis_view_set`, `webgis_layout_set`, etc.) that produced valid schema updates.

### 3. CursorResolutionRate
$$\text{CursorResolutionRate} = \frac{N_{\text{resolved\_ref\_cursors}}}{N_{\text{total\_ref\_cursors}}} \times 100\%$$
Evaluates the proportion of reference cursors (e.g. `ref:geojson:...`) successfully passed and resolved in session context.

### 4. StepEfficiency
$$\text{StepEfficiency} = \min\left(100\%, \frac{S_{\text{ideal}}}{S_{\text{actual}}} \times 100\%\right)$$
Compares the theoretical minimum steps required against the actual number of tool calls taken.

### 5. ErrorRecoveryRate
$$\text{ErrorRecoveryRate} = \frac{N_{\text{successful\_recoveries}}}{N_{\text{total\_tool\_exceptions}}} \times 100\%$$
Measures the agent's self-healing resilience when recovering from tool exceptions.
