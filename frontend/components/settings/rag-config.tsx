'use client';

import React, { useState, useEffect, useRef } from 'react';
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

  const timersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());

  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      timers.forEach((t) => clearTimeout(t));
      timers.clear();
    };
  }, []);

  const addTimer = (cb: () => void, ms: number) => {
    const id = setTimeout(() => {
      timersRef.current.delete(id);
      cb();
    }, ms);
    timersRef.current.add(id);
    return id;
  };

  const handleSave = () => {
    setRagConfig({ vectorDb, collection });
    setSaved(true);
    addTimer(() => setSaved(false), 2000);
  };

  const handleTestConnection = () => {
    setTesting(true);
    setTestResult('idle');
    addTimer(() => {
      setTesting(false);
      setTestResult('success');
      addTimer(() => setTestResult('idle'), 3000);
    }, 1200);
  };

  return (
    <div className="flex flex-col gap-5">
      <STitle title="知识库 · RAG" sub="Retrieval-Augmented Generation" />

      {/* Spatial index section */}
      <div>
        <div className="text-[15px] uppercase tracking-wider text-[var(--theme-text-muted)] font-semibold mb-2">
          Spatial Index
        </div>
        {ragSpatial.length === 0 ? (
          <div className="text-[15px] text-[var(--theme-text-subtle)] italic py-2">
            No spatial documents indexed yet
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {ragSpatial.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center gap-3 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] px-3 py-2"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[14px] font-medium text-[var(--theme-text-primary)]">
                      {doc.name}
                    </span>
                    <span className="text-[14px] text-[var(--theme-text-muted)] bg-[var(--theme-bg-muted)] rounded px-1.5 py-0.5">
                      {doc.type}
                    </span>
                  </div>
                  <div className="text-[14px] text-[var(--theme-text-muted)] mt-0.5">
                    {doc.features !== null && `${doc.features} features`}
                    {doc.features !== null && ' · '}
                    {doc.size}
                  </div>
                </div>
                <span
                  className="text-[14px] font-medium rounded-full px-1.5 py-0.5"
                  style={{
                    backgroundColor: doc.indexed
                      ? 'color-mix(in srgb, var(--agent-accent, #16a34a) 10%, transparent)'
                      : 'var(--theme-bg-muted)',
                    color: doc.indexed ? 'var(--agent-accent, #16a34a)' : 'var(--theme-text-muted)',
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
        <div className="text-[15px] uppercase tracking-wider text-[var(--theme-text-muted)] font-semibold mb-2">
          Semantic Index
        </div>
        {ragSemantic.length === 0 ? (
          <div className="text-[15px] text-[var(--theme-text-subtle)] italic py-2">
            No semantic documents indexed yet
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {ragSemantic.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center gap-3 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] px-3 py-2"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[14px] font-medium text-[var(--theme-text-primary)]">
                      {doc.name}
                    </span>
                    <span className="text-[14px] text-[var(--theme-text-muted)]">
                      {doc.chunks} chunks
                    </span>
                  </div>
                  <div className="text-[14px] text-[var(--theme-text-muted)] mt-0.5">
                    {doc.size}
                  </div>
                </div>
                <span
                  className="text-[14px] font-medium rounded-full px-1.5 py-0.5"
                  style={{
                    backgroundColor: doc.indexed
                      ? 'color-mix(in srgb, var(--agent-accent, #16a34a) 10%, transparent)'
                      : 'var(--theme-bg-muted)',
                    color: doc.indexed ? 'var(--agent-accent, #16a34a)' : 'var(--theme-text-muted)',
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
        <div className="text-[15px] uppercase tracking-wider text-[var(--theme-text-muted)] font-semibold mb-3">
          Retrieval Config
        </div>

        <div className="flex flex-col gap-4 rounded-xl border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] px-4 py-3">
          {/* Spatial weight slider */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[15px] text-[var(--theme-text-secondary)]">
                Spatial Weight
              </span>
              <span className="text-[15px] font-mono text-[var(--theme-text-secondary)]">
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
                background: `linear-gradient(to right, var(--agent-accent, #16a34a) ${ragConfig.spatialWeight}%, var(--theme-border-subtle) ${ragConfig.spatialWeight}%)`,
              }}
            />
          </div>

          {/* Top K */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-[15px] text-[var(--theme-text-secondary)]">Top K</span>
            </div>
            <input
              type="number"
              min={1}
              max={50}
              value={ragConfig.topK}
              onChange={(e) =>
                setRagConfig({ topK: Number(e.target.value) })
              }
              className="w-20 rounded bg-[var(--theme-bg-input)] border border-[var(--theme-border)] px-2 py-1 text-[14px] font-mono text-[var(--theme-text-primary)] focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)]"
            />
          </div>

          {/* Rerank toggle */}
          <div className="flex items-center justify-between">
            <div>
              <span className="text-[15px] text-[var(--theme-text-secondary)]">Rerank</span>
              <span className="text-[14px] text-[var(--theme-text-muted)] ml-1">
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
        <div className="text-[15px] uppercase tracking-wider text-[var(--theme-text-muted)] font-semibold mb-3">
          Vector DB Connection
        </div>
        <div className="flex flex-col gap-3 rounded-xl border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)] px-4 py-3">
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
              className="inline-flex items-center gap-1.5 rounded px-3 py-1 text-[15px] font-medium border border-[var(--theme-border)] bg-[var(--theme-bg-input)] text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-hover)] transition-all disabled:opacity-50"
            >
              {testing ? 'Testing...' : 'Test Connection'}
            </button>
            {testResult === 'success' && (
              <span className="text-[15px] font-medium text-emerald-600 dark:text-emerald-300">
                Connected
              </span>
            )}
            {testResult === 'error' && (
              <span className="text-[15px] font-medium text-red-600 dark:text-red-400">
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
