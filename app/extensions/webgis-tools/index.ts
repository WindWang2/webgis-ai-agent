/**
 * WebGIS Tools Extension for Pi
 *
 * Registers a single proxy tool `webgis_execute` that calls back to
 * the Python FastAPI server at http://localhost:8000/pi-tools/execute.
 */
import type { ExtensionAPI, ToolDefinition } from "@earendil-works/pi-coding-agent";
import { TSchema, Type } from "typebox";

const WEBGIS_API_BASE = process.env.WEBGIS_API_BASE ?? "http://localhost:8000";

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
		async execute(toolCallId, params) {
			const { toolName, arguments: args } = params as {
				toolName: string;
				arguments: Record<string, unknown>;
			};

			try {
				const response = await fetch(`${WEBGIS_API_BASE}/pi-tools/execute`, {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ toolCallId, name: toolName, arguments: args }),
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
