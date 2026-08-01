# Deepen ChatEngine by extracting ToolExecutionPipeline

**Status:** accepted

We will extract tool execution, task tracking, parameter parsing, and duplicate loop protection out of `ChatEngine` into a dedicated deep module `ToolExecutionPipeline` (`app/services/chat/tool_pipeline.py`).

## Context

`ChatEngine` (`app/services/chat_engine.py`) grew into a ~1000 LOC orchestrator that managed LLM API calls, history loading, prompt caching, tool parameter JSON parsing, `TaskTracker` step lifecycle (`start_step`/`complete_step`), duplicate tool call sentinel checks, and perception state synchronization inside nested loops in `chat()` and `chat_stream()`.

This coupling caused several maintenance and testing frictions:
1. Tool execution boilerplate was duplicated between non-streaming (`chat`) and SSE streaming (`chat_stream`) paths.
2. `TaskTracker` step registration and exception handling were intermingled with LLM prompt formatting.
3. Unit testing tool execution loops required mocking full `ChatEngine` LLM calls or complex setup.

## Decision

1. **Create `ToolExecutionPipeline`**: Move tool dispatch, parameter JSON parsing, `TaskTracker` step lifecycle management (`start_step`/`complete_step`/`fail_step`), duplicate loop protection (`executed_tools`), and result slimming into `ToolExecutionPipeline` (`app/services/chat/tool_pipeline.py`).
2. **Structured Result Contract**: Define a `ToolExecutionResult` dataclass (`tool_name`, `tool_call_id`, `raw_result`, `llm_payload`, `is_error`, `execution_time_ms`) returned by `execute_tool_call`.
3. **Encapsulated Step Lifecycle**: `execute_tool_call` manages step creation and completion within a `try...finally` block, ensuring steps reach a terminal state even if tool dispatch or payload formatting encounters an error.
4. **Thin ChatEngine Loop**: `ChatEngine.chat()` and `chat_stream()` delegate individual tool call execution directly to `ToolExecutionPipeline.execute_tool_call()`.

## Consequences

- **Locality**: Tool execution and task tracking lifecycle are concentrated in `app/services/chat/tool_pipeline.py`.
- **Testability**: `ToolExecutionPipeline` can be unit-tested directly without LLM API mocks.
- **Maintainability**: `ChatEngine` drops ~200 LOC of tool execution and tracking boilerplate.
