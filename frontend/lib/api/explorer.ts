import { API_BASE } from "./config";
import { parseSSEStream } from "./sse-stream-parser";

export interface StartExploreRequest {
  query: string;
  session_id?: string;
  expected_data_type?: string;
  source_hint?: string[];
  auto_threshold?: number;
}

export async function startExploration(req: StartExploreRequest): Promise<{ task_id: string; status: string }> {
  const res = await fetch(`${API_BASE}/api/v1/explorer/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`Explorer start error: ${res.status}`);
  return res.json();
}

export async function getExplorerStatus(taskId: string): Promise<{
  task_id: string;
  status: string;
  progress: number;
  result: unknown;
}> {
  const res = await fetch(`${API_BASE}/api/v1/explorer/status/${taskId}`);
  if (!res.ok) throw new Error(`Explorer status error: ${res.status}`);
  return res.json();
}

export async function abortExploration(taskId: string): Promise<{ task_id: string; aborted: boolean }> {
  const res = await fetch(`${API_BASE}/api/v1/explorer/abort/${taskId}`, { method: "POST" });
  if (!res.ok) throw new Error(`Explorer abort error: ${res.status}`);
  return res.json();
}

export async function* streamExplorerProgress(taskId: string, signal?: AbortSignal): AsyncGenerator<{
  event: string;
  data: Record<string, unknown>;
}> {
  const response = await fetch(`${API_BASE}/api/v1/explorer/stream/${taskId}`, { signal });
  if (!response.ok) throw new Error(`Explorer stream error: ${response.status}`);

  if (!response.body) throw new Error("No response body");

  // transport goal A-F-06: use the shared parser. The inline copy here had
  // diverged from chat.ts: it never flushed a final unterminated event (a
  // trailing progress event with no blank line was silently dropped) and on
  // abort it `break`ed without reader.cancel(), leaking the connection until
  // the server closed it. The shared parser flushes at EOF and cancels the
  // reader in a finally on abort/exception.
  for await (const ev of parseSSEStream(response.body, signal)) {
    const data =
      typeof ev.data === "string"
        ? { raw: ev.data }
        : (ev.data as Record<string, unknown>);
    yield { event: ev.event, data };
  }
}
