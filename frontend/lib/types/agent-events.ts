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
  /**
   * ADR-0052: 本 tool step 派生出的后台 durable job id。
   * 任务中心据此把后台 GIS job 挂到对应步骤下，用户不会看到两条互不相关的条目。
   */
  background_job_ids?: string[];
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
