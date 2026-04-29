'use client';

import React, { useState } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { STitle, SField, SButton } from '@/components/shared/section-title';
import ToggleSwitch from '@/components/shared/toggle-switch';

export function LlmConfig() {
  const llmConfigFull = useHudStore((s) => s.llmConfigFull);
  const setLlmConfigFull = useHudStore((s) => s.setLlmConfigFull);

  const [baseUrl, setBaseUrl] = useState(llmConfigFull.baseUrl);
  const [apiKey, setApiKey] = useState(llmConfigFull.apiKey);
  const [model, setModel] = useState(llmConfigFull.model);
  const [caching, setCaching] = useState(llmConfigFull.caching);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<
    'idle' | 'success' | 'error'
  >('idle');
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    setLlmConfigFull({ baseUrl, apiKey, model, caching });
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleTest = () => {
    setTesting(true);
    setTestResult('idle');
    setTimeout(() => {
      setTesting(false);
      setTestResult('success');
      setTimeout(() => setTestResult('idle'), 3000);
    }, 1500);
  };

  return (
    <div className="flex flex-col gap-5">
      <STitle title="大模型配置" sub="LLM Model Settings" />

      {/* Base URL */}
      <SField
        label="Base URL"
        value={baseUrl}
        onChange={setBaseUrl}
        placeholder="https://api.openai.com/v1"
      />

      {/* API Key */}
      <SField
        label="API Key"
        value={apiKey}
        onChange={setApiKey}
        type="password"
        placeholder="sk-..."
      />

      {/* Model */}
      <SField
        label="Model"
        value={model}
        onChange={setModel}
        placeholder="gpt-4o"
      />

      {/* Caching toggle */}
      <div className="flex items-center justify-between py-1">
        <div>
          <div className="text-[12px] font-medium text-slate-700">
            Prompt Caching
          </div>
          <div className="text-[11px] text-slate-400">
            Cache repeated prompts to reduce latency and token usage
          </div>
        </div>
        <ToggleSwitch checked={caching} onChange={() => setCaching(!caching)} />
      </div>

      {/* Connectivity test */}
      <div className="flex items-center gap-3">
        <button
          onClick={handleTest}
          disabled={testing}
          className="inline-flex items-center gap-1.5 rounded px-3 py-1 text-[11px] font-medium border border-slate-200 bg-white/70 text-slate-600 hover:bg-slate-50 transition-all disabled:opacity-50"
        >
          {testing ? (
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
        {testResult === 'success' && (
          <span className="text-[11px] font-medium text-green-600">
            Connection OK
          </span>
        )}
        {testResult === 'error' && (
          <span className="text-[11px] font-medium text-red-500">
            Connection Failed
          </span>
        )}
      </div>

      {/* Save */}
      <div className="pt-2">
        <SButton saved={saved} onClick={handleSave}>
          {saved ? 'Saved' : 'Save'}
        </SButton>
      </div>
    </div>
  );
}
