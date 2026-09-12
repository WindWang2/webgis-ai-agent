/**
 * Deterministic OpenAI-compatible LLM stub for the nightly REAL journey lane
 * (quality-e2e-v9, ADR-0146).
 *
 * The journeys must be deterministic: the LLM boundary is scripted, everything
 * behind it (chat engine, tools, upload/SSE/export, DB, fabric) is the real
 * backend. Behavior is a call-count script:
 *
 *   call 1..n per "phase": tool_call hotspot_analysis with an inline
 *   FeatureCollection (real spatial analysis runs behind it), then a final
 *   plain-text answer. STUB_FAIL_AT=<n> makes call n return HTTP 500 (J2
 *   real: a failing turn to retry). STUB_DELAY_FIRST_TOKEN_MS=<ms> delays the
 *   first SSE byte (J2 real: a long-running turn to cancel).
 *
 * The repository tool schema arrives in the request; the stub answers with the
 * OpenAI wire format the backend's OpenAI-compatible client already parses.
 *
 * Usage: node e2e/tools/llm-stub.mjs   (port via STUB_PORT, default 8767)
 */
import http from 'node:http';

const PORT = Number(process.env.STUB_PORT ?? 8767);
const FAIL_AT = process.env.STUB_FAIL_AT ? Number(process.env.STUB_FAIL_AT) : -1;
const DELAY_MS = process.env.STUB_DELAY_FIRST_TOKEN_MS
  ? Number(process.env.STUB_DELAY_FIRST_TOKEN_MS)
  : 0;

let calls = 0;

const INLINE_FC = {
  type: 'FeatureCollection',
  features: Array.from({ length: 12 }, (_, i) => ({
    type: 'Feature',
    properties: { name: `p${i}`, value: (i * 7) % 23 },
    geometry: { type: 'Point', coordinates: [116.3 + (i % 4) * 0.05, 39.8 + Math.floor(i / 4) * 0.05] },
  })),
};

function toolCallChunk(id) {
  const args = JSON.stringify({
    geojson: INLINE_FC,
    value_field: 'value',
    distance_band: 5000,
  });
  return {
    id,
    object: 'chat.completion.chunk',
    created: 1,
    model: 'quality-e2e-stub',
    choices: [
      {
        index: 0,
        delta: {
          tool_calls: [
            {
              index: 0,
              id: `call_${id}`,
              type: 'function',
              function: { name: 'hotspot_analysis', arguments: args },
            },
          ],
        },
        finish_reason: null,
      },
    ],
  };
}

function sseEvent(obj) {
  return `data: ${JSON.stringify(obj)}\n\n`;
}

function streamResponse(res, body) {
  res.writeHead(200, {
    'content-type': 'text/event-stream',
    'cache-control': 'no-cache',
    connection: 'keep-alive',
  });
  const id = `chatcmpl-${calls}`;
  if (DELAY_MS > 0) {
    setTimeout(() => writeStream(res, id, body), DELAY_MS);
  } else {
    writeStream(res, id, body);
  }
}

function writeStream(res, id, body) {
  const wantsTool = body.messages && !body.messages.some((m) => m.role === 'tool');
  if (wantsTool) {
    res.write(sseEvent({
      id,
      object: 'chat.completion.chunk',
      created: 1,
      model: 'quality-e2e-stub',
      choices: [{ index: 0, delta: { role: 'assistant', content: '' }, finish_reason: null }],
    }));
    res.write(sseEvent(toolCallChunk(id)));
    res.write(sseEvent({
      id,
      object: 'chat.completion.chunk',
      created: 1,
      model: 'quality-e2e-stub',
      choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }],
    }));
  } else {
    res.write(sseEvent({
      id,
      object: 'chat.completion.chunk',
      created: 1,
      model: 'quality-e2e-stub',
      choices: [{
        index: 0,
        delta: { role: 'assistant', content: '热点分析已完成：12 个点位参与计算，结果已挂载为图层。' },
        finish_reason: null,
      }],
    }));
    res.write(sseEvent({
      id,
      object: 'chat.completion.chunk',
      created: 1,
      model: 'quality-e2e-stub',
      choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
    }));
  }
  res.write('data: [DONE]\n\n');
  res.end();
}

function blockingResponse(res, body) {
  const wantsTool = body.messages && !body.messages.some((m) => m.role === 'tool');
  const id = `chatcmpl-${calls}`;
  const payload = wantsTool
    ? {
        id,
        object: 'chat.completion',
        created: 1,
        model: 'quality-e2e-stub',
        choices: [{
          index: 0,
          message: {
            role: 'assistant',
            content: null,
            tool_calls: [{
              id: `call_${id}`,
              type: 'function',
              function: {
                name: 'hotspot_analysis',
                arguments: JSON.stringify({ geojson: INLINE_FC, value_field: 'value', distance_band: 5000 }),
              },
            }],
          },
          finish_reason: 'tool_calls',
        }],
      }
    : {
        id,
        object: 'chat.completion',
        created: 1,
        model: 'quality-e2e-stub',
        choices: [{
          index: 0,
          message: { role: 'assistant', content: '热点分析已完成，结果已挂载为图层。' },
          finish_reason: 'stop',
        }],
      };
  res.writeHead(200, { 'content-type': 'application/json' });
  res.end(JSON.stringify(payload));
}

const server = http.createServer((req, res) => {
  const chunks = [];
  req.on('data', (c) => chunks.push(c));
  req.on('end', () => {
    calls += 1;
    if (calls === FAIL_AT) {
      res.writeHead(500, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ error: { message: 'quality-e2e scripted failure' } }));
      return;
    }
    let body = {};
    try { body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}'); } catch { /* keep {} */ }
    if (body.stream) {
      streamResponse(res, body);
    } else {
      blockingResponse(res, body);
    }
  });
});

server.listen(PORT, () => {
  console.log(`[llm-stub] listening on :${PORT} (fail_at=${FAIL_AT}, delay=${DELAY_MS}ms)`);
});
