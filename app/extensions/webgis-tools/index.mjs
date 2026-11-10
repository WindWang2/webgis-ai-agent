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
 * 目录说明（#1546）：app/extensions/ 不是 Python 包，也与 Python 扩展平台
 * app/extensions_platform/ 无关 —— 本目录只承载 Pi coding-agent 的扩展入口
 * 产物（本文件），由 app/main.py 启动时经 extension_paths 传入 vendor/pi 加载。
 */
import { readFileSync } from "node:fs";

const WEBGIS_API_BASE = process.env.WEBGIS_API_BASE ?? "http://localhost:8000";
const BRIDGE_SECRET = process.env.WEBGIS_BRIDGE_SECRET ?? "";
const TURN_CONTEXT_SUFFIX_RE = /(?:^|\n\n)\[WEBGIS_TURN_CONTEXT:([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\]\n\n\(Internal routing context; do not quote or modify this marker\.\)\s*$/;

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

// ADR-0288（W04）：上游内置发现入口 —— `--no-builtin-tools` 只清默认工具
// 选择，内置扩展（tool-search/codemode）仍以 defaultActive:false +
// exposure:"model-only" 注册（C11）。把它们并入常驻激活集后，模型可
// tool_search 发现 deferred 长尾（C12 merge 语义，前门不丢），或经 codemode
// 沙箱做有界只读组合（C13）。两者只能被模型直调，脚本不可触达。
// PI_NATIVE_DISCOVERY=0（spawn env）一键回退 G12 前行为。
const DISCOVERY_ENTRY_TOOLS = ["tool_search", "codemode"];
const NATIVE_DISCOVERY_ENABLED = process.env.PI_NATIVE_DISCOVERY !== "0";

// ADR-0103: per-turn dynamic tool surface. Python attaches the projection to
// the turn prompt. Only its final control paragraph is authoritative.
const ACTIVE_TOOLS_SUFFIX_RE = /(?:^|\n\n)\[WEBGIS_ACTIVE_TOOLS:(\[[^\r\n]*?\])\](?:\[工具面(?:提示|资格)\][^\r\n]*)*$/;

const GIS_IDENTITY =
  "You are GeoAgent, a WebGIS spatial analysis agent. You perceive the map, run GIS tools, and produce cartographic insight. Geographic questions use native GIS tools (webgis_map_intent, query_local_poi, get_local_admin_boundary) or webgis_execute for the long tail — never bash, never reading or writing files.";

// Vendor preamble openings we strip when injecting the GIS persona. The exact
// wording changed upstream across pins: v0.81 ("expert coding assistant
// operating inside pi…") → v1.5.0 ("a coding assistant running inside pi…").
// Any matched variant is REPLACED by GIS_IDENTITY; an unmatched prompt falls
// back to prefixing GIS_IDENTITY (behavior unchanged from the original shim).
const CODING_ASSISTANT_OPENINGS = [
  "You are an expert coding assistant operating inside pi, a coding agent harness. You help users by reading files, executing commands, editing code, and writing new files.",
  "You are a coding assistant running inside pi, a coding agent harness. Help users inspect files, run commands, and modify code.",
];

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

// Kept as a compatibility export; tokens are never retained across contexts.
export function _resetPinnedTurnToken() {}

function routingSuffix(prompt) {
  const match = prompt.match(TURN_CONTEXT_SUFFIX_RE);
  return match ? { token: match[1], body: prompt.slice(0, match.index) } : null;
}

function userEntryText(entry) {
  if (typeof entry === "string") return entry;
  const message = entry?.message ?? entry;
  if (message?.role !== "user") return null;
  if (typeof message.content === "string") return message.content;
  if (Array.isArray(message.content)) {
    return message.content.filter((part) => part?.type === "text" && typeof part.text === "string")
      .map((part) => part.text).join("\n");
  }
  return "";
}

export function currentTurnToken(ctx) {
  const entries = ctx?.sessionManager?.getEntries?.() ?? [];
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const text = userEntryText(entries[index]);
    if (text !== null) return routingSuffix(text)?.token ?? "";
  }
  return "";
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
    // ADR-0224: echo the dump-v3 spec fingerprint when known. The
    // backend compares it against the turn's SurfaceReceipt to detect stale
    // surfaces (registry drift / superseded turns). Absent echo = legacy
    // behavior; the echo itself is never an authorization.
    const body = { toolCallId, name, arguments: args, turnToken };
    if (options?.toolFingerprint) {
      body.toolFingerprint = String(options.toolFingerprint);
    }
    const response = await fetch(`${WEBGIS_API_BASE}/pi-tools/execute`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Pi-Bridge-Secret": BRIDGE_SECRET,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
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
      } catch (error) {
        if (controller.signal.aborted) throw error;
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
  } finally {
    clearTimeout(timeoutId);
    cleanupSignal?.();
  }
}

/**
 * @param {import("@earendil-works/pi-coding-agent").ExtensionAPI} pi
 */
export default function webgisToolsExtension(pi) {
  // ADR-0103: the dump is the v2/v3 dynamic surface — every model-visible,
  // non-tier-3 registry tool is REGISTERED here; only the active subset
  // (default-active, then per-turn WEBGIS_ACTIVE_TOOLS) reaches the model.
  // ToolRegistry on the Python side stays the single execution truth.
  // v3 (ADR-0224): each dumped tool may carry a `spec` object; its
  // `fingerprint` is echoed on callbacks for stale-surface detection. The
  // dump-level surface_fingerprint is deliberately NOT echoed — the backend
  // verifies against the per-turn SurfaceReceipt (selected subset), which is
  // a different fingerprint by construction.
  const surface = loadNativeTools();
  const nativeTools = surface.tools;
  const toolFingerprints = new Map(
    nativeTools
      .filter((tool) => tool?.spec?.fingerprint)
      .map((tool) => [tool.name, tool.spec.fingerprint]),
  );
  // Wrap-reject only names actually registered. FALLBACK_NATIVE is prompt
  // vocabulary; if the schema dump missed, wrapping still reaches Python.
  const nativeNames = new Set(nativeTools.map((tool) => tool.name).filter(Boolean));
  // webgis_execute is registered below and always part of the active surface.
  // ADR-0288：发现入口（tool_search/codemode）常驻激活 —— 它们不在 dump
  // 注册面（vendor 内置扩展注册），须经 alwaysActive 白名单进入声明面。
  const alwaysActive = new Set([
    ...FALLBACK_NATIVE,
    EXECUTE_PROXY_NAME,
    ...(NATIVE_DISCOVERY_ENABLED ? DISCOVERY_ENTRY_TOOLS : []),
  ]);

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
      // 发现入口占用常驻名额（≤2），不动 48 上限。
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

  // Default projection: the frozen native surface (Phase 1 compat). Deferred
  // past extension loading (see session_start handler below); re-applied
  // lazily on the first before_agent_start in case session_start never fires
  // in this vendor mode.
  let defaultProjectionApplied = false;
  const applyDefaultActiveTools = (why) => {
    if (defaultProjectionApplied) return;
    defaultProjectionApplied = true;
    applyActiveTools(surface.defaultActive, why);
  };

  for (const tool of nativeTools) {
    if (!tool?.name) continue;
    const toolFingerprint = toolFingerprints.get(tool.name);
    // dump v4（ADR-0288）：透传原生封套 —— exposure（前门 direct / 长尾
    // deferred，deferred 可被上游 tool_search 发现并入 active 集）、
    // namespace（域分组，tool_search 的 namespace 权重）、annotations
    // （side_effect 派生的 MCP 语义提示；提示不是授权，执行仍回 Python 闸）。
    // 字段缺席（v2 dump / 旧投影）= 保持默认行为，零漂移。
    const registration = {
      name: tool.name,
      label: tool.label || tool.name,
      description: tool.description || tool.name,
      promptSnippet: tool.promptSnippet,
      parameters: tool.parameters || { type: "object", properties: {} },
      async execute(toolCallId, params, _signal, _onUpdate, ctx) {
        return postToBridge(toolCallId, tool.name, params || {}, currentTurnToken(ctx), {
          signal: _signal,
          toolFingerprint,
        });
      },
    };
    if (tool.exposure) registration.exposure = tool.exposure;
    if (tool.namespace && typeof tool.namespace === "object") {
      registration.namespace = tool.namespace;
    }
    if (tool.annotations && typeof tool.annotations === "object") {
      registration.annotations = tool.annotations;
    }
    pi.registerTool(registration);
  }

  pi.registerTool({
    name: "webgis_execute",
    label: "WebGIS Tool Executor",
    description: [
      "Execute a GIS tool that is not registered on this process.",
      "Registered tools, including heatmap_data and the frozen native set, must be called directly. Never wrap a name that appears in your tool list.",
      "Distribution / density / heatmap (e.g. 成都市小学分布): first call webgis_map_intent with {query: the user text}. Then get_local_admin_boundary and query_local_poi (district + subtype). Then call heatmap_data or h3_binning directly. Then webgis_map_product.",
      "Restyle (color, legend position, title) uses webgis_component_update, not a new POI query.",
      "webgis_cartography_status is a ZERO-argument read of the server cartography verdict AFTER the map has changed. Call it as webgis_cartography_status {}. It does not accept city, topic, scope, or query.",
    ].join("\n"),
    // 代理可派发读写混合的长尾 —— readOnlyHint 未知即缺席（保守语义）。
    exposure: "direct",
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
          description: "Name of a GIS tool that is not already registered (e.g. finalize_display). Do not pass heatmap_data or a native tool.",
        },
        arguments: {
          type: "object",
          description: "Tool-specific arguments as a JSON object. Registered tools are not valid here.",
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
      // ADR-0224: no per-tool fingerprint echo here by construction — wrapping
      // a dumped (registered) tool is client-side rejected above, and only
      // dumped tools carry fingerprints; proxy long-tail targets are outside
      // the fingerprint map, so the echo would always be undefined.
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
    // Pi v1.5.0 (≥0.99 two-phase loading): action methods like setActiveTools
    // cannot run while extensions load ("Extension runtime not initialized"),
    // so the default surface projection defers to session_start. Registration
    // above is unaffected (registerTool is load-safe); the per-turn projection
    // in before_agent_start keeps applying the marker-driven surface.
    pi.on("session_start", async () => {
      applyDefaultActiveTools("default(session_start)");
    });

    // ADR-0288（W09）：compaction 生命周期 —— 只记账披露，绝不改写 summary
    // （不可信数据不当指令；自定义摘要 = summary 获得事实话语权，拒绝）。
    // 压缩后的权威状态重建由后端 typed assembly 的 GIS 状态重建块
    // （GisStateRebuildProvider，MapSpec/SessionPlan 现取）承担 —— 隐藏层/
    // 待办/证据 refs 不随压缩丢失，也不凭 summary 授予修改权。
    pi.on("session_before_compact", async (event) => {
      console.log(`[webgis-tools] compaction starting (reason=${event?.reason ?? "?"})`);
      return {}; // 不注入自定义 summary —— 保持 vendor 默认压缩内容
    });
    pi.on("session_compact_failed", async (event) => {
      console.error(
        `[webgis-tools] compaction failed (fromHandler=${event?.fromBeforeCompactHandler ?? "?"})`,
      );
      return {};
    });

    pi.on("before_agent_start", async (event) => {
      const current = event.systemPrompt || "";
      let rewritten = `${GIS_IDENTITY}\n\n${current}`;
      for (const opening of CODING_ASSISTANT_OPENINGS) {
        if (current.includes(opening)) {
          rewritten = current.replace(opening, GIS_IDENTITY);
          break;
        }
      }

      // ADR-0103 Phase 3: apply this turn's projected tool surface before the
      // agent loop starts. Names outside the registered superset are ignored
      // (setActiveToolsByName semantics); the native front door stays active.
      const promptText = typeof event.prompt === "string" ? event.prompt : "";
      // Reset every turn: an absent/invalid suffix must not inherit a prior
      // turn's broader surface. Runtime actions still happen only after load.
      applyActiveTools(surface.defaultActive, "turn-default");
      defaultProjectionApplied = true;
      const suffix = routingSuffix(promptText);
      const match = suffix?.body.match(ACTIVE_TOOLS_SUFFIX_RE);
      if (match) {
        try {
          const requested = JSON.parse(match[1]);
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
