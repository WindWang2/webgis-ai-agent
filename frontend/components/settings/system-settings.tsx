'use client';

import React, { useState } from 'react';
import { STitle, SField, SButton } from '@/components/shared/section-title';

export function SystemSettings() {
  const [apiUrl, setApiUrl] = useState('http://localhost:8000');
  const [language, setLanguage] = useState<'zh' | 'en'>('zh');
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <div className="flex flex-col gap-5">
      <STitle title="系统设置" sub="System Settings" />

      {/* Backend API URL */}
      <SField
        label="Backend API URL"
        value={apiUrl}
        onChange={setApiUrl}
        placeholder="http://localhost:8000"
      />

      {/* Language selection */}
      <div>
        <div className="text-[10px] uppercase tracking-wide text-slate-400 font-medium mb-2">
          Language / 语言
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setLanguage('zh')}
            className="flex-1 rounded-lg border-2 py-2 text-[12px] font-medium transition-all"
            style={{
              borderColor:
                language === 'zh' ? '#16a34a' : 'rgba(15,23,42,0.08)',
              backgroundColor:
                language === 'zh'
                  ? 'rgba(22,163,74,0.04)'
                  : 'rgba(255,255,255,0.5)',
              color: language === 'zh' ? '#16a34a' : '#475569',
            }}
          >
            中文
          </button>
          <button
            onClick={() => setLanguage('en')}
            className="flex-1 rounded-lg border-2 py-2 text-[12px] font-medium transition-all"
            style={{
              borderColor:
                language === 'en' ? '#16a34a' : 'rgba(15,23,42,0.08)',
              backgroundColor:
                language === 'en'
                  ? 'rgba(22,163,74,0.04)'
                  : 'rgba(255,255,255,0.5)',
              color: language === 'en' ? '#16a34a' : '#475569',
            }}
          >
            English
          </button>
        </div>
      </div>

      {/* About section */}
      <div className="rounded-xl border border-slate-900/8 bg-white/50 px-4 py-3">
        <div className="flex items-center gap-2.5 mb-2">
          <div
            className="flex items-center justify-center rounded-lg"
            style={{
              width: 28,
              height: 28,
              background:
                'linear-gradient(135deg, #16a34a 0%, #22c55e 100%)',
            }}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="white"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <path d="M12 6v6l4 2" />
            </svg>
          </div>
          <div>
            <div className="text-[13px] font-bold text-slate-800">
              GeoAgent
            </div>
            <div className="text-[10px] text-slate-400 font-mono">
              v0.1.0
            </div>
          </div>
        </div>
        <div className="text-[11px] text-slate-400 italic">
          &quot;All is Agent&quot;
        </div>
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
