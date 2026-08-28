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
 *
 * #1044 硬化：回调 fetch 带 AbortSignal 预算（对齐后端 turn 预算），
 * 409/503/401 映射为恢复指引而非裸状态行；turn token 钉在滚动窗口之外
 * （工具密集回合不再把 marker 挤出窗口）；dump 解析失败大声报错而非静默降级。
 */
import { readFileSync } from "node:fs";

const WEBGIS_API_BASE = process.env.WEBGIS_API_BASE ?? "http://localhost:8000";
const BRIDGE_SECRET = process.env.WEBGIS_BRIDGE_SECRET ?? "";
const TURN_CONTEXT_RE = /WEBGIS_TURN_CONTEXT:([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)/g;

// 与后端 turn 预算对齐：spawn 时后端把 PI_TURN_TOTAL_TIMEOUT（默认 300s）
// 换算成 WEBGIS_BRIDGE_TIMEOUT_MS 注入本进程，一次回调挂死不可能也不应该
// 活得比服务端的回合预算更久；300s 只是脱离后端独立运行时的兜底默认。
const BRIDGE_TIMEOUT_MS = Number.parseInt(process.env.WEBGIS_BRIDGE_TIMEOUT_MS ?? "", 10) > 0
  ? Number.parseInt(process.env.WEBGIS_BRIDGE_TIMEOUT_MS, 10)
  : 300_000;

// 模型面的恢复指引：裸 "HTTP 409: Conflict" 只会诱发盲目重试。
const STATUS_GUIDANCE = {
  401: {
    error: "turn_context_rejected",
    text: "The WebGIS backend rejected the turn context as invalid or expired. Do not retry tool calls; finish the reply and tell the user to send a new message to re-establish the map session.",
  },
  409: {
    error: "turn_not_active",
    text: "This turn is no longer active (it completed, was aborted, or was superseded by a newer message). Retrying will keep failing — stop calling GIS tools and summarize the results you already have for the user.",
  },
  503: {
    error: "bridge_unavailable",
    text: "The WebGIS backend is temporarily unavailable (starting up or overloaded). Wait a few seconds and retry the same call once; if it fails again, report the outage to the user instead of retrying further.",
  },
};

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
    if (!Array.isArray(parsed)) {
      throw new TypeError(`dump root is ${Object.prototype.toString.call(parsed)}, expected an array`);
    }
    return parsed;
  } catch (error) {
    // #1044：spawn 侧对 dump 失败已经 fail-fast，走到这里说明文件在写后
    // 损坏/被改写。静默返回 [] 会复活"crippled GeoAgent、零诊断"的洞 ——
    // 大声报到 stderr（PiRpcClient 会转发 Pi 子进程 stderr），再降级为
    // 仅 webgis_execute，让长尾工具与显式报错仍可达。
    const message = error instanceof Error ? error.message : String(error);
    console.error(
      `[webgis-tools] WEBGIS_NATIVE_TOOLS_PATH is set but the dump at ${path} is unusable: ${message}. ` +
        "Native GIS tools are NOT registered this session (webgis_execute still is); " +
        "the spawn-time dump should have prevented this — investigate the file.",
    );
    return [];
  }
}

// #1044：marker 钉在滚动窗口之外。此前只扫最近 24 条 session entry，一个
// 工具密集回合（每步约 2-3 条）会在 ~8-10 步后把 turn marker 挤出窗口，
// 之后所有回调都 401 missing_turn_context。改为：按 sessionManager 记住
// 上次命中的 {index, token}，每次只扫上次命中之后的新 entry（追加型会话的
// 稳态成本 = 新 entry 数 + 钉住点一次校验），扫不到新 marker 就沿用钉住的
// token。新回合的 marker 一定在更高 index，回扫先命中，不会被记忆中的旧
// token 遮蔽；钉住点越界或不再携带钉住 token（会话重置、压缩重写）时作废
// 重扫。校验是子串级的：重写后原样引用旧 marker 的病态 entry 仍会沿用旧
// token —— 服务端的签名/活跃 turn 校验（401/409）是最终裁决者。
const TURN_TOKEN_MEMO = new WeakMap();

function entryStillHasToken(entry, token) {
  try {
    return JSON.stringify(entry).includes(`WEBGIS_TURN_CONTEXT:${token}`);
  } catch {
    return false;
  }
}

export function currentTurnToken(ctx) {
  const manager = ctx?.sessionManager;
  const entries = manager?.getEntries?.() ?? [];
  let pinned = TURN_TOKEN_MEMO.get(manager);
  if (pinned && (pinned.index >= entries.length || !entryStillHasToken(entries[pinned.index], pinned.token))) {
    TURN_TOKEN_MEMO.delete(manager);
    pinned = undefined;
  }
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    if (pinned && index === pinned.index) break;
    let serialized = "";
    try {
      serialized = JSON.stringify(entries[index]);
    } catch {
      continue;
    }
    const matches = Array.from(serialized.matchAll(TURN_CONTEXT_RE));
    if (matches.length) {
      // 取该 entry 的最后一个匹配：后端 marker 排在不可信用户文本之后，
      // 用户伪造的同形 marker 不能遮蔽服务器能力。
      const token = matches[matches.length - 1][1];
      TURN_TOKEN_MEMO.set(manager, { index, token });
      return token;
    }
  }
  return pinned ? pinned.token : "";
}

export async function postToBridge(toolCallId, name, args, turnToken) {
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
      signal: AbortSignal.timeout(BRIDGE_TIMEOUT_MS),
    });
    if (!response.ok) {
      const guidance = STATUS_GUIDANCE[response.status];
      return {
        content: [{
          type: "text",
          text: guidance
            ? `HTTP ${response.status}: ${guidance.text}`
            : `HTTP ${response.status}: ${response.statusText}`,
        }],
        details: {
          error: guidance ? guidance.error : `HTTP ${response.status}`,
          status: response.status,
          toolName: name,
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
    const errorName = error instanceof Error ? error.name : "";
    const message = errorName === "TimeoutError" || errorName === "AbortError"
      ? `bridge fetch timed out after ${BRIDGE_TIMEOUT_MS}ms (backend did not answer within the turn budget); do not blind-retry — the tool may still be running server-side`
      : error instanceof Error ? error.message : String(error);
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
