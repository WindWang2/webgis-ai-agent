/**
 * WebGIS Tools Extension for Pi
 *
 * Registers a single proxy tool `webgis_execute` that calls back to
 * the Python FastAPI server at http://localhost:8000/pi-tools/execute.
 */
import type { ExtensionAPI, ToolDefinition } from "@earendil-works/pi-coding-agent";
import { TSchema, Type } from "typebox";

const WEBGIS_API_BASE = process.env.WEBGIS_API_BASE ?? "http://localhost:8000";
const BRIDGE_SECRET = process.env.WEBGIS_BRIDGE_SECRET ?? "";
const TURN_CONTEXT_RE = /WEBGIS_TURN_CONTEXT:([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)/;

function currentTurnToken(ctx: any): string {
	const entries = ctx?.sessionManager?.getEntries?.() ?? [];
	for (let index = entries.length - 1; index >= Math.max(0, entries.length - 24); index -= 1) {
		try {
			const match = TURN_CONTEXT_RE.exec(JSON.stringify(entries[index]));
			if (match) return match[1];
		} catch { /* ignore unserializable extension entries */ }
	}
	return "";
}

export default function webgisToolsExtension(pi: ExtensionAPI): void {
	pi.registerTool(defineWebgisExecuteTool());
}

function defineWebgisExecuteTool(): ToolDefinition<TSchema> {
	return {
		name: "webgis_execute",
		label: "WebGIS Tool Executor",
		description: [
			"Execute a Python GIS tool on behalf of the agent.",
			"Use this when the user's request requires spatial analysis, raster operations,",
			"geocoding, routing, or other GIS-specific capabilities.",
			"The tool name must match a registered Python GIS tool.",
		].join("\n"),
		promptSnippet: "Use webgis_execute(toolName, arguments) for GIS operations.",
		parameters: Type.Object({
			toolName: Type.String({ description: "Name of the GIS tool to execute (e.g., spatial_analyze, raster_ndvi)" }),
			arguments: Type.Record(Type.String(), Type.Any(), { description: "Tool-specific arguments as a JSON object" }),
		}),
		async execute(toolCallId, params, _signal, _onUpdate, ctx) {
			const { toolName, arguments: args } = params as {
				toolName: string;
				arguments: Record<string, unknown>;
			};

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

				const result = (await response.json()) as {
					toolCallId: string;
					content: Array<{ type: string; text: string }>;
					details?: unknown;
					isError: boolean;
				};

				return {
					content: result.content ?? [{ type: "text", text: JSON.stringify(result) }],
					details: result.details ?? result,
					isError: result.isError,
				};
			} catch (error: unknown) {
				const message = error instanceof Error ? error.message : String(error);
				return {
					content: [{ type: "text", text: `WebGIS tool execution failed: ${message}` }],
					details: { error: message, toolName },
					isError: true,
				};
			}
		},
	};
}
