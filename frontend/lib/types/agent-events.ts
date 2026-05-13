// [DX3] Re-export SSE core types — callers import from here, not from lib/api/chat
export type { SSEEvent, SSEEventType } from '@/lib/api/chat';

export interface StepResultEvent {
  result?: {
    command?: string;
    params?: Record<string, unknown>;
    bbox?: [number, number, number, number];
    image?: string;
  };
  bbox?: [number, number, number, number];
  geojson_ref?: string;
  tool?: string;
  name?: string;
}

export interface TokenEvent {
  content: string;
}

export interface StepErrorEvent {
  error: string;
  tool?: string;
  step_id?: string;
}

export interface TaskCompleteEvent {
  summary?: string;
  step_count?: number;
  task_id?: string;
}
