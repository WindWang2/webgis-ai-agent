import { describe, expect, it, vi } from 'vitest';
import { createMapActionAckSender } from './map-action-acks';

describe('map action ACK follow-up', () => {
  it('delivers a server-issued cartographic repair action to the current session', async () => {
    const body = { repair_action: { action_id: 'ma-carto-1' } };
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(body),
    });
    const onResponse = vi.fn();
    let token = 'owner-a';
    const sender = createMapActionAckSender({
      getSessionId: () => 'session-a',
      getToken: () => token,
      debounceMs: 1,
      fetchImpl: fetchImpl as unknown as typeof fetch,
      onResponse,
    });

    sender.sink({ action_id: 'ma-original', command: 'add_layer', status: 'succeeded' });
    token = 'owner-b';
    sender.flush();
    await Promise.resolve();
    await Promise.resolve();

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl.mock.calls[0][1].headers['X-Session-Token']).toBe('owner-a');
    expect(onResponse).toHaveBeenCalledWith('session-a', body);
    sender.dispose();
  });
});
