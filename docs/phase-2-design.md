# Phase 2: Agent Backend Capability Enhancement - Design Spec
> Version: 1.0 | Date: 2026-04-05 | Status: Approved for Implementation

## 1. Overview
Add 4 new backend capabilities to the WebGIS AI Agent:
1. MCP Protocol Support - Connect to MCP servers for external tools
2. Skill System - Register and execute skills with metadata
3. Multi-tool Orchestration - Chain multiple tool calls with dependency resolution
4. Tool Call Visualization - Real-time SSE for tool invocation status

## 2. Architecture
```
app/
├── tools/
│   ├── mcp_client.py      # NEW: MCP protocol client
│   ├── registry.py        # Existing: FC tool registry
│   └── ...existing tools...
├── skills/               # NEW: Skill system
│   ├── __init__.py       # SkillRegistry + loader
│   ├── base.py           # Base skill class
│   └── *.py              # Individual skill files
├── services/
│   ├── tool_chain.py     # NEW: Multi-tool orchestration
│   ├── chat_engine.py   # Existing: Extended for SSE
│   └── ...existing services...
└── api/routes/
    ├── chat.py           # Extended: SSE endpoint
    └── ...existing routes...
```

**Data Flow:**
User request → ChatEngine → ToolChain (orchestrates local tools + MCP tools + skills) → SSE progress → Response

## 3. MCP Protocol Support

### 3.1 MCPClient Class
**File:** `app/tools/mcp_client.py`

```python
class MCPClient:
    """MCP Protocol Client supporting stdio and HTTP transport"""
    def __init__(self, name: str)
    async def connect_stdio(self, command: list[str], env: dict = None)
    async def connect_http(self, url: str, headers: dict = None)
    async def disconnect(self)
    async def discover_tools(self) -> list[dict]
    async def call_tool(self, name: str, arguments: dict) -> Any
    def register_to_registry(self, registry: ToolRegistry)
```

### 3.2 Transport Support
- **stdio**: Execute local MCP server processes via subprocess
- **HTTP**: Connect to remote MCP servers via HTTP(S)
- **Config**: Passed as dict with transport type and parameters

### 3.3 JSON-RPC 2.0
- Request: `{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {...}}`
- Response: `{"jsonrpc": "2.0", "id": 1, "result": {...}}`
- Error: `{"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "..."}}`

### 3.4 Integration
- MCP tools discovered and registered to existing `ToolRegistry`
- Tools exposed to LLM via schema in chat engine

## 4. Skill System

### 4.1 Skill Base Class
**File:** `app/skills/base.py`

```python
class Skill:
    """Base skill class with metadata"""
    name: str
    description: str
    parameters: dict  # JSON Schema for input
    return_type: str
    category: str = "general"

    async def execute(self, **params) -> Any
    async def validate(self, params: dict) -> bool
```

### 4.2 Skill Registry
**File:** `app/skills/__init__.py`

```python
class SkillRegistry:
    """Skill registration and execution engine"""
    def __init__(self)
    def register(self, skill: Skill)
    def load_from_directory(self, path: str)  # Auto-discover .py files
    def list_skills(self) -> list[dict]  # Metadata for LLM
    async def execute(self, skill_name: str, params: dict) -> Any

def skill_decorator(name: str, description: str, parameters: dict, return_type: str):
    """Decorator for registering skills"""
```

### 4.3 Skill File Structure
- Skills in `app/skills/*.py`
- Auto-loaded on app startup
- Exposed to LLM via chat engine

## 5. Multi-tool Orchestration

### 5.1 ToolChain Class
**File:** `app/services/tool_chain.py`

```python
class ToolChain:
    """Multi-tool orchestration engine"""
    def __init__(self, registry: ToolRegistry)

    async def execute_serial(self, tool_calls: list[ToolCall]) -> list[dict]:
        """Sequential execution: output of each tool passes to next"""

    async def execute_parallel(self, tool_call: list[ToolCall]) -> list[dict]:
        """Concurrent execution of independent tools"""

    async def execute_with_deps(self, tool_calls: list[ToolCall]) -> list[dict]:
        """Execute with automatic dependency resolution"""

class ToolCall:
    tool: str  # Tool name
    params: dict  # Input parameters
    depends_on: list[str] = []  # List of tool names this depends on
```

### 5.2 Dependency Resolution
- Parse `depends_on` field to build execution DAG
- Topological sort for correct execution order
- Parallelize independent branches

### 5.3 Result Aggregation
- Collect results by tool name
- Return list of `{tool: str, result: Any, status: str}`

## 6. Tool Call Visualization

### 6.1 SSE Events
**Endpoint:** `/api/v1/chat/stream` (existing, extend)

**Event Types:**
- `tool_start`: `{"tool": "name", "status": "pending"}`
- `tool_running`: `{"tool": "name", "status": "running", "progress": 50}`
- `tool_complete`: `{"tool": "name", "status": "completed", "result": {...}}`
- `tool_error`: `{"tool": "name", "status": "failed", "error": "..."}`
- `content`: Final response

### 6.2 Progress Tracking
- Each tool call tracked by ID
- Progress percentage for long operations
- Stream result chunks by chunk for large outputs

### 6.3 Integration
- Extend existing `ChatEngine.chat_stream()` method
- Emit SSE events during tool execution
- Frontend consumes via EventSource

## 7. API Endpoints

### 7.1 New Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/skills` | List registered skills |
| POST | `/api/v1/skills/{name}/execute` | Execute a skill |
| GET | `/api/v1/mcp/servers` | List MCP server connections status |
| POST | `/api/v1/mcp/connect` | Connect to MCP server |

### 7.2 Extended Endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/chat/stream` | Chat with SSE tool visualization (existing, extend) |

## 8. Error Handling

### 8.1 MCP Errors
- Connection failure: Retry 3 times with exponential backoff
- Tool not found: Return error with available tool list
- Timeout: Configurable per-tool timeout (default 30s)

### 8.2 Skill Errors
- Validation failure: Return 422 with validation error details
- Execution failure: Return 500 with error message, stack trace in debug mode

### 8.3 Tool Chain Errors
- Dependency not met: Error before execution
- Tool failure: Continue other branches, mark failed in results

## 9. Configuration

### 9.1 New Settings
```python
class Settings(BaseSettings):
    # MCP
    MCP_SERVERS: list[dict] = []  # Pre-configured MCP servers

    # Skills
    SKILLS_DIR: str = "app/skills"
    SKILL_AUTO_LOAD: bool = True
```

## 10. Testing Strategy
- Unit: MCP client, skill registry, tool chain each have isolated tests
- Integration: Chat endpoint with tool chain end-to-end
- Mock: Use stdio mock for MCP testing

---

**Approved for implementation.**