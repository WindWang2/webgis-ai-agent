import { describe, expect, it } from 'vitest';

import { parseModelStatus } from './useModelRuntimeStore';

describe('parseModelStatus (G37 model_status SSE payload)', () => {
  it('parses a full applied payload', () => {
    const p = parseModelStatus({
      session_id: 's1',
      turn_id: 't1',
      status: 'applied',
      ok: true,
      mode: 'explicit',
      requested: { provider: 'webgis', model: 'g37-b' },
      effective: { provider: 'webgis', model: 'g37-b' },
      thinking: { requested: 'high', effective: 'off', controllable: false },
      reason_code: null,
      reason_detail: null,
      catalog_generation: 'abc123',
    });
    expect(p).not.toBeNull();
    expect(p!.effective!.model).toBe('g37-b');
    expect(p!.thinking.controllable).toBe(false);
  });

  it('tolerates missing optional fields without throwing', () => {
    const p = parseModelStatus({ session_id: 's1', turn_id: 't2' });
    expect(p).not.toBeNull();
    expect(p!.requested).toBeNull();
    expect(p!.effective).toBeNull();
    expect(p!.ok).toBe(false);
  });

  it('rejects malformed payloads (null / no session id)', () => {
    expect(parseModelStatus(null)).toBeNull();
    expect(parseModelStatus('nope')).toBeNull();
    expect(parseModelStatus({ turn_id: 't3' })).toBeNull();
  });
});
