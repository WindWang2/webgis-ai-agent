import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// LlmConfig 只读展示服务端配置，不再读写 store —— 模块本身也不 import
// useHudStore，无需 mock。

import { LlmConfig } from './llm-config';

// ── Response doubles（与 transport.test.ts / use-workspace-session.test.ts 同形）──
const jsonOk = (body: unknown, status = 200) => ({
  ok: true,
  status,
  statusText: 'OK',
  json: () => Promise.resolve(body),
  text: () => Promise.resolve(JSON.stringify(body)),
});

const jsonErr = (status: number, statusText: string, body: unknown) => ({
  ok: false,
  status,
  statusText,
  json: () => Promise.resolve(body),
  text: () => Promise.resolve(typeof body === 'string' ? body : JSON.stringify(body)),
});

const fetchMock = vi.fn();

const SERVER_CONFIG = {
  base_url: 'https://api.openai.com/v1',
  model: 'gpt-4o',
  api_key: '***abcd',
  use_prompt_caching: true,
};

describe('LlmConfig connectivity test (#390)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('对失败端点渲染 error 状态并展示后端错误详情（不再假成功）', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk(SERVER_CONFIG)) // mount: GET /config/llm
      .mockResolvedValueOnce(
        jsonErr(502, 'Bad Gateway', { detail: '连接失败: 上游返回 HTTP 401' })
      ); // POST /config/llm/test

    const user = userEvent.setup();
    render(<LlmConfig />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole('button', { name: /connectivity test/i }));

    expect(await screen.findByText(/Connection Failed/)).toBeInTheDocument();
    // 错误详情来自真实后端响应，渲染在 UI 上（不是不可达的死分支）
    expect(screen.getByText(/连接失败: 上游返回 HTTP 401/)).toBeInTheDocument();

    // 测试请求确实是 POST 到 /config/llm/test，且 body 为空
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toContain('/api/v1/config/llm/test');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({});
  });

  it('测试成功后渲染 Connection OK', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk(SERVER_CONFIG))
      .mockResolvedValueOnce(jsonOk({ status: 'ok', detail: '连接成功: gpt-4o' }));

    const user = userEvent.setup();
    render(<LlmConfig />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole('button', { name: /connectivity test/i }));

    expect(await screen.findByText('Connection OK')).toBeInTheDocument();
  });

  it('只读展示服务端配置，并说明前端保存不生效', async () => {
    fetchMock.mockResolvedValueOnce(jsonOk(SERVER_CONFIG));

    render(<LlmConfig />);

    expect(await screen.findByText('https://api.openai.com/v1')).toBeInTheDocument();
    expect(screen.getByText('gpt-4o')).toBeInTheDocument();
    expect(screen.getByText('***abcd')).toBeInTheDocument();
    expect(screen.getByText('已启用')).toBeInTheDocument(); // prompt caching
    expect(screen.getByText(/前端保存不会生效/)).toBeInTheDocument();
    // 只读展示 —— 没有可编辑输入框
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('非管理员读取服务端配置失败时给出明确提示', async () => {
    fetchMock.mockResolvedValueOnce(jsonErr(403, 'Forbidden', { detail: 'Not enough permissions' }));

    render(<LlmConfig />);

    expect(
      await screen.findByText('需要管理员权限才能查看服务端 LLM 配置')
    ).toBeInTheDocument();
  });
});
