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

const GIS_IDENTITY =
  "You are GeoAgent, a WebGIS spatial analysis agent. You perceive the map, run GIS tools, and produce cartographic insight. Geographic questions use native GIS tools (webgis_map_intent, query_local_poi, get_local_admin_boundary) or webgis_execute for the long tail — never bash, never reading or writing files.";

const CODING_ASSISTANT_OPENING =
  "You are an expert coding assistant operating inside pi, a coding agent harness. You help users by reading files, executing commands, editing code, and writing new files.";

function loadNativeTools() {
  const path = process.env.WEBGIS_NATIVE_TOOLS_PATH;
  if (!path) return [];
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8"));
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function currentTurnToken(ctx) {
  const entries = ctx?.sessionManager?.getEntries?.() ?? [];
  const start = Math.max(0, entries.length - 24);
  for (let index = entries.length - 1; index >= start; index -= 1) {
    let serialized = "";
    try {
      serialized = JSON.stringify(entries[index]);
    } catch {
      continue;
    }
    const matches = Array.from(serialized.matchAll(TURN_CONTEXT_RE));
    if (matches.length) return matches[matches.length - 1][1];
  }
  return "";
}

async function postToBridge(toolCallId, name, args, turnToken) {
  if (!turnToken) {
    return {
      content: [{ type: "text", text: "WebGIS tool execution rejected: missing turn context" }],
      details: { error: "missing_turn_context", toolName: name },
      isError: true,
    };
  }
  try {
    const response = await fetch(`${WEBGIS_API_BASE}/pi-tools/execute`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Pi-Bridge-Secret": BRIDGE_SECRET,
      },
      body: JSON.stringify({ toolCallId, name, arguments: args, turnToken }),
    });
    if (!response.ok) {
      return {
        content: [{ type: "text", text: `HTTP ${response.status}: ${response.statusText}` }],
        details: { error: `HTTP ${response.status}`, toolName: name },
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
  const nativeTools = loadNativeTools();
  // Wrap-reject only names actually registered. FALLBACK_NATIVE is prompt
  // vocabulary; if the schema dump missed, wrapping still reaches Python.
  const nativeNames = new Set(nativeTools.map((tool) => tool.name).filter(Boolean));

  for (const tool of nativeTools) {
    if (!tool?.name) continue;
    pi.registerTool({
      name: tool.name,
      label: tool.label || tool.name,
      description: tool.description || tool.name,
      promptSnippet: tool.promptSnippet,
      parameters: tool.parameters || { type: "object", properties: {} },
      async execute(toolCallId, params, _signal, _onUpdate, ctx) {
        return postToBridge(toolCallId, tool.name, params || {}, currentTurnToken(ctx));
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
      );
    },
  });

  if (typeof pi.on === "function") {
    pi.on("before_agent_start", async (event) => {
      const current = event.systemPrompt || "";
      const rewritten = current.includes(CODING_ASSISTANT_OPENING)
        ? current.replace(CODING_ASSISTANT_OPENING, GIS_IDENTITY)
        : `${GIS_IDENTITY}\n\n${current}`;
      return { systemPrompt: rewritten };
    });
  }
}
