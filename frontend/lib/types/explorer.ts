export type ExplorerStage =
  | "discover"
  | "fetch"
  | "parse"
  | "geocode"
  | "validate";

export type ExplorerStatus =
  | "idle"
  | "discovering"
  | "fetching"
  | "parsing"
  | "geocoding"
  | "validating"
  | "decision_required"
  | "completed"
  | "failed"
  | "aborted";

export interface ExplorerTask {
  taskId: string;
  status: ExplorerStatus;
  stage: ExplorerStage;
  progress: number;
  query: string;
  sourcesFound?: number;
  sourcesSelected?: string[];
  rowCount?: number;
  successRate?: number;
  resultRefId?: string;
  error?: string;
  startedAt: number;
  updatedAt: number;
}

export interface ExplorerEvent {
  stage: ExplorerStage;
  task_id: string;
  status: "started" | "progress" | "decision_point" | "completed" | "failed";
  context: Record<string, unknown>;
  available_actions?: string[];
  recommended_action?: string;
  requires_intervention?: boolean;
  confidence?: number;
}
