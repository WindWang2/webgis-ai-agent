'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { STitle } from '@/components/shared/section-title';
import { apiFetch, isApiError, describeApiError } from '@/lib/api/transport';

/** GET /api/v1/config/llm 返回的服务端 LLM 配置（api_key 已被服务端掩码）。 */
interface ServerLlmConfig {
  base_url: string;
  model: string;
  api_key: string;
  use_prompt_caching: boolean;
}

type TestState = 'idle' | 'testing' | 'success' | 'error';

/** 只读展示行：值来自服务端，前端不提供编辑（#390，见下）。 */
function ReadOnlyRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="eyebrow">{label}</span>
      <div className="flex h-control-lg w-full items-center rounded-sm border border-edge-subtle bg-surface-sunken px-2 font-mono text-body text-ink-disabled">
        <span className="truncate">{value || '—'}</span>
      </div>
    </div>
  );
}

export function LlmConfig() {
  const [serverConfig, setServerConfig] = useState<ServerLlmConfig | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [testState, setTestState] = useState<TestState>('idle');
  const [testDetail, setTestDetail] = useState<string | null>(null);

  const loadServerConfig = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await apiFetch<ServerLlmConfig>('/api/v1/config/llm', {
        label: 'LLM config load error',
      });
      setServerConfig(data);
    } catch (err) {
      setServerConfig(null);
      setLoadError(
        isApiError(err) && err.status === 403
          ? '需要管理员权限才能查看服务端 LLM 配置'
          : describeApiError(err, '无法读取服务端 LLM 配置')
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadServerConfig();
  }, [loadServerConfig]);

  const handleTest = useCallback(async () => {
    setTestState('testing');
    setTestDetail(null);
    try {
      // #390：真实连通性测试。body 留空 —— 测试服务端当前生效的配置，
      // 真实 apiKey 由服务端持有，不会出现在请求里。之前这里是
      // setTimeout 1.5s 后无条件 setTestState('success') 的假测试。
      await apiFetch<{ status: string; detail?: string }>(
        '/api/v1/config/llm/test',
        { method: 'POST', body: {}, label: 'LLM connectivity test error' }
      );
      setTestState('success');
    } catch (err) {
      setTestState('error');
      setTestDetail(
        isApiError(err) && err.status === 403
          ? '需要管理员权限才能测试连接'
          : describeApiError(err, '连接失败')
      );
    }
  }, []);

  return (
    <div className="flex flex-col gap-5">
      <STitle title="大模型配置" sub="LLM Model Settings" />

      {/* #390：LLM 配置由服务端统一管理，前端保存从未被后端消费 ——
          改为只读展示并在 UI 明说，不再假装前端设置有效。 */}
      <div className="rounded-md border border-edge-subtle bg-surface-raised px-4 py-3 text-body text-ink-secondary">
        LLM 配置由服务端统一管理，前端保存不会生效。如需修改，请使用管理员账号在服务端完成，之后点击「刷新配置」更新显示。
      </div>

      {loading && <div className="text-body text-ink-muted">加载中…</div>}

      {!loading && loadError && (
        <div className="text-body font-medium text-status-critical">{loadError}</div>
      )}

      {!loading && serverConfig && (
        <>
          <ReadOnlyRow label="Base URL" value={serverConfig.base_url} />
          <ReadOnlyRow label="Model" value={serverConfig.model} />
          <ReadOnlyRow
            label="API Key"
            value={serverConfig.api_key || '（未配置）'}
          />
          <ReadOnlyRow
            label="Prompt Caching"
            value={serverConfig.use_prompt_caching ? '已启用' : '未启用'}
          />
        </>
      )}

      {/* Connectivity test */}
      <div className="flex items-center gap-3">
        <button
          onClick={handleTest}
          disabled={testState === 'testing'}
          className="inline-flex items-center gap-1.5 rounded-sm border border-edge-subtle bg-surface-sunken px-3 py-1 text-body font-medium text-ink-secondary transition-all hover:bg-surface-hover disabled:opacity-50"
        >
          {testState === 'testing' ? (
            <>
              <svg
                className="animate-spin"
                width="12"
                height="12"
                viewBox="0 0 24 24"
                fill="none"
              >
                <circle
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="3"
                  strokeDasharray="31.4 31.4"
                  strokeLinecap="round"
                />
              </svg>
              Testing...
            </>
          ) : (
            <>Connectivity Test</>
          )}
        </button>
        <button
          onClick={loadServerConfig}
          disabled={loading}
          className="inline-flex items-center gap-1.5 rounded-sm border border-edge-subtle bg-surface-sunken px-3 py-1 text-body font-medium text-ink-secondary transition-all hover:bg-surface-hover disabled:opacity-50"
        >
          刷新配置
        </button>
      </div>
      {testState === 'success' && (
        <div className="text-body font-medium text-status-success">
          Connection OK
        </div>
      )}
      {testState === 'error' && (
        <div className="text-body font-medium text-status-critical">
          Connection Failed{testDetail ? `：${testDetail}` : ''}
        </div>
      )}
    </div>
  );
}
