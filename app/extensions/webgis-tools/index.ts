/**
 * WebGIS Tools Extension for Pi
 *
 * ⚠️ #694：本文件是 index.mjs 的**死副本**——Pi 只加载编译后的 index.mjs。
 * 编辑本文件不会生效；改完 .ts 源后必须重新编译/同步到 index.mjs。
 *
 * Registers a single proxy tool `webgis_execute` that calls back to
 * the Python FastAPI server at http://localhost:8000/pi-tools/execute.
 */
import type { ExtensionAPI, ToolDefinition } from "@earendil-works/pi-coding-agent";
import { TSchema, Type } from "typebox";

const WEBGIS_API_BASE = process.env.WEBGIS_API_BASE ?? "http://localhost:8000";
const BRIDGE_SECRET = process.env.WEBGIS_BRIDGE_SECRET ?? "";
const TURN_CONTEXT_RE = /WEBGIS_TURN_CONTEXT:([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)/g;

let _pinnedTurnToken = "";

export function _resetPinnedTurnToken(): void {
	_pinnedTurnToken = "";
}

export function currentTurnToken(ctx: any): string {
	const entries = ctx?.sessionManager?.getEntries?.() ?? [];
	for (let index = entries.length - 1; index >= 0; index -= 1) {
		try {
			// The backend appends its marker after the untrusted user text. Take
			// the last marker in the newest entry so a user-supplied lookalike
			// earlier in that same entry cannot shadow the server capability.
			const matches = Array.from(JSON.stringify(entries[index]).matchAll(TURN_CONTEXT_RE));
			if (matches.length) {
				const token = matches[matches.length - 1][1];
				_pinnedTurnToken = token;
				return token;
			}
		} catch { /* ignore unserializable extension entries */ }
	}
	return _pinnedTurnToken;
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
			"After display-producing map changes, verify the server-side cartography verdict",
			"by executing the read-only tool 'webgis_cartography_status'.",
		].join("\n"),
		promptSnippet: "Use webgis_execute(toolName, arguments) for GIS operations; verify map convergence with webgis_execute('webgis_cartography_status', {}).",
		parameters: Type.Object({
			toolName: Type.String({ description: "Name of the GIS tool to execute (e.g., spatial_aggregate, compute_ndvi)" }),
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

			const timeoutMs = Number(process.env.WEBGIS_TOOL_TIMEOUT_MS) > 0
				? Number(process.env.WEBGIS_TOOL_TIMEOUT_MS)
				: 60000;
			const controller = new AbortController();
			const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

			try {
				const response = await fetch(`${WEBGIS_API_BASE}/pi-tools/execute`, {
					method: "POST",
					headers: {
						"Content-Type": "application/json",
						"X-Pi-Bridge-Secret": BRIDGE_SECRET,
					},
					body: JSON.stringify({ toolCallId, name: toolName, arguments: args, turnToken }),
					signal: controller.signal,
				});
				clearTimeout(timeoutId);

				if (!response.ok) {
					let detailText = "";
					try {
						const errJson = (await response.json()) as any;
						if (errJson && typeof errJson.detail === "string") {
							detailText = errJson.detail;
						} else if (errJson && typeof errJson.error === "string") {
							detailText = errJson.error;
						}
					} catch {
						// non-JSON
					}

					let errorText = "";
					if (response.status === 409) {
						errorText = `HTTP 409 Conflict: Concurrent mutation conflict on session state${detailText ? ` (${detailText})` : ""}. Retry the tool call or inspect the current map state with webgis_cartography_status {}.`;
					} else if (response.status === 503) {
						errorText = `HTTP 503 Service Unavailable: WebGIS backend is temporarily overloaded or unavailable${detailText ? ` (${detailText})` : ""}. Please wait a moment and retry.`;
					} else if (response.status === 401) {
						errorText = `HTTP 401 Unauthorized: Invalid turn authentication or missing bridge credentials${detailText ? ` (${detailText})` : ""}.`;
					} else {
						errorText = `HTTP ${response.status}: ${response.statusText || "Error"}${detailText ? ` - ${detailText}` : ""}`;
					}

					return {
						content: [{ type: "text", text: errorText }],
						details: { error: `HTTP ${response.status}`, status: response.status, toolName, detail: detailText || undefined },
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
				clearTimeout(timeoutId);
				if ((error as any)?.name === "AbortError" || controller.signal.aborted) {
					const timeoutSec = Math.round(timeoutMs / 1000);
					return {
						content: [{
							type: "text",
							text: `WebGIS tool execution timed out after ${timeoutSec}s: server is busy or processing a long-running spatial calculation. Check current map state with webgis_cartography_status {} or retry with a narrower scope.`,
						}],
						details: { error: "timeout", toolName, timeoutMs },
						isError: true,
					};
				}
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
