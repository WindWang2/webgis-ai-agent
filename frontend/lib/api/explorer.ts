import { API_BASE } from "./config";

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

export async function* streamExplorerProgress(taskId: string): AsyncGenerator<{
  event: string;
  data: Record<string, unknown>;
}> {
  const response = await fetch(`${API_BASE}/api/v1/explorer/stream/${taskId}`);
  if (!response.ok) throw new Error(`Explorer stream error: ${response.status}`);

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "";
  let currentData = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("event: ")) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        currentData += line.slice(6);
      } else if (line === "" && currentEvent && currentData) {
        try {
          yield { event: currentEvent, data: JSON.parse(currentData) };
        } catch {
          yield { event: currentEvent, data: { raw: currentData } };
        }
        currentEvent = "";
        currentData = "";
      }
    }
  }
}
