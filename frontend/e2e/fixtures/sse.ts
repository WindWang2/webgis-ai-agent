/**
 * SSE replay builders for journey fixtures.
 *
 * The event vocabulary mirrors the production chat stream contract parsed by
 * frontend/lib/api/chat.ts (session / task_start / token / tool_call /
 * step_result / task_complete / [DONE]) — the same shapes the visual harness
 * (test/visual/capture.mjs) replays, kept here journey-scoped and composable.
 */

export function sse(name: string, payload: unknown): string {
  return `event: ${name}\ndata: ${JSON.stringify(payload)}\n\n`;
}

export function toolCall(name: string, args: Record<string, unknown>): string {
  return sse('tool_call', { name, arguments: JSON.stringify(args) });
}

/** Deterministic analysis turn: one completed vector analysis with a bound, visible layer. */
export function analysisTurn(opts: {
  sessionId?: string;
  taskId?: string;
  ref?: string;
  layerName?: string;
  summary?: string;
}): string {
  const sessionId = opts.sessionId ?? 's-j1';
  const taskId = opts.taskId ?? 't-j1';
  const ref = opts.ref ?? 'ref:j1-hotspot';
  return [
    sse('session', { session_id: sessionId }),
    sse('task_start', { task_id: taskId, session_id: sessionId }),
    sse('token', { content: opts.summary ?? '分析完成，结果已挂载为图层。' }),
    toolCall('hotspot_analysis', { geojson: ref, value_field: 'value', distance_band: 1000 }),
    sse('step_result', {
      task_id: taskId,
      step_id: 'sr-j1',
      tool: 'hotspot_analysis',
      geojson_ref: ref,
      // Attached by the backend execution_engine in production: drives the
      // frontend MVT-vs-GeoJSON data-plane decision (use-sse-stream.ts:723).
      // feature_count > VECTOR_TILE_THRESHOLD (5000) + mvt_capable → tiles.
      ref_descriptor: {
        ref_id: ref,
        feature_count: 8421,
        geometry_types: ['Point'],
        bbox: [116.2814, 39.7842, 116.7351, 40.1213],
        mvt_capable: true,
        raster_capable: false,
        estimated_bytes: 482304,
        content_revision: 1,
      },
      result: {
        success: true,
        summary: opts.summary ?? '已完成热点分析，结果已挂载为图层。',
        bbox: [116.2814, 39.7842, 116.7351, 40.1213],
        data: { hot_spots_count: 5, cold_spots_count: 2, distance_band_m: 1000 },
        legend_spec: {
          type: 'graduated',
          field: 'gi_bin',
          breaks: [0, 3, 6, 9],
          palette: 'RdYlBu',
          palette_colors: ['#2166ac', '#f7f7f7', '#b2182b'],
        },
        runtime_patch: { visible: true },
      },
    }),
    sse('token', { content: '结果已收入结果工作台。' }),
    sse('task_complete', { task_id: taskId }),
    'data: [DONE]\n\n',
  ].join('');
}

/**
 * Long-running turn used by the cancel/retry journey: task_start only, the
 * stream never completes — the task sits "running" until the journey cancels.
 * `held` lets the fixture hold the stream open (chunked) for a realistic
 * cancel window; closing it immediately is fine too, the UI keys off
 * task_complete absence.
 */
export function hangingTurn(opts: { sessionId?: string; taskId?: string } = {}): string {
  const sessionId = opts.sessionId ?? 's-j2';
  const taskId = opts.taskId ?? 't-j2';
  return [
    sse('session', { session_id: sessionId }),
    sse('task_start', { task_id: taskId, session_id: sessionId }),
    sse('token', { content: '长时间分析进行中…' }),
  ].join('');
}

/** Turn replayed after retry: completes successfully. */
export function completingTurn(opts: { sessionId?: string; taskId?: string } = {}): string {
  return analysisTurn(opts);
}

/** Failed turn (validation error with a correction hint) for the retry journey. */
export function failingTurn(opts: { sessionId?: string; taskId?: string } = {}): string {
  const sessionId = opts.sessionId ?? 's-j2f';
  const taskId = opts.taskId ?? 't-j2f';
  return [
    sse('session', { session_id: sessionId }),
    sse('task_start', { task_id: taskId, session_id: sessionId }),
    toolCall('st_dbscan', { geojson: 'ref:j2', eps: 300, min_samples: 8 }),
    sse('step_result', {
      task_id: taskId,
      step_id: 'sr-j2f',
      tool: 'st_dbscan',
      result: {
        success: false,
        error_type: 'VALIDATION_ERROR',
        summary: '时间字段解析失败。',
        correction_hint: '请提供 ISO 8601 格式的时间字段。',
      },
    }),
    sse('task_complete', { task_id: taskId }),
    'data: [DONE]\n\n',
  ].join('');
}

/**
 * SSE 首事件断言原语：resolve 到流中第一个 `event:` 行的事件名。
 * 供旅程在真实/桩两种模式下断言「首事件语义」（会话先于任务、token 先于完成）。
 */
export function firstEventName(body: string): string {
  const m = /^event:\s*(.+)$/m.exec(body);
  if (!m) throw new Error('SSE body carries no event line');
  return m[1].trim();
}
