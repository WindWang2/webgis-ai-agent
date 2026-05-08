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

// Spatial Reasoning Types
export interface ReasoningStep {
  step: number;
  fact: string;
  source: string;
}

export interface SpatialReasoningResult {
  type: "spatial_reasoning";
  conclusion: string;
  reasoning_chain: ReasoningStep[];
  confidence: number;
  uncertainty: string;
  recommendations: string[];
}

// What-if Simulation Types
export interface MetricDelta {
  baseline: number;
  simulated: number;
  delta_pct: number;
}

export interface WhatIfSimulationResult {
  type: "what_if_simulation";
  scenario: string;
  target_area: string;
  simulation_ref_id: string;
  impact_summary: {
    direct_area_km2: number;
    indirect_area_km2: number;
    scenario_type: string;
    affected_metrics: string[];
  };
  metrics: Record<string, MetricDelta>;
  uncertainty: string;
  rules_applied: string[];
  simulation_geojson: GeoJSON.FeatureCollection;
}

export type SimulationViewMode = "baseline" | "simulated" | "delta";
