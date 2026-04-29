'use client';

import React, { useState } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { STitle, SField, SButton } from '@/components/shared/section-title';
import ToggleSwitch from '@/components/shared/toggle-switch';

export function RagConfig() {
  const ragConfig = useHudStore((s) => s.ragConfig);
  const setRagConfig = useHudStore((s) => s.setRagConfig);
  const ragSpatial = useHudStore((s) => s.ragSpatial);
  const ragSemantic = useHudStore((s) => s.ragSemantic);

  const [vectorDb, setVectorDb] = useState(ragConfig.vectorDb);
  const [collection, setCollection] = useState(ragConfig.collection);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<
    'idle' | 'success' | 'error'
  >('idle');
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    setRagConfig({ vectorDb, collection });
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleTestConnection = () => {
    setTesting(true);
    setTestResult('idle');
    setTimeout(() => {
      setTesting(false);
      setTestResult('success');
      setTimeout(() => setTestResult('idle'), 3000);
    }, 1200);
  };

  return (
    <div className="flex flex-col gap-5">
      <STitle title="知识库 · RAG" sub="Retrieval-Augmented Generation" />

      {/* Spatial index section */}
      <div>
        <div className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold mb-2">
          Spatial Index
        </div>
        {ragSpatial.length === 0 ? (
          <div className="text-[11px] text-slate-300 italic py-2">
            No spatial documents indexed yet
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {ragSpatial.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center gap-3 rounded-lg border border-slate-900/6 bg-white/50 px-3 py-2"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[12px] font-medium text-slate-700">
                      {doc.name}
                    </span>
                    <span className="text-[10px] text-slate-400 bg-slate-100/80 rounded px-1.5 py-0.5">
                      {doc.type}
                    </span>
                  </div>
                  <div className="text-[10px] text-slate-400 mt-0.5">
                    {doc.features !== null && `${doc.features} features`}
                    {doc.features !== null && ' · '}
                    {doc.size}
                  </div>
                </div>
                <span
                  className="text-[10px] font-medium rounded-full px-1.5 py-0.5"
                  style={{
                    backgroundColor: doc.indexed
                      ? 'rgba(22,163,74,0.08)'
                      : 'rgba(15,23,42,0.05)',
                    color: doc.indexed ? '#16a34a' : '#94a3b8',
                  }}
                >
                  {doc.indexed ? 'Indexed' : 'Pending'}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Semantic index section */}
      <div>
        <div className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold mb-2">
          Semantic Index
        </div>
        {ragSemantic.length === 0 ? (
          <div className="text-[11px] text-slate-300 italic py-2">
            No semantic documents indexed yet
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {ragSemantic.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center gap-3 rounded-lg border border-slate-900/6 bg-white/50 px-3 py-2"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[12px] font-medium text-slate-700">
                      {doc.name}
                    </span>
                    <span className="text-[10px] text-slate-400">
                      {doc.chunks} chunks
                    </span>
                  </div>
                  <div className="text-[10px] text-slate-400 mt-0.5">
                    {doc.size}
                  </div>
                </div>
                <span
                  className="text-[10px] font-medium rounded-full px-1.5 py-0.5"
                  style={{
                    backgroundColor: doc.indexed
                      ? 'rgba(22,163,74,0.08)'
                      : 'rgba(15,23,42,0.05)',
                    color: doc.indexed ? '#16a34a' : '#94a3b8',
                  }}
                >
                  {doc.indexed ? 'Indexed' : 'Pending'}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Retrieval config */}
      <div>
        <div className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold mb-3">
          Retrieval Config
        </div>

        <div className="flex flex-col gap-4 rounded-xl border border-slate-900/8 bg-white/50 px-4 py-3">
          {/* Spatial weight slider */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[11px] text-slate-500">
                Spatial Weight
              </span>
              <span className="text-[11px] font-mono text-slate-600">
                {ragConfig.spatialWeight}%
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={100}
              value={ragConfig.spatialWeight}
              onChange={(e) =>
                setRagConfig({ spatialWeight: Number(e.target.value) })
              }
              className="w-full h-1.5 rounded-full appearance-none cursor-pointer"
              style={{
                background: `linear-gradient(to right, #16a34a ${ragConfig.spatialWeight}%, #e2e8f0 ${ragConfig.spatialWeight}%)`,
              }}
            />
          </div>

          {/* Top K */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-[11px] text-slate-500">Top K</span>
            </div>
            <input
              type="number"
              min={1}
              max={50}
              value={ragConfig.topK}
              onChange={(e) =>
                setRagConfig({ topK: Number(e.target.value) })
              }
              className="w-20 rounded bg-white/70 border border-slate-200/80 px-2 py-1 text-[12px] font-mono text-slate-700 focus:outline-none focus:ring-1 focus:ring-green-400/50"
            />
          </div>

          {/* Rerank toggle */}
          <div className="flex items-center justify-between">
            <div>
              <span className="text-[11px] text-slate-500">Rerank</span>
              <span className="text-[10px] text-slate-400 ml-1">
                Cross-encoder reranking
              </span>
            </div>
            <ToggleSwitch
              checked={ragConfig.rerank}
              onChange={() =>
                setRagConfig({ rerank: !ragConfig.rerank })
              }
            />
          </div>
        </div>
      </div>

      {/* Vector DB connection */}
      <div>
        <div className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold mb-3">
          Vector DB Connection
        </div>
        <div className="flex flex-col gap-3 rounded-xl border border-slate-900/8 bg-white/50 px-4 py-3">
          <SField
            label="Address"
            value={vectorDb}
            onChange={setVectorDb}
            placeholder="http://localhost:19530"
          />
          <SField
            label="Collection"
            value={collection}
            onChange={setCollection}
            placeholder="geoagent"
          />
          <div className="flex items-center gap-3">
            <button
              onClick={handleTestConnection}
              disabled={testing}
              className="inline-flex items-center gap-1.5 rounded px-3 py-1 text-[11px] font-medium border border-slate-200 bg-white/70 text-slate-600 hover:bg-slate-50 transition-all disabled:opacity-50"
            >
              {testing ? 'Testing...' : 'Test Connection'}
            </button>
            {testResult === 'success' && (
              <span className="text-[11px] font-medium text-green-600">
                Connected
              </span>
            )}
            {testResult === 'error' && (
              <span className="text-[11px] font-medium text-red-500">
                Failed
              </span>
            )}
          </div>
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
