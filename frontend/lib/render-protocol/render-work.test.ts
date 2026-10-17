/**
 * RenderWorkEnvelope 前端 store 测试（Goal 03，ADR-0224）。
 *
 * 锁定：防御式解析（坏形状拒收）、代次门（旧代不覆盖新代；同代幂等）、
 * 会话隔离（跨会话拒收/查询隔离）、workEchoStatus 语义。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  getLatestRenderWork,
  ingestRenderWork,
  parseRenderWorkEnvelope,
  resetRenderWorkStore,
  workEchoStatus,
  type RenderWorkEnvelope,
} from './render-work';

function envelope(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: 'render_work.v1',
    work_id: 'rw-5-fpabc',
    session_id: 'sess-A',
    mapspec_revision: 5,
    mapspec_fingerprint: 'fp-abc',
    observation_target_seq: 12,
    data_dependencies: [
      { source_id: 's1', kind: 'geojson', revision: '3', fingerprint: 'd1' },
    ],
    layer_work: [
      { layer_id: 'l1', layer_type: 'circle', source_id: 's1', visible: true },
    ],
    component_work: [],
    invalidation: { data_changed: [], layers_added: [], layers_removed: [], components_changed: [] },
    required_capabilities: ['circle', 'geojson'],
    diagnostics: [],
    work_fingerprint: 'wf1',
    ...over,
  };
}

beforeEach(() => resetRenderWorkStore());
afterEach(() => resetRenderWorkStore());

describe('parseRenderWorkEnvelope', () => {
  it('parses a well-formed envelope', () => {
    const parsed = parseRenderWorkEnvelope(envelope());
    expect(parsed?.work_id).toBe('rw-5-fpabc');
    expect(parsed?.data_dependencies[0].revision).toBe('3');
  });

  it('rejects wrong schema_version / missing identity', () => {
    expect(parseRenderWorkEnvelope(envelope({ schema_version: 'render_work.v2' }))).toBeNull();
    expect(parseRenderWorkEnvelope(envelope({ work_id: '' }))).toBeNull();
    expect(parseRenderWorkEnvelope(envelope({ work_fingerprint: null }))).toBeNull();
    expect(parseRenderWorkEnvelope(42)).toBeNull();
    expect(parseRenderWorkEnvelope(null)).toBeNull();
  });

  it('rejects non-numeric revision', () => {
    expect(parseRenderWorkEnvelope(envelope({ mapspec_revision: 'five' }))).toBeNull();
  });

  it('bounds arrays and unknown kinds degrade honestly', () => {
    const parsed = parseRenderWorkEnvelope(envelope({
      data_dependencies: [
        { source_id: 'a', kind: 'wormhole', revision: '1' },
        ...Array.from({ length: 100 }, (_, i) => ({ source_id: `s${i}`, kind: 'geojson' })),
      ],
    }));
    expect(parsed?.data_dependencies.length).toBeLessThanOrEqual(64);
    expect(parsed?.data_dependencies[0].kind).toBe('unknown');
  });
});

describe('ingestRenderWork generation gate', () => {
  it('accepts and stores same-session envelope', () => {
    expect(ingestRenderWork(envelope(), 'sess-A')).toBe(true);
    expect(getLatestRenderWork('sess-A')?.work_id).toBe('rw-5-fpabc');
  });

  it('rejects cross-session envelope', () => {
    expect(ingestRenderWork(envelope(), 'sess-B')).toBe(false);
    expect(getLatestRenderWork('sess-A')).toBeNull();
  });

  it('rejects malformed payload without touching state', () => {
    ingestRenderWork(envelope(), 'sess-A');
    expect(ingestRenderWork({ garbage: true }, 'sess-A')).toBe(false);
    expect(getLatestRenderWork('sess-A')?.mapspec_revision).toBe(5);
  });

  it('older generation never overwrites newer', () => {
    ingestRenderWork(envelope({ mapspec_revision: 7, work_id: 'rw-7' }), 'sess-A');
    expect(ingestRenderWork(envelope({ mapspec_revision: 6, work_id: 'rw-6' }), 'sess-A')).toBe(false);
    expect(getLatestRenderWork('sess-A')?.work_id).toBe('rw-7');
  });

  it('same-generation replay is idempotent accept', () => {
    expect(ingestRenderWork(envelope(), 'sess-A')).toBe(true);
    expect(ingestRenderWork(envelope(), 'sess-A')).toBe(true);
    expect(getLatestRenderWork('sess-A')?.work_id).toBe('rw-5-fpabc');
  });
});

describe('workEchoStatus', () => {
  it('absent / matched / stale', () => {
    expect(workEchoStatus(null, 5)).toBe('absent');
    const env = parseRenderWorkEnvelope(envelope());
    expect(workEchoStatus(env as RenderWorkEnvelope, 5)).toBe('matched');
    expect(workEchoStatus(env as RenderWorkEnvelope, 6)).toBe('stale');
  });
});
