import { describe, it, expect, afterEach } from 'vitest';
import { installStoryTokenResponder, requestStoryOwnerToken } from './story-token-handoff';

/**
 * F-09: the Story tab gets the anonymous owner token from its opener via a
 * same-origin postMessage handshake (never via the URL).
 */
describe('story owner-token handoff (F-09)', () => {
  const origOpener = Object.getOwnPropertyDescriptor(window, 'opener');
  let uninstall: () => void = () => {};

  afterEach(() => {
    uninstall();
    if (origOpener) Object.defineProperty(window, 'opener', origOpener);
  });

  /** Wire window.opener to a fake opener that routes into this same window. */
  function fakeOpener(): void {
    const opener = {
      postMessage: (data: unknown) => {
        // Deliver as a same-origin message whose source can reply.
        window.dispatchEvent(
          new MessageEvent('message', {
            data,
            origin: window.location.origin,
            source: {
              postMessage: (reply: unknown) =>
                window.dispatchEvent(new MessageEvent('message', { data: reply, origin: window.location.origin })),
            } as unknown as Window,
          }),
        );
      },
    };
    Object.defineProperty(window, 'opener', { configurable: true, value: opener });
  }

  it('resolves null immediately without an opener', async () => {
    Object.defineProperty(window, 'opener', { configurable: true, value: null });
    await expect(requestStoryOwnerToken('s1', 50)).resolves.toBeNull();
  });

  it('receives the token the opener holds for that session', async () => {
    uninstall = installStoryTokenResponder((sid) => (sid === 's1' ? 'owner-1' : null));
    fakeOpener();
    await expect(requestStoryOwnerToken('s1', 500)).resolves.toBe('owner-1');
    await expect(requestStoryOwnerToken('other', 500)).resolves.toBeNull();
  });

  it('the responder ignores cross-origin requests', async () => {
    const replies: unknown[] = [];
    uninstall = installStoryTokenResponder(() => 'secret');
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'webgis:story-owner-token:request', sessionId: 's1', nonce: 'n' },
        origin: 'https://evil.example',
        source: { postMessage: (r: unknown) => replies.push(r) } as unknown as Window,
      }),
    );
    expect(replies).toEqual([]);
  });

  it('times out to null when the opener never answers', async () => {
    Object.defineProperty(window, 'opener', { configurable: true, value: { postMessage: () => {} } });
    await expect(requestStoryOwnerToken('s1', 20)).resolves.toBeNull();
  });
});
