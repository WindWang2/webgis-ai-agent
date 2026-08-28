/** Agent host for the current chat turn. */
export type AgentRuntime = "pi" | "chatengine";

export function parseAgentRuntime(value: unknown): AgentRuntime | null {
  if (value === "pi" || value === "chatengine") return value;
  return null;
}

export function agentRuntimeLabel(runtime: AgentRuntime): string {
  return runtime === "pi" ? "powered by Pi" : "powered by ChatEngine";
}
