import { createServer, type RequestListener, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { request, type APIRequestContext } from 'playwright';
import type { Page } from 'playwright/test';
import { afterEach, describe, expect, it, vi } from 'vitest';

// Only the browser locator matcher is irrelevant to these bootstrap tests.
// APIRequestContext below is real, so relative URLs resolve exactly as they
// do in the journey runner rather than being accepted by a mocked post().
vi.mock('playwright/test', () => ({ expect: vi.fn() }));

const servers: Server[] = [];
const contexts: APIRequestContext[] = [];

async function serve(handler: RequestListener): Promise<string> {
  const server = createServer(handler);
  servers.push(server);
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
}

async function frontendRequest(baseURL: string): Promise<APIRequestContext> {
  const context = await request.newContext({ baseURL });
  contexts.push(context);
  return context;
}

afterEach(async () => {
  await Promise.all(contexts.splice(0).map((context) => context.dispose()));
  await Promise.all(servers.splice(0).map((server) => new Promise<void>((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  })));
  vi.unstubAllEnvs();
  vi.resetModules();
  vi.clearAllMocks();
});

describe('journey authentication bootstrap', () => {
  it('logs in at E2E_API_URL even when the request context is based at the frontend', async () => {
    let frontendHits = 0;
    const frontend = await serve((_req, res) => {
      frontendHits += 1;
      res.writeHead(404).end();
    });
    const received: { method?: string; path?: string; body: string }[] = [];
    const authUser = { id: 'persisted-admin', username: 'e2e-admin', role: 'admin' };
    const backend = await serve((req, res) => {
      let body = '';
      req.on('data', (chunk) => { body += chunk; });
      req.on('end', () => {
        received.push({ method: req.method, path: req.url, body });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ access_token: 'issued-by-backend', user: authUser }));
      });
    });
    vi.stubEnv('E2E_API_URL', `${backend}/`);
    vi.stubEnv('E2E_USER', 'e2e-admin');
    vi.stubEnv('E2E_PASS', 'journey-admin-pass-2026');
    const { loginViaApi } = await import('./bootstrap');
    const addInitScript = vi.fn();
    const accessToken = await loginViaApi({ request: await frontendRequest(frontend), addInitScript } as unknown as Page);

    expect(accessToken).toBe('issued-by-backend');
    expect(frontendHits).toBe(0);
    expect(received).toHaveLength(1);
    expect(received[0].method).toBe('POST');
    expect(received[0].path).toBe('/api/v1/auth/login');
    expect(JSON.parse(received[0].body)).toEqual({
      identifier: 'e2e-admin', password: 'journey-admin-pass-2026',
    });
    expect(addInitScript).toHaveBeenCalledOnce();
    const [seed, payload] = addInitScript.mock.calls[0];
    seed(payload);
    expect(localStorage.setItem).toHaveBeenCalledWith('webgis_auth', JSON.stringify({
      accessToken: 'issued-by-backend', user: authUser,
    }));
  });

  it('does not seed authentication when the backend rejects the credentials', async () => {
    const backend = await serve((_req, res) => { res.writeHead(401).end(); });
    vi.stubEnv('E2E_API_URL', backend);
    vi.stubEnv('E2E_USER', 'e2e-admin');
    vi.stubEnv('E2E_PASS', 'wrong-password');
    const { loginViaApi } = await import('./bootstrap');
    const addInitScript = vi.fn();
    await expect(loginViaApi({
      request: await frontendRequest(backend), addInitScript,
    } as unknown as Page)).rejects.toThrow('real-mode login failed: 401');
    expect(addInitScript).not.toHaveBeenCalled();
  });

  it('rejects missing real-mode credentials before making a request', async () => {
    vi.stubEnv('E2E_USER', '');
    vi.stubEnv('E2E_PASS', '');
    const { loginViaApi } = await import('./bootstrap');
    const post = vi.fn();
    await expect(loginViaApi({ request: { post } } as unknown as Page))
      .rejects.toThrow('real-mode journeys require E2E_USER/E2E_PASS');
    expect(post).not.toHaveBeenCalled();
  });

  it('keeps mock bootstrap local to the page without contacting the backend', async () => {
    const { bootstrapMock, defaultWorld } = await import('./bootstrap');
    const post = vi.fn();
    const page = { addInitScript: vi.fn(), route: vi.fn(), request: { post } };
    await bootstrapMock(page as unknown as Page, defaultWorld());
    expect(page.addInitScript).toHaveBeenCalledOnce();
    expect(page.route).toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });
});
