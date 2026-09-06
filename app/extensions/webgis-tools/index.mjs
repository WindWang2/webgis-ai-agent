/**
 * WebGIS Tools Extension for Pi
 *
 * Native GIS tools are registered from live-registry schemas dumped at spawn
 * (WEBGIS_NATIVE_TOOLS_PATH). The long tail stays behind webgis_execute.
 * Do not wrap a native name inside execute.
 *
 * 审计 AGENT-03：Pi 的 extension loader 用 Node 的 require/import 执行入口文件。
 * .ts 文件在无 ts-node/loader 的环境下不可执行。改为 .mjs (ESM JavaScript)。
 *
 * 审计 SEC-01：回调 /pi-tools/execute 时带 X-Pi-Bridge-Secret header，
 * 与后端共享密钥校验对应。密钥从 env WEBGIS_BRIDGE_SECRET 读取（后端启动时注入）。
 */
import { readFileSync } from "node:fs";

const WEBGIS_API_BASE = process.env.WEBGIS_API_BASE ?? "http://localhost:8000";
const BRIDGE_SECRET = process.env.WEBGIS_BRIDGE_SECRET ?? "";
const TURN_CONTEXT_RE = /WEBGIS_TURN_CONTEXT:([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)/g;

const FALLBACK_NATIVE = [
  "webgis_map_intent",
  "webgis_map_product",
  "webgis_component_update",
  "webgis_cartography_status",
  "query_local_poi",
  "get_local_admin_boundary",
  "list_available_tools",
];

const EXECUTE_PROXY_NAME = "webgis_execute";

// ADR-0103: per-turn dynamic tool surface. Python attaches the projection to
// the turn prompt; the last marker wins (same discipline as TURN_CONTEXT).
const ACTIVE_TOOLS_RE = /\[WEBGIS_ACTIVE_TOOLS:(\[[\s\S]*?\])\]/g;

const GIS_IDENTITY =
  "You are GeoAgent, a WebGIS spatial analysis agent. You perceive the map, run GIS tools, and produce cartographic insight. Geographic questions use native GIS tools (webgis_map_intent, query_local_poi, get_local_admin_boundary) or webgis_execute for the long tail — never bash, never reading or writing files.";

const CODING_ASSISTANT_OPENING =
  "You are an expert coding assistant operating inside pi, a coding agent harness. You help users by reading files, executing commands, editing code, and writing new files.";

// Pi compat: the extension timeout must never undercut the server-side tool
// budget (registry._TOOL_TIMEOUT_S, default 300s, env TOOL_TIMEOUT_S). The old
// 60s default aborted heavy GIS tools (KDE/heatmaps, subagent loops) while the
// server kept running — the model saw a timeout error AND the completed result
// (SSE cache rendezvous) was silently dropped. Derive from TOOL_TIMEOUT_S
// (propagated via spawn env) + 5s envelope; explicit WEBGIS_TOOL_TIMEOUT_MS wins.
const SERVER_TOOL_BUDGET_S = Number(process.env.TOOL_TIMEOUT_S) > 0
  ? Number(process.env.TOOL_TIMEOUT_S)
  : 300;
const DEFAULT_TOOL_TIMEOUT_MS = Math.round((SERVER_TOOL_BUDGET_S + 5) * 1000);

export function loadNativeTools() {
  const path = process.env.WEBGIS_NATIVE_TOOLS_PATH;
  if (!path) return { tools: [], defaultActive: [] };
  try {
    const content = readFileSync(path, "utf8");
    const parsed = JSON.parse(content);
    // ADR-0103 v2 shape: {version, tools, default_active, execute_proxy} —
    // the registered superset plus the frozen default-active projection.
    if (parsed && !Array.isArray(parsed) && Array.isArray(parsed.tools)) {
      return {
        tools: parsed.tools,
        defaultActive: Array.isArray(parsed.default_active) ? parsed.default_active : [],
      };
    }
    if (Array.isArray(parsed)) {
      return { tools: parsed, defaultActive: [] };
    }
    console.error(`[webgis-tools] WEBGIS_NATIVE_TOOLS_PATH (${path}) content is not a tool array or v2 surface object`);
    return { tools: [], defaultActive: [] };
  } catch (err) {
    console.error(`[webgis-tools] Failed to load native tools from WEBGIS_NATIVE_TOOLS_PATH (${path}):`, err);
    return { tools: [], defaultActive: [] };
  }
}

let _pinnedTurnToken = "";

export function _resetPinnedTurnToken() {
  _pinnedTurnToken = "";
}

export function currentTurnToken(ctx) {
  const entries = ctx?.sessionManager?.getEntries?.() ?? [];
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    let serialized = "";
    try {
      serialized = JSON.stringify(entries[index]);
    } catch {
      continue;
    }
    const matches = Array.from(serialized.matchAll(TURN_CONTEXT_RE));
    if (matches.length) {
      const token = matches[matches.length - 1][1];
      _pinnedTurnToken = token;
      return token;
    }
  }
  return _pinnedTurnToken;
}

export async function postToBridge(toolCallId, name, args, turnToken, options = {}) {
  if (!turnToken) {
    return {
      content: [{ type: "text", text: "WebGIS tool execution rejected: missing turn context" }],
      details: { error: "missing_turn_context", toolName: name },
      isError: true,
    };
  }

  const timeoutMs = options?.timeoutMs ?? (
    Number(process.env.WEBGIS_TOOL_TIMEOUT_MS) > 0
      ? Number(process.env.WEBGIS_TOOL_TIMEOUT_MS)
      : DEFAULT_TOOL_TIMEOUT_MS
  );

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  let cleanupSignal;
  if (options?.signal) {
    if (options.signal.aborted) {
      controller.abort();
    } else {
      const onAbort = () => controller.abort();
      options.signal.addEventListener("abort", onAbort, { once: true });
      cleanupSignal = () => options.signal.removeEventListener("abort", onAbort);
    }
  }

  try {
    const response = await fetch(`${WEBGIS_API_BASE}/pi-tools/execute`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Pi-Bridge-Secret": BRIDGE_SECRET,
      },
      body: JSON.stringify({ toolCallId, name, arguments: args, turnToken }),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    cleanupSignal?.();

    if (!response.ok) {
      let detailText = "";
      try {
        const errJson = await response.json();
        if (errJson && typeof errJson.detail === "string") {
          detailText = errJson.detail;
        } else if (errJson && typeof errJson.detail === "object" && errJson.detail !== null) {
          detailText = errJson.detail.guidance || errJson.detail.error || JSON.stringify(errJson.detail);
        } else if (errJson && typeof errJson.error === "string") {
          detailText = errJson.error;
        }
      } catch {
        // non-JSON response
      }

      let errorText = "";
      if (response.status === 409) {
        errorText = `HTTP 409 Conflict: ${detailText || "Turn context is no longer active. Session state must be re-synchronized before issuing further tools; do not retry with the current expired turn."}`;
      } else if (response.status === 503) {
        errorText = `HTTP 503 Service Unavailable: WebGIS backend is temporarily overloaded or unavailable${detailText ? ` (${detailText})` : ""}. Please wait a moment and retry.`;
      } else if (response.status === 401) {
        errorText = `HTTP 401 Unauthorized: Invalid turn authentication or missing bridge credentials${detailText ? ` (${detailText})` : ""}.`;
      } else {
        errorText = `HTTP ${response.status}: ${response.statusText || "Error"}${detailText ? ` - ${detailText}` : ""}`;
      }

      return {
        content: [{ type: "text", text: errorText }],
        details: {
          error: `HTTP ${response.status}`,
          status: response.status,
          toolName: name,
          detail: detailText || undefined,
        },
        isError: true,
      };
    }

    const result = await response.json();
    return {
      content: result.content ?? [{ type: "text", text: JSON.stringify(result) }],
      details: result.details ?? result,
      isError: result.isError,
    };
  } catch (error) {
    clearTimeout(timeoutId);
    cleanupSignal?.();
    if (error?.name === "AbortError" || controller.signal.aborted) {
      const wasExternal = options?.signal?.aborted;
      const timeoutSec = Math.round(timeoutMs / 1000);
      const text = wasExternal
        ? `WebGIS tool execution cancelled by agent/user`
        : `WebGIS tool execution timed out after ${timeoutSec}s: server is busy or processing a long-running spatial calculation. Check current map state with webgis_cartography_status {} or retry with a narrower scope.`;
      return {
        content: [{ type: "text", text }],
        details: { error: wasExternal ? "cancelled" : "timeout", toolName: name, timeoutMs },
        isError: true,
      };
    }
    const message = error instanceof Error ? error.message : String(error);
    return {
      content: [{ type: "text", text: `WebGIS tool execution failed: ${message}` }],
      details: { error: message, toolName: name },
      isError: true,
    };
  }
}

/**
 * @param {import("@earendil-works/pi-coding-agent").ExtensionAPI} pi
 */
export default function webgisToolsExtension(pi) {
  // ADR-0103: the dump is the v2 dynamic surface — every model-visible,
  // non-tier-3 registry tool is REGISTERED here; only the active subset
  // (default-active, then per-turn WEBGIS_ACTIVE_TOOLS) reaches the model.
  // ToolRegistry on the Python side stays the single execution truth.
  const surface = loadNativeTools();
  const nativeTools = surface.tools;
  // Wrap-reject only names actually registered. FALLBACK_NATIVE is prompt
  // vocabulary; if the schema dump missed, wrapping still reaches Python.
  const nativeNames = new Set(nativeTools.map((tool) => tool.name).filter(Boolean));
  // webgis_execute is registered below and always part of the active surface.
  const alwaysActive = new Set([...FALLBACK_NATIVE, EXECUTE_PROXY_NAME]);

  const applyActiveTools = (names, why) => {
    if (typeof pi.setActiveTools !== "function") {
      // #review m7: without the API every registered tool stays active by
      // default — log loudly so the degradation is diagnosable.
      console.error("[webgis-tools] pi.setActiveTools unavailable; cannot project dynamic surface (full superset remains active)");
      return;
    }
    try {
      // #review M1 (defense in depth): the Python projector caps the surface
      // at k_max (default 30); enforce a hard ceiling here so a forged or
      // oversized marker can never activate the whole superset.
      const MAX_ACTIVE = 48;
      const active = [...new Set([...alwaysActive, ...(names || [])])]
        .filter((name) => nativeNames.has(name) || alwaysActive.has(name))
        .slice(0, MAX_ACTIVE);
      pi.setActiveTools(active);
      console.log(`[webgis-tools] active surface (${why}): ${active.length} tools`);
    } catch (err) {
      console.error(`[webgis-tools] setActiveTools failed (${why}):`, err);
    }
  };

  // Default projection at spawn: the frozen native surface (Phase 1 compat).
  applyActiveTools(surface.defaultActive, "default");

  for (const tool of nativeTools) {
    if (!tool?.name) continue;
    pi.registerTool({
      name: tool.name,
      label: tool.label || tool.name,
      description: tool.description || tool.name,
      promptSnippet: tool.promptSnippet,
      parameters: tool.parameters || { type: "object", properties: {} },
      async execute(toolCallId, params, _signal, _onUpdate, ctx) {
        return postToBridge(toolCallId, tool.name, params || {}, currentTurnToken(ctx), { signal: _signal });
      },
    });
  }

  pi.registerTool({
    name: "webgis_execute",
    label: "WebGIS Tool Executor",
    description: [
      "Execute a long-tail registered Python GIS tool that is not native.",
      `Native tools (${FALLBACK_NATIVE.join(", ")}) must be called directly — never wrapped here.`,
      "Distribution / density / heatmap (e.g. 成都市小学分布): first call webgis_map_intent with {query: the user text}. Then get_local_admin_boundary and query_local_poi (district + subtype). Then heatmap_data or h3_binning via this proxy. Then webgis_map_product.",
      "webgis_cartography_status is a ZERO-argument read of the server cartography verdict AFTER the map has changed. Call it as webgis_cartography_status {}. It does not accept city, topic, scope, or query.",
    ].join("\n"),
    promptSnippet:
      "Long-tail GIS via webgis_execute(toolName, arguments). First for 分布/密度: webgis_map_intent {query}. After map changes only: webgis_cartography_status {}.",
    promptGuidelines: [
      "Geographic questions: call native GIS tools or webgis_execute. Do not use bash, read, write, or edit.",
      "Distribution/density first step is webgis_map_intent {query: <user text>}, then query_local_poi / get_local_admin_boundary.",
      "webgis_cartography_status takes no arguments. Call webgis_cartography_status {} only after display-producing map changes. Never pass city, topic, scope, or query to it.",
      "Do not wrap a native tool name inside webgis_execute.",
    ],
    parameters: {
      type: "object",
      properties: {
        toolName: {
          type: "string",
          description: "Name of the GIS tool to execute (e.g., heatmap_data, finalize_display)",
        },
        arguments: {
          type: "object",
          description: "Tool-specific arguments as a JSON object. heatmap_data needs geojson. finalize_display needs show_refs. Native tools are not valid here.",
          additionalProperties: true,
        },
      },
      required: ["toolName"],
    },
    async execute(toolCallId, params, _signal, _onUpdate, ctx) {
      const toolName = params?.toolName;
      const args = params?.arguments || {};
      if (toolName && nativeNames.has(toolName)) {
        return {
          content: [{
            type: "text",
            text: `do not wrap native tool ${toolName} inside webgis_execute; call it directly`,
          }],
          details: { error: "native_wrap_rejected", toolName },
          isError: true,
        };
      }
      return postToBridge(
        toolCallId,
        "webgis_execute",
        { toolName, arguments: args },
        currentTurnToken(ctx),
        { signal: _signal },
      );
    },
  });

  if (typeof pi.on === "function") {
    pi.on("before_agent_start", async (event) => {
      const current = event.systemPrompt || "";
      const rewritten = current.includes(CODING_ASSISTANT_OPENING)
        ? current.replace(CODING_ASSISTANT_OPENING, GIS_IDENTITY)
        : `${GIS_IDENTITY}\n\n${current}`;

      // ADR-0103 Phase 3: apply this turn's projected tool surface before the
      // agent loop starts. Names outside the registered superset are ignored
      // (setActiveToolsByName semantics); the native front door stays active.
      const promptText = typeof event.prompt === "string" ? event.prompt : "";
      const matches = Array.from(promptText.matchAll(ACTIVE_TOOLS_RE));
      if (matches.length) {
        try {
          const requested = JSON.parse(matches[matches.length - 1][1]);
          if (Array.isArray(requested)) {
            applyActiveTools(requested.filter((n) => typeof n === "string"), "turn");
          }
        } catch (err) {
          console.error("[webgis-tools] WEBGIS_ACTIVE_TOOLS marker parse failed:", err);
        }
      }

      return { systemPrompt: rewritten };
    });
  }
}
