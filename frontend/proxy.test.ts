import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { proxy } from './proxy';

afterEach(() => vi.unstubAllEnvs());

describe('per-request script CSP', () => {
  it('uses distinct nonces for trusted framework and theme scripts in production', () => {
    vi.stubEnv('NODE_ENV', 'production');
    const request = new NextRequest('https://maps.example.test/');
    const first = proxy(request);
    const second = proxy(request);
    const policy = first.headers.get('Content-Security-Policy')!;
    const nonce = policy.match(/'nonce-([^']+)'/)?.[1];
    expect(nonce).toBeTruthy();
    expect(first.headers.get('x-middleware-request-x-nonce')).toBe(nonce);
    expect(first.headers.get('x-middleware-request-content-security-policy')).toBe(policy);
    expect(second.headers.get('Content-Security-Policy')).not.toBe(policy);
    const scripts = policy.split(';').find((directive) => directive.trim().startsWith('script-src'))!;
    expect(scripts).toContain("'strict-dynamic'");
    expect(scripts).not.toMatch(/unsafe-inline|unsafe-eval/);
  });
});
