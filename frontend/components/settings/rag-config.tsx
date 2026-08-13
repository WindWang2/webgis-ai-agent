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
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold mb-2">
          Spatial Index
        </div>
        {ragSpatial.length === 0 ? (
          <div className="text-body text-ink-muted italic py-2">
            No spatial documents indexed yet
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {ragSpatial.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center gap-3 rounded-md border border-edge-subtle bg-surface-raised px-3 py-2"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-body font-medium text-ink">
                      {doc.name}
                    </span>
                    <span className="rounded-sm bg-surface-sunken px-1.5 py-0.5 text-body text-ink-muted">
                      {doc.type}
                    </span>
                  </div>
                  <div className="text-body text-ink-muted mt-0.5">
                    {doc.features !== null && `${doc.features} features`}
                    {doc.features !== null && ' · '}
                    {doc.size}
                  </div>
                </div>
                <span
                  className="rounded-pill px-1.5 py-0.5 text-body font-medium"
                  style={{
                    backgroundColor: doc.indexed
                      ? 'color-mix(in srgb, var(--agent-accent, #16a34a) 10%, transparent)'
                      : 'var(--surface-sunken)',
                    color: doc.indexed ? 'var(--agent-accent)' : 'var(--text-muted)',
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
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold mb-2">
          Semantic Index
        </div>
        {ragSemantic.length === 0 ? (
          <div className="text-body text-ink-muted italic py-2">
            No semantic documents indexed yet
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {ragSemantic.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center gap-3 rounded-md border border-edge-subtle bg-surface-raised px-3 py-2"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-body font-medium text-ink">
                      {doc.name}
                    </span>
                    <span className="text-body text-ink-muted">
                      {doc.chunks} chunks
                    </span>
                  </div>
                  <div className="text-body text-ink-muted mt-0.5">
                    {doc.size}
                  </div>
                </div>
                <span
                  className="rounded-pill px-1.5 py-0.5 text-body font-medium"
                  style={{
                    backgroundColor: doc.indexed
                      ? 'color-mix(in srgb, var(--agent-accent, #16a34a) 10%, transparent)'
                      : 'var(--surface-sunken)',
                    color: doc.indexed ? 'var(--agent-accent)' : 'var(--text-muted)',
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
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold mb-3">
          Retrieval Config
        </div>

        <div className="flex flex-col gap-4 rounded-md border border-edge-subtle bg-surface-raised px-4 py-3">
          {/* Spatial weight slider */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-body text-ink-secondary">
                Spatial Weight
              </span>
              <span className="text-body font-mono text-ink-secondary">
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
              className="slider-track h-1.5 w-full"
              style={{
                background: `linear-gradient(to right, var(--agent-accent, #16a34a) ${ragConfig.spatialWeight}%, var(--border-subtle) ${ragConfig.spatialWeight}%)`,
              }}
            />
          </div>

          {/* Top K */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-body text-ink-secondary">Top K</span>
            </div>
            <input
              type="number"
              min={1}
              max={50}
              value={ragConfig.topK}
              onChange={(e) =>
                setRagConfig({ topK: Number(e.target.value) })
              }
              className="w-20 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-body font-mono text-ink focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)]"
            />
          </div>

          {/* Rerank toggle */}
          <div className="flex items-center justify-between">
            <div>
              <span className="text-body text-ink-secondary">Rerank</span>
              <span className="text-body text-ink-muted ml-1">
                Cross-encoder reranking
              </span>
            </div>
            <ToggleSwitch
              label="Rerank（交叉编码器重排序）"
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
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold mb-3">
          Vector DB Connection
        </div>
        <div className="flex flex-col gap-3 rounded-md border border-edge-subtle bg-surface-raised px-4 py-3">
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
              className="inline-flex items-center gap-1.5 rounded-sm border border-edge-subtle bg-surface-sunken px-3 py-1 text-body font-medium text-ink-secondary transition-all hover:bg-surface-hover disabled:opacity-50"
            >
              {testing ? 'Testing...' : 'Test Connection'}
            </button>
            {testResult === 'success' && (
              <span className="text-body font-medium text-status-success">
                Connected
              </span>
            )}
            {testResult === 'error' && (
              <span className="text-body font-medium text-status-critical">
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
