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


/** Agent 提交一份带可见层与图例的 MapSpec，并将 mutation_revision 从 0 推进到 `revision`。 */
export function mapspecCommitTurn(opts: {
  sessionId?: string;
  taskId?: string;
  revision?: number;
  layerName?: string;
} = {}): string {
  const sessionId = opts.sessionId ?? 's-j13';
  const taskId = opts.taskId ?? 't-j13';
  const revision = opts.revision ?? 2;
  const layerName = opts.layerName ?? '分析结果: 热点';
  const legendSpec = {
    type: 'graduated',
    field: 'gi_bin',
    breaks: [0, 3, 6, 9],
    palette: 'RdYlBu',
    palette_colors: ['#2166ac', '#f7f7f7', '#b2182b'],
  };
  const mapspec = {
    version: '1.1',
    sources: {
      'src-hotspot': {
        type: 'geojson',
        inlineData: { type: 'FeatureCollection', features: [] },
      },
    },
    layers: [
      {
        id: 'ly-hotspot',
        source: 'src-hotspot',
        type: 'circle',
        visible: true,
        legend_spec: legendSpec,
      },
    ],
    layout: {
      components: [
        { id: 'legend-main', type: 'legend', enabled: true },
      ],
    },
  };
  return [
    sse('session', { session_id: sessionId }),
    sse('task_start', { task_id: taskId, session_id: sessionId }),
    sse('token', { content: '已根据分析结果更新地图。' }),
    sse('step_result', {
      task_id: taskId,
      step_id: 'sr-j13',
      tool: 'hotspot_analysis',
      mutation_revision: revision,
      mapspec,
      result: {
        success: true,
        summary: '热点分析结果已写入 MapSpec。',
        legend_spec: legendSpec,
        runtime_patch: { visible: true, name: layerName },
        mapspec,
        mutation_revision: revision,
      },
    }),
    sse('task_complete', { task_id: taskId, mutation_revision: revision, mapspec }),
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

/**
 * G15 journey-16 夹具：Agent 对话提交携带 frame_product 的三画幅产品。
 *
 * committed spec 形状 = 服务端 bind 后的真实投影（layout.frames 身份登记 +
 * frame_product 契约 + 共享分级）；journey 断言 Studio 画幅面板恢复三幅。
 */
export function frameProductCommitTurn(opts: {
  sessionId?: string;
  taskId?: string;
  revision?: number;
} = {}): string {
  const sessionId = opts.sessionId ?? 's-j14';
  const taskId = opts.taskId ?? 't-j14';
  const revision = opts.revision ?? 3;
  const legendSpec = {
    type: 'graduated',
    field: 'per_10k',
    breaks: [20, 26, 32, 38],
    palette: 'Blues',
    palette_colors: ['#c6dbef', '#6baed6', '#2171b5'],
    labels: ['20 – 26', '26 – 32', '32 – 38'],
  };
  const frameSlot = (frameId: string, title: string) => ({
    frame_id: frameId,
    title,
    legend: 'shared',
    data: {
      ref: 'ref:indicators',
      layer_id: 'lyr-districts',
      field: 'per_10k',
      content_revision: 5,
      time_window: {
        frame_id: frameId,
        label: title,
        start: `${title}-01-01T00:00:00Z`,
        end: `${Number(title) + 1}-01-01T00:00:00Z`,
        time_field: 'year',
        time_representation: 'year_int',
        granularity: 'year',
        timezone: 'UTC',
        layer_id: 'lyr-districts',
      },
    },
  });
  const mapspec = {
    version: '2.0',
    sources: {
      'src-districts': {
        type: 'geojson',
        inlineData: { type: 'FeatureCollection', features: [] },
      },
    },
    layers: [
      { id: 'lyr-districts', source: 'src-districts', type: 'fill', visible: true,
        legend_spec: legendSpec },
    ],
    layout: {
      frames: [
        { id: 'fp-t-2020', title: '2020', extent: [103.9, 30.5, 104.3, 30.9] },
        { id: 'fp-t-2022', title: '2022', extent: [103.9, 30.5, 104.3, 30.9] },
        { id: 'fp-t-2024', title: '2024', extent: [103.9, 30.5, 104.3, 30.9] },
      ],
    },
    frame_product: {
      schema_version: 'frame_product.v1',
      enabled: true,
      product_kind: 'time_series',
      grid: { columns: 3, title: '成都每万人小学学位' },
      frames: [
        frameSlot('fp-t-2020', '2020'),
        frameSlot('fp-t-2022', '2022'),
        frameSlot('fp-t-2024', '2024'),
      ],
      shared_expression: {
        field: 'per_10k', semantics: 'other', method: 'equal_interval',
        k: 3, palette: 'Blues', breaks: [20, 26, 32, 38],
        domain_status: 'complete',
        per_frame_counts: { 'fp-t-2020': 3, 'fp-t-2022': 3, 'fp-t-2024': 3 },
        sample_size: 9, eligibility: ['union_breaks'],
      },
      active_frame: 'fp-t-2020',
    },
  };
  return [
    sse('session', { session_id: sessionId }),
    sse('task_start', { task_id: taskId, session_id: sessionId }),
    sse('token', { content: '已创建三年并排小倍数产品。' }),
    sse('step_result', {
      task_id: taskId,
      step_id: 'sr-j14',
      tool: 'frame_product_bind',
      mutation_revision: revision,
      mapspec,
      result: { success: true, status: 'ok', frames: 3, mapspec, mutation_revision: revision },
    }),
    sse('task_complete', { task_id: taskId, mutation_revision: revision, mapspec }),
    'data: [DONE]\n\n',
  ].join('');
}
