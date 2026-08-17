/**
 * WebGIS Tools Extension for Pi
 *
 * 审计 AGENT-03：Pi 的 extension loader 用 Node 的 require/import 执行入口文件。
 * .ts 文件在无 ts-node/loader 的环境下不可执行。改为 .mjs (ESM JavaScript)。
 *
 * 审计 SEC-01：回调 /pi-tools/execute 时带 X-Pi-Bridge-Secret header，
 * 与后端共享密钥校验对应。密钥从 env WEBGIS_BRIDGE_SECRET 读取（后端启动时注入）。
 */
const WEBGIS_API_BASE = process.env.WEBGIS_API_BASE ?? "http://localhost:8000";
const BRIDGE_SECRET = process.env.WEBGIS_BRIDGE_SECRET ?? "";
const TURN_CONTEXT_RE = /WEBGIS_TURN_CONTEXT:([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)/g;

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

/**
 * @param {import("@earendil-works/pi-coding-agent").ExtensionAPI} pi
 */
export default function webgisToolsExtension(pi) {
  pi.registerTool({
    name: "webgis_execute",
    label: "WebGIS Tool Executor",
    description: [
      "Execute a Python GIS tool on behalf of the agent.",
      "Use this when the user's request requires spatial analysis, raster operations,",
      "geocoding, routing, or other GIS-specific capabilities.",
      "The tool name must match a registered Python GIS tool.",
    ].join("\n"),
    promptSnippet: "Use webgis_execute(toolName, arguments) for GIS operations.",
    parameters: {
      type: "object",
      properties: {
        toolName: {
          type: "string",
          description: "Name of the GIS tool to execute (e.g., spatial_aggregate, compute_ndvi)",
        },
        arguments: {
          type: "object",
          description: "Tool-specific arguments as a JSON object",
          additionalProperties: true,
        },
      },
      required: ["toolName"],
    },
    async execute(toolCallId, params, _signal, _onUpdate, ctx) {
      const { toolName, arguments: args } = params;
      // Capture at execute start. A later turn cannot retag an in-flight fetch.
      const turnToken = currentTurnToken(ctx);
      if (!turnToken) {
        return {
          content: [{ type: "text", text: "WebGIS tool execution rejected: missing turn context" }],
          details: { error: "missing_turn_context", toolName },
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
          body: JSON.stringify({ toolCallId, name: toolName, arguments: args, turnToken }),
        });

        if (!response.ok) {
          return {
            content: [{ type: "text", text: `HTTP ${response.status}: ${response.statusText}` }],
            details: { error: `HTTP ${response.status}`, toolName },
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
          details: { error: message, toolName },
          isError: true,
        };
      }
    },
  });
}
