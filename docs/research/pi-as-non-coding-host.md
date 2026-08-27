# What vendored Pi can host as a non-coding GIS agent

**Ticket:** [#1020](https://github.com/WindWang2/webgis-ai-agent/issues/1020) (map [#1016](https://github.com/WindWang2/webgis-ai-agent/issues/1016))
**Question:** 仓内 `vendor/pi`（及上游 earendil-works/pi）作为非 coding 的产品 agent 宿主，官方能力边界是什么？
**Method:** Primary sources only — vendored `vendor/pi/packages/coding-agent/docs/` and `src/`, plus upstream docs/CHANGELOG at the recorded pin. Facts, not a fork recommendation.

**Vendored pin:** submodule `vendor/pi` → [earendil-works/pi](https://github.com/earendil-works/pi) commit `dd6bea41efa8caa7a10fe5a6401676dc5699f83f` (2026-07-21, `v0.81.1-1-gdd6bea41`, package `@earendil-works/pi-coding-agent` `0.81.1`). Recorded in `.gitmodules` as `url = https://github.com/earendil-works/pi.git`. `git submodule status` reports `dd6bea41efa8caa7a10fe5a6401676dc5699f83f vendor/pi (v0.0.2-4787-gdd6bea41)`.

---

## Answer

Official Pi is a **coding harness with a small core**. A non-coding GIS host is already inside the published extension/CLI/RPC surface: register many TypeBox (JSON Schema) tools, drop the default builtins with `--no-builtin-tools`, replace or append the system prompt, and rewrite it per user prompt in `before_agent_start`. Those flags are **process-argv**, not RPC commands — RPC mode is the same `main()` session as TUI.

`promptSnippet` / `promptGuidelines` only feed the **default** system prompt’s “Available tools” / “Guidelines” sections, and only for **currently active** tools. `before_agent_start` can replace the whole system-prompt string for that agent run (chained across extensions); it does not fire on inner tool-loop LLM calls.

Lazy tools are official: `setActiveTools` additive activation records `addedToolNames` on the tool result. Native deferred schemas exist for a short list of Anthropic / OpenAI Responses / Kimi models; everything else resends the full active list and may bust the prompt cache. Activating a tool that carries `promptSnippet` or `promptGuidelines` rebuilds the system prompt and can bust the cache **even on native deferred providers**.

Official **plan mode is not a type**. Core explicitly has no plan object. `examples/extensions/plan-mode/` is a coding-oriented example that stores markdown steps in a `custom` session entry. A product-owned plan object is a different thing.

Almost all GIS-host needs (persona, tool surface, lazy catalogs, persist-your-own-plan, RPC embed) are extension + spawn flags. Core changes are required only for a new session-format Plan type, RPC commands that mutate tools/prompt after spawn, or changing agent-loop / provider-cache protocol.

---

## 1. Extensions: multiple JSON-schema tools; `promptSnippet` / `promptGuidelines` / `before_agent_start`

### Multiple tools, one extension

The factory receives `ExtensionAPI` and may call `pi.registerTool()` any number of times, including after startup (`session_start`, commands, other handlers). New tools refresh in the same session without `/reload`. Docs: [extensions.md — ExtensionAPI `pi.registerTool()`](https://github.com/earendil-works/pi/blob/dd6bea41efa8caa7a10fe5a6401676dc5699f83f/packages/coding-agent/docs/extensions.md#piregistertooldefinition) and [Multiple Tools](https://github.com/earendil-works/pi/blob/dd6bea41efa8caa7a10fe5a6401676dc5699f83f/packages/coding-agent/docs/extensions.md#multiple-tools).

```typescript
export default function (pi: ExtensionAPI) {
  pi.registerTool({ name: "db_connect", ... });
  pi.registerTool({ name: "db_query", ... });
  pi.registerTool({ name: "db_close", ... });
}
```

(`vendor/pi/packages/coding-agent/docs/extensions.md` “Multiple Tools”)

Same-name registration **overrides** a built-in (`read`, `bash`, `edit`, `write`, `grep`, `find`, `ls`). `promptSnippet` / `promptGuidelines` are **not** inherited from the built-in; an override that wants them must set them again (`docs/extensions.md` “Overriding Built-in Tools”).

### JSON Schema is TypeBox `parameters`

`ToolDefinition.parameters` is a TypeBox `TSchema` (`vendor/pi/packages/coding-agent/src/core/extensions/types.ts:440-452`). The LLM sees that object as JSON Schema — pi-ai comments “TypeBox already generates JSON Schema” (`vendor/pi/packages/ai/src/api/openai-completions.ts:1173`, `openai-responses-shared.ts:313`).

Call-time validation is `validateToolArguments` (`vendor/pi/packages/ai/src/utils/validation.ts:271-309`): `Value.Convert` + TypeBox `Check`. If the schema has no TypeBox `KIND` symbol, it goes through `coerceWithJsonSchema` — so a raw `{ type: "object", properties: ... }` also validates. Docs tell authors to use `Type.Object({ ... })` and `StringEnum` from `@earendil-works/pi-ai` for Google-compatible enums (`docs/extensions.md` “Custom Tools”). Optional `prepareArguments(args)` runs **before** schema validation (`types.ts:456-457`).

The execute result sent to the model is `content`; `details` is “for logs or UI rendering” (`vendor/pi/packages/agent/src/types.ts:354-359`). Throw from `execute` to set `isError`; returning a value never sets the error flag (`docs/extensions.md` “Signaling errors”).

### `promptSnippet`

Optional one-line for the default system prompt’s **Available tools** list. Custom tools **without** it stay callable but are omitted from that section (`types.ts:447-448`; `system-prompt.ts:79-84`; locked by `test/system-prompt.test.ts:63-86` and `test/agent-session-dynamic-tools.test.ts:140-179`).

Snippets are normalized to a single line (`agent-session.ts:995-1001`). Built-ins set them (e.g. read: `"Read file contents"` at `src/core/tools/read.ts:213`). Rebuild walks only **currently active** tool names (`agent-session.ts:1019-1033`).

Default prompt shape when there is no `customPrompt` (`system-prompt.ts:121-138`):

```text
You are an expert coding assistant operating inside pi, a coding agent harness. ...

Available tools:
- <name>: <snippet>
...

In addition to the tools above, you may have access to other custom tools depending on the project.

Guidelines:
- ...
```

A tool that is active but has no snippet does not appear under Available tools. Empty visible list renders `(none)` (`system-prompt.ts:83-84`; `test/system-prompt.test.ts:6-14`).

### `promptGuidelines`

Optional string array. When the tool is **active**, bullets are appended **flat** to the default Guidelines section with **no tool-name prefix**. Docs: each bullet must name the tool — write “Use my_tool when…”, not “Use this tool when…” (`docs/extensions.md` `pi.registerTool()`; `types.ts:449-450`).

Rebuild concatenates active tools’ guidelines, then `buildSystemPrompt` trims, drops empties, and dedupes (`agent-session.ts:1004-1016, 1029-1032`; `system-prompt.ts:108-113`; `test/system-prompt.test.ts:90-112`). Default builder also always adds “Be concise…” and “Show file paths clearly…” (`system-prompt.ts:116-118`).

**They do not apply on the `customPrompt` path.** If `--system-prompt` / `SYSTEM.md` set `customPrompt`, `buildSystemPrompt` returns that text plus append/context/skills/cwd and never emits Available tools or Guidelines (`system-prompt.ts:46-71`). `systemPromptOptions.toolSnippets` / `.promptGuidelines` still exist for extensions to inspect (`types.ts:696-697`; `docs/extensions.md` `before_agent_start`).

### `before_agent_start` rewrite

Fired after the user prompt is accepted (and after `input` / skill-template expansion), **before** the agent loop (`docs/extensions.md` lifecycle diagram and `before_agent_start`). Not on each inner tool-loop LLM call — that is `context` (messages) or `before_provider_request` (payload).

Return value (`types.ts:1086-1090`):

```typescript
{
  message?: { customType, content, display, details }; // persisted, sent to LLM
  systemPrompt?: string; // replace for this agent run; chained
}
```

Chaining (`extensions/runner.ts:1064-1127`): handlers run in extension load order; each sees the current string; `event.systemPromptOptions` is the **same base** `BuildSystemPromptOptions` (not rebuilt after earlier rewrites). During the handler, `event.systemPrompt` and `ctx.getSystemPrompt()` both reflect the chain so far (`docs/extensions.md` `ctx.getSystemPrompt()`).

Session applies it once per `prompt()` (`agent-session.ts:1222-1251`):

- returned `systemPrompt` → `_systemPromptOverride` and `agent.state.systemPrompt`
- omitted → reset to `_baseSystemPrompt` (clears a previous turn’s override)

The override is kept for inner turns of **this** run (`_installAgentNextTurnRefresh`, `agent-session.ts:518-538`, changelog [#6162](https://github.com/earendil-works/pi/issues/6162)). It is cleared when the run finishes (`_runAgentPrompt` `finally`, `agent-session.ts:1066-1067`).

`ctx.getSystemPrompt()` does **not** include later `context` mutations or `before_provider_request` payload rewrites. `before_provider_request` *can* rewrite provider-level system instructions; those are not Pi’s system-prompt string (`docs/extensions.md` `before_provider_request`).

Injected `message` is a `custom` role message stored in the session and included in LLM context (`agent-session.ts:1229-1241`; session format `CustomMessage` / `custom_message` entries — `docs/session-format.md`). `pi.appendEntry()` custom entries are **not** in LLM context.

---

## 2. `--no-builtin-tools`, `--system-prompt`, `--append-system-prompt` in RPC mode

RPC is not a second configuration plane. `rpc-entry.ts` calls `main(["--mode", "rpc", ...process.argv.slice(2)])`. `main.ts` parses argv once, builds the same `AgentSession`, then `runRpcMode(runtime)` (`main.ts:816-818`).

`docs/rpc.md` “Common options” only lists provider/model/name/session flags. The parser and session builder still honor the tool/prompt flags for every mode (`cli/args.ts:93-119`; `main.ts:439-450, 663-674, 717-727`).

There is **no** RPC command to change tools or the system prompt after spawn. The command union in `src/modes/rpc/rpc-types.ts:20-73` is prompt/steer/follow_up/abort/session/model/thinking/compaction/retry/**bash**/export/fork/clone/get_commands. Host mutation of tools/prompt is argv at start, or an extension calling `setActiveTools` / returning `systemPrompt` from `before_agent_start`.

### `--no-builtin-tools` (`-nbt`)

CLI → `parsed.noBuiltinTools` → `createAgentSession({ noTools: "builtin" })` (`main.ts:442-443`; `cli/args.ts:118-119`).

SDK semantics (`sdk.ts:54-67, 245-251`):

| Flag / option | Registry | Initial active set |
|---|---|---|
| default | builtins + extension/custom | `read`, `bash`, `edit`, `write` + newly registered extension tools |
| `--no-builtin-tools` / `noTools: "builtin"` | **builtins remain registered** | no default builtins; **extension/custom tools enabled** |
| `--no-tools` / `noTools: "all"` | **empty** | empty |
| `--tools a,b` | allowlist only | only listed names (must include each extension tool you want) |
| `--exclude-tools` | denylist after allowlist | remaining |

`AgentSession` constructor always `_buildRuntime({ includeAllExtensionTools: true })` (`agent-session.ts:395-398`). With `noTools: "builtin"`, `initialActiveToolNames` is `[]` and `allowedToolNames` is `undefined`, so builtins stay in `getAllTools()` and can be turned on later with `setActiveTools`. Locked by `test/suite/regressions/3592-no-builtin-tools-keeps-extension-tools.test.ts:73-86`: active `["dynamic_tool"]`; registry still has `read`/`bash`/`edit`/`write`/`grep`/`find`/`ls`.

`--no-tools` puts `allowedToolNames = []`, so `isAllowedTool` is false for every name and the registry is empty (same test, lines 89-95).

Docs: `docs/usage.md` Tool Options; `docs/extensions.md` “Overriding Built-in Tools”; `docs/sdk.md` Tools (`noTools: "builtin"`).

**RPC `bash` is a host command, not the bash tool.** `{"type":"bash","command":"..."}` still exists on the pin (`rpc-types.ts:54-55`; `rpc-mode.ts:558-562` → `session.executeBash`). It does not go through `--no-builtin-tools`. On this pin it also does **not** go through the `user_bash` extension hook (that bypass is fixed upstream in 0.83.0, CHANGELOG “Fixed direct RPC bash commands bypassing extension `user_bash` handlers”). A GIS host that never sends the RPC `bash` command never hits it.

### `--system-prompt`

Parser stores the next argv token (`cli/args.ts:93-94`). Resource loader `resolvePromptInput`: if `existsSync(input)`, read file; else use the string (`resource-loader.ts:50-65`). CLI value overrides discovered `SYSTEM.md` (`resource-loader.ts:474-478`). Discovery: trusted project `.pi/SYSTEM.md`, else `~/.pi/agent/SYSTEM.md` (`resource-loader.ts:966-977`; `docs/usage.md` “System Prompt Files”).

That string becomes `BuildSystemPromptOptions.customPrompt` (`agent-session.ts:1035-1051`). **Replaces** the default coding-assistant prompt. Still appended, in order: `--append-system-prompt` block, `<project_context>` from AGENTS.md/CLAUDE.md (unless `--no-context-files`), skills **only if `read` is in `selectedTools`**, then `Current working directory: …` (`system-prompt.ts:46-71`). CLI help: “Replace default prompt; context files and skills are still appended” (`docs/usage.md:240`) — skills append is actually gated on `read`.

With `--no-builtin-tools` and no `read` in the active set, the skills section is omitted even if skills are loaded. Skill **slash commands** (`/skill:name`) still expand on the RPC `prompt` path (`docs/rpc.md` “Input expansion”). The default skills blurb tells the model to **use the read tool** to load SKILL.md (`skills.ts:342-344`) — that sentence is coding-host specific.

### `--append-system-prompt`

Repeatable. Values collected as `string[]` (`cli/args.ts:95-97`; `test/args.test.ts:108-116`). Joined with `\n\n` into `appendSystemPrompt` (`agent-session.ts:1036-1038`). Applied on **both** custom and default prompt paths (`system-prompt.ts:49-51, 140-142`). File-or-inline via the same `resolvePromptInput`. Fallback file: `.pi/APPEND_SYSTEM.md` / `~/.pi/agent/APPEND_SYSTEM.md` (`resource-loader.ts:980-990`).

### Mode

`ctx.mode === "rpc"`, `ctx.hasUI === true`. Dialogs (`select`/`confirm`/`input`/`editor`) become JSON `extension_ui_request` / `extension_ui_response`. `custom()` returns `undefined`. TUI-only methods are no-ops (`docs/extensions.md` “Mode Behavior”; `docs/rpc.md` “Extension UI Protocol”).

---

## 3. Deferred / lazy tool schemas, `setActiveTools`, prompt cache

### Official lazy pattern

1. `registerTool()` every tool (they appear in `getAllTools()`).
2. Keep a loader (e.g. `search_tools`) active; leave searchable tools inactive.
3. During loader `execute`, `setActiveTools([...current, ...matches])` — **additive only**.
4. Wrapper diffs active names before/after execute and sets `addedToolNames` on the result (`extensions/wrapper.ts:17-35`; `AgentToolResult.addedToolNames` at `agent/src/types.ts:362-363`).
5. Unknown names passed to `setActiveTools` are ignored (`agent-session.ts:918-932`; `docs/extensions.md` “Dynamic Tool Loading”).

Non-additive changes (removals / replacements) still work as a full active-list update; they **do not** use deferred loading (`docs/extensions.md` “Fallback behavior”).

`setActiveTools` always rebuilds `_baseSystemPrompt` from the new active set and assigns `agent.state.systemPrompt = _systemPromptOverride ?? _baseSystemPrompt` (`agent-session.ts:924-938`). Tool-registry refresh after `registerTool` calls the same path (`agent-session.ts:2546`). Inner-turn refresh copies current tools + the override into the next provider call (`agent-session.ts:518-538`; changelog [#6162](https://github.com/earendil-works/pi/issues/6162): extension tool changes apply before the next provider request in the same run without dropping the `before_agent_start` override).

**Override interaction:** if `before_agent_start` replaced the system prompt, later additive activation still updates `_baseSystemPrompt` (snippets/guidelines) but the **sent** prompt stays the override until the next user `prompt()`. Cache-busting from snippet rebuild is therefore about the **base** prompt unless the override is absent.

### Native deferred vs fallback

`splitDeferredTools` (`vendor/pi/packages/ai/src/utils/deferred-tools.ts:8-38`): when enabled, tools named in transcript `addedToolNames` and not yet used as a `toolCall` are split into `deferred`; the rest are `immediate`.

Docs (`docs/extensions.md` “Models with native deferred loading”), added in 0.80.7 ([CHANGELOG](https://github.com/earendil-works/pi/blob/dd6bea41efa8caa7a10fe5a6401676dc5699f83f/packages/coding-agent/CHANGELOG.md) “Cache-friendly dynamic tool loading”, [#6474](https://github.com/earendil-works/pi-mono/pull/6474)):

| Provider family | Models (as documented on this pin) | Wire format |
|---|---|---|
| Anthropic | Sonnet, Opus, Fable **4.5 or newer, without Haiku** | `defer_loading` on definitions; load point `tool_reference` content |
| OpenAI Responses | `gpt-5.4` and newer family | completed client `tool_search_call` / `tool_search_output` at the load point |
| Kimi OpenAI-compatible | `compat.deferredToolsMode: "kimi"` | Kimi deferred schemas (0.80.9 CHANGELOG) |

Custom/proxy opt-in: `compat.supportsToolReferences: true` (`anthropic-messages`) or `compat.supportsToolSearch: true` (`openai-responses` / `openai-codex-responses`). Leave off unless the endpoint actually speaks that protocol (`docs/extensions.md`; `docs/models.md` `deferredToolsMode`).

**Everyone else:** next request sends the complete current active tool list. The model can call the new tools; adding definitions **may invalidate the cached prompt prefix**. Same fallback for non-additive `setActiveTools`.

### Cache vs `promptSnippet` / `promptGuidelines`

Official warning (`docs/extensions.md` “Fallback behavior”, last paragraph):

> For the best cache behavior, keep the loader tool active for the whole session and add tools instead of replacing the active set. Also note that activating a tool with `promptSnippet` or `promptGuidelines` rebuilds the system prompt; that system-prompt change can invalidate the prefix even when the provider supports deferred schemas. Lazily loaded tools should usually rely on their tool `description` and omit active-only prompt metadata.

So: native deferred loading preserves the **tool-schema** prefix; a system-prompt rebuild from snippet/guideline activation is a separate cache key.

There is no RPC `setActiveTools`. A Python host drives lazy catalogs only by having an **in-process extension** call it (typical: a `search_tools` / `list_available_tools` native tool).

---

## 4. Official plan-mode extension ≠ a product-owned plan object

Pi core **does not ship plan mode**. Design docs:

- `docs/usage.md` “Design Principles”: “It intentionally does not include built-in MCP, sub-agents, permission popups, **plan mode**, to-dos, or background bash.”
- `README.md` Philosophy: “**No plan mode.** Write plans to files, or build it with extensions, or install a package.” Same for to-dos.

`examples/extensions/plan-mode/` is an **example**, listed in `docs/extensions.md` Examples Reference as “Full plan mode implementation”. It is coding-agent workflow:

- Disable built-in `edit`/`write`; keep other active tools; bash allowlist of read-only commands (`examples/extensions/plan-mode/index.ts:21-24, 104-114, 163-174`; `README.md` in that directory).
- Ask the model to emit a numbered list under a `Plan:` header (`index.ts:200-227`).
- Parse those lines into in-memory todos (`extractTodoItems`).
- Persist `{ enabled, todos, executing, toolsBeforePlanMode }` via `pi.appendEntry("plan-mode", …)` — a **custom entry**, not LLM context (`index.ts:116-123, 347-357`; `docs/session-format.md` CustomEntry).
- On execute: restore write tools, inject custom messages, track `[DONE:n]` in assistant text (`index.ts:250-258, 307-329`).
- TUI `ctx.ui.select` for Execute / Stay / Refine — in RPC that becomes the extension UI sub-protocol, not a plan API.

Session format on this pin has no Plan type. First-class entries are message / compaction / branch_summary / **custom** / **custom_message** / label (`docs/session-format.md`). Apps extend `CustomAgentMessages` by declaration merging (`agent/src/types.ts:310-319`); that is still “your type in custom messages”, not a Pi-native plan.

A **product-owned plan object** (GIS planning truth, recipes, acceptance) is therefore:

- **Not** the official example, and
- **Not** a core session type.

It can be stored as `appendEntry` / `custom_message` (extension layer) or introduced as a new session-format type (core change). Those are different objects.

---

## 5. Host needs: Pi core vs extension layer

Pi’s stated split: keep the core small; push workflow into extensions (`docs/usage.md` Design Principles; `README.md` Philosophy). That is the official boundary for a GIS host as well.

### Extension + CLI/RPC spawn (no core patch)

| Host need | Official mechanism |
|---|---|
| Not a coding agent | `--no-builtin-tools`; `--system-prompt` / `SYSTEM.md`; and/or `before_agent_start` string rewrite. Default prompt still opens “expert coding assistant” until replaced (`system-prompt.ts:121`). |
| Many GIS tools with JSON Schema | Repeated `pi.registerTool({ name, parameters, execute })`. One extension may own the whole surface. |
| Native planning / product / acceptance tools + long-tail proxy | Same: register a small native set; leave the rest inactive; loader tool calls `setActiveTools` (official Dynamic Tool Loading). |
| GIS persona that still lists tools | Prefer `before_agent_start` rewrite of the default prompt **or** a `customPrompt` that you assemble from `systemPromptOptions.toolSnippets` / `.promptGuidelines`. `--system-prompt` alone drops Available tools / Guidelines. |
| Inject turn context / verdicts | `before_agent_start.message`, `context` filter, or the host putting text on the RPC `prompt` (product already concatenates user + cartography block + turn marker). |
| Block bash/read if they get re-enabled | `tool_call` `{ block: true, reason }` (`docs/extensions.md` Tool Events). Registry still contains builtins under `--no-builtin-tools`. |
| Persist plan-like state across resume | `appendEntry` custom entries; restore on `session_start`. Optional `custom_message` if the model must see it. |
| Commands (`/plan`, …) | `pi.registerCommand`; invoked via RPC `prompt` with `/name` (`docs/rpc.md` `get_commands`). |
| Embed in a product UI | `--mode rpc` JSONL, or Node `AgentSession` SDK (`docs/rpc.md`, `docs/sdk.md`). |
| Sub-agents | Not in core. Example `examples/extensions/subagent/` / spawn another pi. |
| MCP | Not in core. Build as an extension if needed. |

### Product host (outside Pi), still no core fork

| Host need | Where it lives |
|---|---|
| GIS world keyed by `session_id` (ADR-0055) | Product session, not Pi session id (`get_state.sessionId` is Pi’s). Mapping is the bridge. |
| Tool execution against Python GIS | Extension `execute` HTTP callback (current `webgis_execute` shape) or in-process tools. |
| Frontend plan panel | Pi TUI `registerEntryRenderer` / `setWidget` do **not** drive the product React UI. RPC events + product state. |
| Do not run coding bash | Do not send RPC `{"type":"bash"}`. Do not `setActiveTools` back to `bash`. |

### Requires changing Pi core (or waiting on upstream)

| Host need | Why extension/CLI cannot |
|---|---|
| First-class session **Plan** type with typed RPC get/set | Session format has only generic `custom` / `custom_message`. |
| RPC command `set_active_tools` / `set_system_prompt` from Python without an extension | Command union has neither (`rpc-types.ts`). |
| Native deferred schemas on an unsupported provider | Fallback already works; cache-preserving wire format is pi-ai provider code (`deferred-tools.ts`, anthropic/openai serializers). |
| Change agent-loop semantics (e.g. stop the turn on GIS harness FAIL without a terminating tool) | Loop is `@earendil-works/pi-agent-core`. Extensions have `terminate: true` on **tool results** (all tools in the batch must terminate — `docs/extensions.md` “Early termination”) and `tool_call` block; they cannot replace the loop. |
| Make Pi session identity = GIS `session_id` | Pi session files/ids are owned by `SessionManager`. |
| Skills text that does not tell the model to `read` SKILL.md | `formatSkillsForPrompt` is core (`skills.ts:342-344`). Bypass: don’t rely on that section; expand `/skill:` yourself or inject via `before_agent_start`. |
| Default prompt that is not a coding assistant, with no flags/extensions | Hardcoded in `buildSystemPrompt` (`system-prompt.ts:121`). Official escape hatches already exist (`customPrompt`, `before_agent_start`). |

A GIS-native **plan type** as described by map #1016 is therefore **not** something vendored Pi already hosts. Storing it as extension custom entries is hosted. Promoting it to a Pi session type is a core change (or an upstream contribution of a new entry type — none exists through 0.84.3).

---

## Pin vs upstream (through 2026-08-27)

Compared to [upstream CHANGELOG](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/CHANGELOG.md) after `0.81.1` (pin is `v0.81.1-1-gdd6bea41`):

| After the pin | Effect on these five questions |
|---|---|
| 0.83.0 | RPC `bash` starts going through `user_bash`. TypeBox 1.3.7. Still no Plan type. |
| 0.84.0 | RPC `message_update` drops cumulative `message` (breaking for clients that relied on it). Experimental remote-session client. Still no Plan type / no RPC tool-prompt commands. |
| 0.84.2 | `defaultTools` setting for **startup builtins**; OpenAI Responses deferred loading prefers message-anchored `additional_tools`. `defaultTools` must not drop extension tools (explicit fix). |
| 0.84.3 / Unreleased | `clear_queue` RPC; `ui_prompt_start` / `ui_prompt_end`. Still no `set_active_tools` / `set_system_prompt` RPC. |

None of the post-pin releases add a product plan object or a documented GIS-host mode. The pin already contains the extension/CLI/RPC mechanisms in §§1–3.

---

## What this does not decide

Whether WebGIS should keep wrapping `vendor/pi`, fork it, or contribute a GIS extension upstream is map #1016 remaining work, not this ticket. Officially, a non-coding GIS agent **can** sit on vendored Pi without a core fork, with the limits above: no first-class Plan type, no post-spawn RPC tool/prompt API, deferred cache only on listed providers, default identity still “coding assistant” until the host replaces it.
