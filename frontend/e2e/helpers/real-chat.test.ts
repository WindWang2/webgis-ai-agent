import { createServer, type RequestListener, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { request, type APIRequestContext } from 'playwright';
import { afterEach, describe, expect, it, vi } from 'vitest';

const servers: Server[] = [];
const contexts: APIRequestContext[] = [];

async function serve(handler: RequestListener): Promise<string> {
  const server = createServer(handler);
  servers.push(server);
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
}

afterEach(async () => {
  await Promise.all(contexts.splice(0).map((context) => context.dispose()));
  await Promise.all(servers.splice(0).map((server) => new Promise<void>((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  })));
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe('real story conversation seed', () => {
  it('uses the backend origin, authenticated owner, and one session for every turn', async () => {
    let frontendHits = 0;
    const frontend = await serve((_req, res) => {
      frontendHits += 1;
      res.writeHead(404).end();
    });
    const received: Array<{ path?: string; authorization?: string; body: unknown }> = [];
    const backend = await serve((req, res) => {
      let body = '';
      req.on('data', (chunk) => { body += chunk; });
      req.on('end', () => {
        received.push({ path: req.url, authorization: req.headers.authorization, body: JSON.parse(body) });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ session_id: 'owned-story-session', content: 'chapter' }));
      });
    });
    vi.stubEnv('E2E_API_URL', `${backend}/`);
    const context = await request.newContext({ baseURL: frontend });
    contexts.push(context);
    const { seedConversationViaApi } = await import('./real-chat');
    const sessionId = await seedConversationViaApi(context, 'issued-token', ['first chapter', 'second chapter']);

    expect(sessionId).toBe('owned-story-session');
    expect(frontendHits).toBe(0);
    expect(received).toEqual([
      { path: '/api/v1/chat/completions', authorization: 'Bearer issued-token', body: { message: 'first chapter' } },
      { path: '/api/v1/chat/completions', authorization: 'Bearer issued-token', body: { message: 'second chapter', session_id: 'owned-story-session' } },
    ]);
  });

  it.each([
    [401, {}, 'real-mode conversation seeding failed: 401'],
    [200, {}, 'real-mode conversation seeding produced no session id'],
  ])('fails explicitly for a %s response without a usable session', async (status, payload, message) => {
    const backend = await serve((_req, res) => {
      res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(payload));
    });
    vi.stubEnv('E2E_API_URL', backend);
    const context = await request.newContext({ baseURL: backend });
    contexts.push(context);
    const { seedConversationViaApi } = await import('./real-chat');
    await expect(seedConversationViaApi(context, 'issued-token', ['chapter'])).rejects.toThrow(message);
  });

  it('rejects a backend that changes the session between turns', async () => {
    let turns = 0;
    const backend = await serve((_req, res) => {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ session_id: `session-${++turns}` }));
    });
    vi.stubEnv('E2E_API_URL', backend);
    const context = await request.newContext({ baseURL: backend });
    contexts.push(context);
    const { seedConversationViaApi } = await import('./real-chat');
    await expect(seedConversationViaApi(context, 'issued-token', ['one', 'two']))
      .rejects.toThrow('real-mode conversation seeding changed session');
  });
});
