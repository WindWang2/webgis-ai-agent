import { spawn, type ChildProcess } from 'node:child_process';
import { createServer } from 'node:net';
import type { AddressInfo } from 'node:net';
import { request, type APIRequestContext } from 'playwright';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import storySeed from '../fixtures/story-seed.json';

let child: ChildProcess;
let client: APIRequestContext;
let baseURL: string;
let stubOutput = '';

beforeAll(async () => {
  const portFinder = createServer();
  await new Promise<void>((resolve) => portFinder.listen(0, '127.0.0.1', resolve));
  const port = (portFinder.address() as AddressInfo).port;
  await new Promise<void>((resolve) => portFinder.close(() => resolve()));
  child = spawn(process.execPath, ['e2e/tools/llm-stub.mjs'], {
    cwd: process.cwd(),
    env: { ...process.env, STUB_PORT: String(port), STUB_FAIL_AT: '', STUB_DELAY_FIRST_TOKEN_MS: '', STUB_TASK_CANCEL_DELAY_MS: '90000' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout!.on('data', (chunk) => { stubOutput += chunk.toString(); });
  await new Promise<void>((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', (code) => reject(new Error(`LLM stub exited before listening: ${code}`)));
    child.stdout!.on('data', (chunk) => {
      if (chunk.toString().includes('[llm-stub] listening')) resolve();
    });
  });
  baseURL = `http://127.0.0.1:${port}`;
  client = await request.newContext({ baseURL });
});

afterAll(async () => {
  await client?.dispose();
  if (child && child.exitCode === null) {
    const exited = new Promise<void>((resolve) => child.once('exit', () => resolve()));
    child.kill();
    await exited;
  }
});

describe('nightly LLM story contract', () => {
  it('answers the first StoryMap turn with the heading the real UI must render', async () => {
    const response = await client.post('/v1/chat/completions', { data: {
      messages: [{ role: 'user', content: storySeed[0].message }],
    } });
    const payload = await response.json();
    expect(payload.choices[0].message.content).toContain('## 商业 POI 空间分布');
    expect(payload.choices[0].message.tool_calls).toBeUndefined();
  });

  it('selects the second chapter from the latest user turn, not the first prompt in history', async () => {
    const response = await client.post('/v1/chat/completions', { data: {
      messages: [
        { role: 'user', content: storySeed[0].message },
        { role: 'assistant', content: '## 商业 POI 空间分布\n\n第一章' },
        { role: 'user', content: '继续：生成 15 分钟等时圈结论' },
      ],
    } });
    const payload = await response.json();
    expect(payload.choices[0].message.content).toContain('## 15 分钟等时圈');
    expect(payload.choices[0].message.content).not.toContain('## 商业 POI 空间分布');
  });

  it('preserves the hotspot tool call for the analysis journey', async () => {
    const response = await client.post('/v1/chat/completions', { data: {
      messages: [{ role: 'user', content: '对上传的数据做热点分析' }],
    } });
    const payload = await response.json();
    expect(payload.choices[0].message.tool_calls[0].function.name).toBe('hotspot_analysis');
  });

  it('does not mistake a prior turn tool result for the current turn result', async () => {
    const response = await client.post('/v1/chat/completions', { data: {
      messages: [
        { role: 'user', content: '第一次分析' },
        { role: 'tool', content: 'first result' },
        { role: 'assistant', content: '第一次完成' },
        { role: 'user', content: '对上传的数据做热点分析' },
      ],
    } });
    const payload = await response.json();
    expect(payload.choices[0].message.tool_calls[0].function.name).toBe('hotspot_analysis');
  });

  it.each([false, true])('holds only the cancellation intent and observes the closed upstream socket (stream=%s)', async (stream) => {
    const closedBefore = (stubOutput.match(/ cancelclosed\b/g) ?? []).length;
    const cancellableClient = await request.newContext({ baseURL });
    let settled = false;
    const cancel = cancellableClient.post('/v1/chat/completions', { data: {
      stream,
      messages: [{ role: 'user', content: '启动可取消的任务旅程' }],
    } }).then(() => { settled = true; return null; }, (error) => { settled = true; return error; });
    try {
      const story = await client.post('/v1/chat/completions', { data: {
        messages: [{ role: 'user', content: '继续：生成 15 分钟等时圈结论' }],
      } });
      expect((await story.json()).choices[0].message.content).toContain('## 15 分钟等时圈');
      expect(settled).toBe(false);
    } finally {
      await cancellableClient.dispose();
    }
    expect(await cancel).toBeInstanceOf(Error);
    await expect.poll(() => (stubOutput.match(/ cancelclosed\b/g) ?? []).length).toBe(closedBefore + 1);
  });

  it('establishes a session for the fabric journey without an unrelated spatial tool', async () => {
    const response = await client.post('/v1/chat/completions', { data: {
      messages: [{ role: 'user', content: '建立会话' }],
    } });
    const payload = await response.json();
    expect(payload.choices[0].message.content).toBe('会话已建立，可以开始数据查询。');
    expect(payload.choices[0].message.tool_calls).toBeUndefined();
  });

  it('emits story markdown and a terminal stop in streaming mode too', async () => {
    const response = await client.post('/v1/chat/completions', { data: {
      stream: true,
      messages: [{ role: 'user', content: '继续：生成 15 分钟等时圈结论' }],
    } });
    const body = await response.text();
    expect(body).toContain('## 15 分钟等时圈');
    expect(body).toContain('"finish_reason":"stop"');
    expect(body).not.toContain('"tool_calls"');
    expect(body).toContain('data: [DONE]');
  });
});
