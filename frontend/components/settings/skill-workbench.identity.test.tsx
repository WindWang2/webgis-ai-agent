import React, { StrictMode } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAuth, setAuth } from '@/lib/auth/tokenStore';
import { setSessionIdentity } from '@/lib/store/session-identity';
import { SkillWorkbench } from './skill-workbench';

const catalog = { skills: [{ asset_type: 'gis_skill', id: 'review_skill', name: 'Review skill', description: 'Review', version: '1', runnability: 'runnable', parameters: [], role_bindings: [{ role: 'subject', required: true, geometry_kinds: ['point'] }] }], md_guides: [], extension_candidates: [] };
const prepared = { prepared_id: 'prepared-private-a', preview: { skill: { digest: 'private-a' }, steps: [], evidence_covered: [], evidence_not_covered: [] } };
const preset = { id: 'private-a', name: 'OLD_PRIVATE_PRESET', skill_id: 'review_skill', skill_version: '1', params: {}, bindings_template: { subject: { last_ref: 'ref:private-a', mode: 'ref' } } };
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
const fetchMock = vi.fn();
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function login(id: string, accessToken = `access-${id}`) {
  setAuth({ accessToken, refreshToken: null }, { id, username: id });
}
function route(url: string, init?: RequestInit): Promise<Response> {
  if (url.includes('/catalog')) return Promise.resolve(json(catalog));
  if (url.endsWith('/presets')) return Promise.resolve(json(init?.method === 'POST' ? preset : { presets: [preset] }));
  if (url.endsWith('/prepare')) return Promise.resolve(json(prepared));
  if (url.endsWith('/start')) return Promise.resolve(json({ job_id: '9', status: 'running', active: true, cancellable: true }));
  if (url.includes('/cancel')) return Promise.resolve(json({ cancel_requested: true }));
  return Promise.resolve(json({ runs: [] }));
}
async function configure() {
  await userEvent.click(await screen.findByTestId('skill-review_skill'));
  await userEvent.type(screen.getByTestId('binding-ref-subject'), 'ref:private-a');
}
async function preview() {
  await configure();
  await userEvent.click(screen.getByTestId('prepare-btn'));
  await screen.findByTestId('preview-panel');
}
function urls() { return fetchMock.mock.calls.map(([url]) => String(url)); }

beforeEach(() => {
  login('user-a');
  setSessionIdentity('session-a', 'owner-a');
  fetchMock.mockReset().mockImplementation(route);
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  cleanup();
  clearAuth();
  setSessionIdentity(undefined, null);
  vi.unstubAllGlobals();
});

describe('SkillWorkbench identity lifetime with real stores', () => {
  it.each(['session', 'owner-token', 'principal', 'logout'] as const)(
    'clears private presets/form immediately on %s change even if reload fails', async (change) => {
      const next = deferred<Response>();
      let switched = false;
      fetchMock.mockImplementation((url: string, init?: RequestInit) => switched && url.endsWith('/presets') ? next.promise : route(url, init));
      const view = render(<SkillWorkbench />);
      await configure();
      expect(screen.getByText('OLD_PRIVATE_PRESET')).toBeInTheDocument();
      const name = view.container.querySelector('details input') as HTMLInputElement;
      fireEvent.change(name, { target: { value: 'PRIVATE_DRAFT_NAME' } });
      await act(async () => {
        switched = true;
        if (change === 'session') setSessionIdentity('session-b', 'owner-a');
        if (change === 'owner-token') setSessionIdentity('session-a', 'owner-b');
        if (change === 'principal') login('user-b');
        if (change === 'logout') clearAuth();
      });
      await userEvent.click(await screen.findByTestId('skill-review_skill'));
      expect(screen.queryByText('OLD_PRIVATE_PRESET')).not.toBeInTheDocument();
      expect(view.container.querySelector('details input')).toHaveValue('');
      expect(screen.getByTestId('binding-ref-subject')).toHaveValue('');
      await act(async () => { next.reject(new TypeError('replacement list failed')); });
      expect(screen.queryByText('OLD_PRIVATE_PRESET')).not.toBeInTheDocument();
    },
  );

  it('accepts only the new principal list when the old list resolves later', async () => {
    const oldList = deferred<Response>();
    let request = 0;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith('/presets')) return ++request === 1 ? oldList.promise : Promise.resolve(json({ presets: [{ ...preset, id: 'new', name: 'NEW_USER_PRESET' }] }));
      return route(url, init);
    });
    render(<SkillWorkbench />);
    await screen.findByTestId('skill-review_skill');
    await act(async () => login('user-b'));
    await configure();
    await screen.findByText('NEW_USER_PRESET');
    await act(async () => { oldList.resolve(json({ presets: [preset] })); });
    expect(screen.queryByText('OLD_PRIVATE_PRESET')).not.toBeInTheDocument();
    expect(screen.getByText('NEW_USER_PRESET')).toBeInTheDocument();
    const latest = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/presets')).at(-1)!;
    expect(new Headers(latest[1].headers).get('Authorization')).toBe('Bearer access-user-b');
  });

  it.each(['prepare', 'save', 'reuse', 'start', 'cancel'] as const)(
    'refuses a stale %s action in the same event as a principal switch', async (action) => {
      const view = render(<SkillWorkbench />);
      let button: HTMLElement;
      if (action === 'start' || action === 'cancel') {
        await preview();
        button = screen.getByTestId('start-btn');
        if (action === 'cancel') {
          await userEvent.click(button);
          const panel = await screen.findByTestId('run-panel');
          button = panel.querySelector('button')!;
        }
      } else {
        await configure();
        const details = view.container.querySelector('details')!;
        const buttons = details.querySelectorAll('button');
        if (action === 'save') fireEvent.change(details.querySelector('input')!, { target: { value: 'PRIVATE_DRAFT_NAME' } });
        button = action === 'prepare' ? screen.getByTestId('prepare-btn') : buttons[action === 'save' ? 0 : 1];
      }
      const before = fetchMock.mock.calls.length;
      await act(async () => { login('user-b'); fireEvent.click(button); });
      await screen.findByTestId('skill-review_skill');
      const writes = fetchMock.mock.calls.slice(before).filter(([, init]) => init?.method === 'POST');
      expect(writes).toHaveLength(0);
      expect(screen.queryByTestId('preview-panel')).not.toBeInTheDocument();
      expect(screen.queryByTestId('run-panel')).not.toBeInTheDocument();
      expect(screen.queryByTestId('binding-ref-subject')).not.toBeInTheDocument();
    },
  );

  it.each([
    ['prepare', 'success'], ['prepare', 'error'],
    ['start', 'success'], ['start', 'error'],
    ['save', 'success'], ['save', 'error'],
  ] as const)('ignores late %s %s after principal switch and unmount', async (operation, outcome) => {
    const pending = deferred<Response>();
    const endpoint = operation === 'save' ? '/presets' : `/${operation}`;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => url.endsWith(endpoint) && init?.method === 'POST' ? pending.promise : route(url, init));
    const old = render(<SkillWorkbench />);
    if (operation === 'start') {
      await preview();
      await userEvent.click(screen.getByTestId('start-btn'));
    } else {
      await configure();
      if (operation === 'prepare') await userEvent.click(screen.getByTestId('prepare-btn'));
      else {
        const details = old.container.querySelector('details')!;
        fireEvent.change(details.querySelector('input')!, { target: { value: 'PRIVATE_DRAFT_NAME' } });
        fireEvent.click(details.querySelector('button')!);
      }
    }
    const init = fetchMock.mock.calls.find(([url, options]) => String(url).endsWith(endpoint) && options?.method === 'POST')![1] as RequestInit;
    await act(async () => login('user-b'));
    expect(init.signal?.aborted).toBe(true);
    old.unmount();
    render(<SkillWorkbench />);
    await screen.findByTestId('skill-review_skill');
    const count = fetchMock.mock.calls.length;
    await act(async () => {
      if (outcome === 'success') pending.resolve(json(operation === 'prepare' ? prepared : operation === 'start' ? { job_id: '9', status: 'running', active: true } : preset));
      else pending.reject(new TypeError('late failure'));
    });
    expect(screen.queryByTestId('preview-panel')).not.toBeInTheDocument();
    expect(screen.queryByTestId('run-panel')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(count);
  });

  it('old prepare finally cannot clear the new principal busy state', async () => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    let count = 0;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => url.endsWith('/prepare') ? (++count === 1 ? first.promise : second.promise) : route(url, init));
    render(<SkillWorkbench />);
    await configure();
    await userEvent.click(screen.getByTestId('prepare-btn'));
    await act(async () => login('user-b'));
    await configure();
    await userEvent.click(screen.getByTestId('prepare-btn'));
    await act(async () => { first.resolve(json(prepared)); });
    expect(screen.getByTestId('prepare-btn')).toBeDisabled();
    expect(screen.queryByTestId('preview-panel')).not.toBeInTheDocument();
    await act(async () => { second.resolve(json({ ...prepared, preview: { ...prepared.preview, skill: { digest: 'new-user-b' } } })); });
    expect(screen.getByTestId('preview-panel')).toHaveTextContent('new-user-b');
  });

  it('observes real tokenStore cross-tab identity invalidation', async () => {
    const view = render(<SkillWorkbench />);
    await configure();
    const count = fetchMock.mock.calls.length;
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', { key: 'webgis_auth', newValue: JSON.stringify({ userId: 'user-b' }) }));
    });
    expect(screen.queryByTestId('configure-panel')).not.toBeInTheDocument();
    const next = fetchMock.mock.calls.slice(count);
    expect(next.length).toBeGreaterThan(0);
    for (const [, init] of next) expect(new Headers(init.headers).has('Authorization')).toBe(false);
    view.unmount();
  });

  it('discards an old polling result even when fetch ignores cancellation', async () => {
    const oldPoll = deferred<Response>();
    fetchMock.mockImplementation((url: string, init?: RequestInit) => url.includes('/runs/9?') ? oldPoll.promise : route(url, init));
    const view = render(<SkillWorkbench />);
    await preview();
    await userEvent.click(screen.getByTestId('start-btn'));
    await waitFor(() => expect(urls().some(url => url.includes('/runs/9?'))).toBe(true), { timeout: 4000 });
    const pollCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/runs/9?'))!;
    await act(async () => login('user-b'));
    expect(pollCall[1].signal.aborted).toBe(true);
    await act(async () => { oldPoll.resolve(json({ job_id: '9', status: 'completed', active: false, result_ref: 'PRIVATE_A_REPORT' })); });
    expect(screen.queryByTestId('run-panel')).not.toBeInTheDocument();
    expect(screen.queryByText('PRIVATE_A_REPORT')).not.toBeInTheDocument();
    view.unmount();
  });

  it('does not invalidate a prepared plan on a same-user access-token refresh', async () => {
    render(<SkillWorkbench />);
    await preview();
    const count = fetchMock.mock.calls.length;
    const signal = fetchMock.mock.calls[0][1].signal as AbortSignal;
    await act(async () => login('user-a', 'access-refreshed'));
    expect(signal.aborted).toBe(false);
    expect(screen.getByTestId('preview-panel')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(count);
  });

  it('never revives an old generation on A-to-B-to-A principal changes', async () => {
    const pending = deferred<Response>();
    fetchMock.mockImplementation((url: string, init?: RequestInit) => url.endsWith('/presets') ? pending.promise : route(url, init));
    render(<SkillWorkbench />);
    await preview();
    const oldButton = screen.getByTestId('start-btn');
    const signal = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/presets'))![1].signal as AbortSignal;
    await act(async () => { login('user-b'); login('user-a'); fireEvent.click(oldButton); });
    expect(signal.aborted).toBe(true);
    expect(screen.queryByTestId('preview-panel')).not.toBeInTheDocument();
    expect(urls().filter(url => url.endsWith('/start'))).toHaveLength(0);
  });

  it('keeps a live controller through StrictMode setup/cleanup and aborts it on unmount', async () => {
    const pending = deferred<Response>();
    fetchMock.mockImplementation((url: string, init?: RequestInit) => url.endsWith('/presets') ? pending.promise : route(url, init));
    const view = render(<StrictMode><SkillWorkbench /></StrictMode>);
    await screen.findByTestId('skill-review_skill');
    const current = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/presets')).at(-1)![1].signal as AbortSignal;
    expect(current.aborted).toBe(false);
    view.unmount();
    expect(current.aborted).toBe(true);
    const count = fetchMock.mock.calls.length;
    await act(async () => login('user-b'));
    expect(fetchMock).toHaveBeenCalledTimes(count);
  });
});
