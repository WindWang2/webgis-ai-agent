import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as jobs from './skill-jobs';

const fetchMock = vi.fn();
const payload = { session_id: 'session/a', skill_id: 'skill', params: { distance: 5 }, bindings: [] };
const preset = { name: 'preset', skill_id: 'skill', params: {}, bindings_template: {} };
type Context = { ownerToken?: string | null; signal?: AbortSignal };
type ClientCase = [string, (context: Context) => Promise<unknown>, string, string, unknown?];
const cases: ClientCase[] = [
  ['catalog', (ctx) => jobs.getCatalog(ctx), 'GET', '/catalog'],
  ['prepare', (ctx) => jobs.prepareSkillJob(payload, ctx), 'POST', '/prepare', payload],
  ['start', (ctx) => jobs.startSkillJob({ prepared_id: 'prepared', session_id: 'session/a' }, ctx), 'POST', '/start', { prepared_id: 'prepared', session_id: 'session/a' }],
  ['list runs', (ctx) => jobs.listSkillJobRuns('session/a', ctx), 'GET', '/runs?session_id=session%2Fa'],
  ['get run', (ctx) => jobs.getSkillJobRun('job', 'session/a', ctx), 'GET', '/runs/job?session_id=session%2Fa'],
  ['cancel', (ctx) => jobs.cancelSkillJobRun('job', 'session/a', ctx), 'POST', '/runs/job/cancel?session_id=session%2Fa'],
  ['list presets', (ctx) => jobs.listPresets(ctx), 'GET', '/presets'],
  ['create preset', (ctx) => jobs.createPreset(preset, ctx), 'POST', '/presets', preset],
  ['preset prepare', (ctx) => jobs.presetPrepare('preset/a', { session_id: 'session/a', bindings: [] }, ctx), 'POST', '/presets/preset%2Fa/prepare', { session_id: 'session/a', bindings: [] }],
];

describe('skill job request context (#2178)', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset().mockResolvedValue({ ok: true, status: 200, text: async () => '{}' });
  });
  it.each(cases)('%s forwards owner, path, body and caller cancellation', async (_name, invoke, method, path, body) => {
    const controller = new AbortController();
    await invoke({ ownerToken: 'owner-test', signal: controller.signal });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url);
    expect(parsed.pathname + parsed.search).toBe('/api/v1/skill-jobs' + path);
    expect(init.method).toBe(method);
    expect(new Headers(init.headers).get('X-Session-Token')).toBe('owner-test');
    expect(init.body).toBe(body === undefined ? undefined : JSON.stringify(body));
    // transport composes the caller signal with its timeout signal.
    expect(init.signal?.aborted).toBe(false);
  });
  it.each(cases)('%s aborts an in-flight fetch without converting it to an API error', async (_name, invoke) => {
    const controller = new AbortController();
    fetchMock.mockImplementation((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }));
    const pending = invoke({ ownerToken: 'owner-test', signal: controller.signal });
    controller.abort();
    const signal = (fetchMock.mock.calls[0][1] as RequestInit).signal;
    if (!signal?.aborted) {
      // Settle the old implementation's pending request to release its timer.
      signal?.dispatchEvent(new Event('abort'));
    }
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(signal?.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('does not invent an owner token for authenticated-user sessions', async () => {
    await jobs.listSkillJobRuns('session', { ownerToken: null });
    expect(new Headers(fetchMock.mock.calls[0][1].headers).has('X-Session-Token')).toBe(false);
  });
  it('preserves typed domain errors', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 409, statusText: 'Conflict', text: async () => JSON.stringify({ detail: { code: 'STALE', message: 'Prepare again', detail: { field: 'revision' } } }) });
    await expect(jobs.prepareSkillJob(payload, { ownerToken: 'owner-test' })).rejects.toMatchObject({ code: 'STALE', message: 'Prepare again', detail: { field: 'revision' } });
  });
});
