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
// 500 必须映射：dispatch 路径（registry 未注入的启动窗口等）真实返回的是
// 500 而非 503 —— 只映射 503 会留一个无指引的盲重试洞（red team）。
const STATUS_GUIDANCE = {
  401: {
    error: "turn_context_rejected",
    text: "The WebGIS backend rejected the turn context as invalid or expired. Do not retry tool calls; finish the reply and tell the user to send a new message to re-establish the map session.",
  },
  409: {
    error: "turn_not_active",
    text: "This turn is no longer active (it completed, was aborted, or was superseded by a newer message). Retrying will keep failing — stop calling GIS tools and summarize the results you already have for the user.",
  },
  500: {
    error: "bridge_server_error",
    text: "The WebGIS backend failed to dispatch this call (it may be starting up or transiently broken). Retry the same call once after a few seconds; if it fails again, report the error to the user instead of retrying further.",
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
// 之后所有回调都 401 missing_turn_context。记忆按 sessionManager 存
// {index, token, scanned}：
// - scanned 高水位线以上的 entry 已扫过且无新 marker（追加型会话），每次
//   只扫新 entry —— 每次调用 O(新 entry 数) + 钉住点一次校验，整个回合
//   不再是 O(n²)（red team P1）。
// - user-role 消息 entry 里的 marker 是权威来源；可识别的非 user 形状
//   （assistant/toolResult/custom 消息）里的同形 marker 一律不采信 ——
//   工具结果回显攻击文本不能遮蔽真实 token（red team S1）。未知形状
//   （字符串等非 Pi entry 对象）保留旧语义（最新 marker 生效）以兼容
//   独立运行/测试注入。
// - 钉住点越界或不再携带钉住 token（会话重置、压缩重写）时作废进度、
//   全量重扫；重扫无果时沿用被作废的 token（carry）而非本地返回空串 ——
//   压缩恰好抹掉 marker entry 时，服务端签名 + 活跃 turn 校验（401/409
//   带指引）仍是最终裁决者，本地空串死角会让整个回合的 GIS 面静默死亡
//   （red team RT4）。校验是子串级的：重写后原样引用旧 marker 的病态
//   entry 仍会沿用旧 token，同样交给服务端裁决。
const TURN_TOKEN_MEMO = new WeakMap();

function entryStillHasToken(entry, token) {
  try {
    return typeof entry !== "undefined" && entry !== null
      && JSON.stringify(entry).includes(`WEBGIS_TURN_CONTEXT:${token}`);
  } catch {
    return false;
  }
}

// Pi 的真实 entry 是 {type:"message", message:{role:"user"|...}}；只有
// user 消息 entry 能被后端附加 marker（attach_turn_context 只装饰用户
// 消息），也只有它铸造的 marker 可信。
function isUserMessageEntry(entry) {
  return entry !== null && typeof entry === "object"
    && entry.type === "message"
    && entry.message !== null && typeof entry.message === "object"
    && entry.message.role === "user";
}

// 可识别的 Pi 消息 entry 但非 user：其中的 marker 一律视为回显，不采信。
function isKnownNonUserMessageEntry(entry) {
  return entry !== null && typeof entry === "object"
    && entry.type === "message"
    && entry.message !== null && typeof entry.message === "object"
    && entry.message.role !== "user";
}

function lastMarkerIn(serialized) {
  const matches = Array.from(serialized.matchAll(TURN_CONTEXT_RE));
  // 取最后一个匹配：后端 marker 排在不可信用户文本之后，用户伪造的同形
  // marker 不能遮蔽服务器能力。
  return matches.length ? matches[matches.length - 1][1] : "";
}

export function currentTurnToken(ctx) {
  const manager = ctx?.sessionManager;
  if (!manager) return "";
  const entries = manager.getEntries?.() ?? [];
  const memo = TURN_TOKEN_MEMO.get(manager);
  // index < 0 是 carry-only 记忆（marker entry 已被压缩抹掉，token 无锚点）。
  const pinValid = !!memo
    && (memo.index < 0
      ? entries.length >= memo.scanned
      : memo.index < entries.length && entryStillHasToken(entries[memo.index], memo.token));
  if (memo && !pinValid) {
    TURN_TOKEN_MEMO.delete(manager);
  }
  // 只扫高水位以上的新 entry；钉住点之下（已扫过、无新 marker）不重扫。
  const startFrom = memo
    ? Math.max(pinValid ? memo.scanned : 0, pinValid ? memo.index + 1 : 0)
    : 0;
  let legacyAnswer = "";
  for (let index = entries.length - 1; index >= startFrom; index -= 1) {
    let serialized = "";
    try {
      serialized = JSON.stringify(entries[index]);
    } catch {
      continue;
    }
    const marker = lastMarkerIn(serialized);
    if (!marker) continue;
    if (isUserMessageEntry(entries[index])) {
      TURN_TOKEN_MEMO.set(manager, { index, token: marker, scanned: entries.length });
      return marker;
    }
    if (!isKnownNonUserMessageEntry(entries[index]) && !legacyAnswer) {
      // 未知形状（字符串/非 Pi entry 对象）：兼容旧语义，先记住再继续找
      // 更权威的 user-role marker。
      legacyAnswer = marker;
    }
  }
  const scanned = entries.length;
  // 锚定 pin（index>=0，user-role marker 铸造）不受后续非 user marker 影响；
  // carry/legacy 记忆（index<0）保持旧语义：新 marker（含未知形状）取代之。
  if (legacyAnswer && (!pinValid || memo.index < 0)) {
    TURN_TOKEN_MEMO.set(manager, { index: -1, token: legacyAnswer, scanned });
    return legacyAnswer;
  }
  if (pinValid) {
    memo.scanned = scanned;
    return memo.token;
  }
  if (memo) {
    // 钉住点被重写/压缩抹掉且重扫无果：carry 旧 token，服务端裁决。
    TURN_TOKEN_MEMO.set(manager, { index: -1, token: memo.token, scanned });
    return memo.token;
  }
  return "";
}

export async function postToBridge(toolCallId, name, args, turnToken) {
  if (!turnToken) {
    return {
      content: [{
        type: "text",
        text: "WebGIS tool execution rejected: no turn context marker exists in this session (it was compacted away before any tool ran, or the session was reset). Do not retry; tell the user to send a new message to start a fresh turn.",
      }],
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
