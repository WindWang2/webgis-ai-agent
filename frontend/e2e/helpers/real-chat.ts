import type { APIRequestContext } from 'playwright/test';
import { API_ORIGIN } from './mode';

/** Seed persisted chat history without bypassing the real chat engine/DB.
 * The browser and API caller must have the same owner, and later turns must
 * reuse the first response's session rather than create separate stories. */
export async function seedConversationViaApi(
  request: APIRequestContext,
  accessToken: string,
  messages: readonly string[],
): Promise<string> {
  let sessionId = '';
  for (const message of messages) {
    const response = await request.post(new URL('/api/v1/chat/completions', API_ORIGIN).href, {
      headers: { Authorization: `Bearer ${accessToken}` },
      data: { message, ...(sessionId ? { session_id: sessionId } : {}) },
    });
    if (!response.ok()) {
      throw new Error(`real-mode conversation seeding failed: ${response.status()}`);
    }
    const payload = (await response.json()) as { session_id?: string };
    if (!payload.session_id) throw new Error('real-mode conversation seeding produced no session id');
    if (sessionId && payload.session_id !== sessionId) {
      throw new Error('real-mode conversation seeding changed session');
    }
    sessionId = payload.session_id;
  }
  if (!sessionId) throw new Error('real-mode conversation seeding produced no session id');
  return sessionId;
}
