'use client';

import React, { useState } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { STitle } from '@/components/shared/section-title';
import ToggleSwitch from '@/components/shared/toggle-switch';

export function McpServers() {
  const mcpServers = useHudStore((s) => s.mcpServers);
  const toggleMcpServer = useHudStore((s) => s.toggleMcpServer);

  const [pingingId, setPingingId] = useState<string | null>(null);
  const [pingResults, setPingResults] = useState<
    Record<string, 'ok' | 'fail'>
  >({});

  const handlePing = (id: string) => {
    setPingingId(id);
    setTimeout(() => {
      setPingingId(null);
      setPingResults((prev) => ({ ...prev, [id]: 'ok' }));
      setTimeout(() => {
        setPingResults((prev) => {
          const next = { ...prev };
          delete next[id];
          return next;
        });
      }, 3000);
    }, 800);
  };

  return (
    <div className="flex flex-col gap-4">
      <STitle title="MCP 服务" sub="Model Context Protocol Servers" />

      {/* Server cards */}
      <div className="flex flex-col gap-2.5">
        {mcpServers.map((srv) => {
          const isActive = srv.status === 'active';
          return (
            <div
              key={srv.id}
              className="rounded-xl border border-slate-900/8 bg-white/60 px-4 py-3 transition-all"
              style={{
                opacity: isActive ? 1 : 0.65,
              }}
            >
              <div className="flex items-start gap-3">
                {/* Status dot */}
                <div className="pt-1">
                  <span
                    className="block rounded-full"
                    style={{
                      width: 8,
                      height: 8,
                      backgroundColor: isActive ? '#16a34a' : '#cbd5e1',
                      boxShadow: isActive
                        ? '0 0 6px rgba(22,163,74,0.4)'
                        : 'none',
                    }}
                  />
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-semibold text-slate-800 font-mono">
                      {srv.name}
                    </span>
                    <span
                      className="text-[10px] font-medium rounded-full px-1.5 py-0.5"
                      style={{
                        backgroundColor:
                          srv.transport === 'stdio'
                            ? 'rgba(22,163,74,0.08)'
                            : 'rgba(59,130,246,0.08)',
                        color:
                          srv.transport === 'stdio' ? '#16a34a' : '#3b82f6',
                      }}
                    >
                      {srv.transport.toUpperCase()}
                    </span>
                    {srv.warn && (
                      <span className="text-[10px] text-amber-500 font-medium">
                        !
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-slate-400 mt-0.5 leading-snug">
                    {srv.desc}
                  </div>

                  {/* Command / URL */}
                  <div className="mt-1.5 text-[10px] font-mono text-slate-300 truncate">
                    {srv.transport === 'stdio' ? srv.cmd : srv.url}
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2 pt-0.5">
                  {/* Ping */}
                  <button
                    onClick={() => handlePing(srv.id)}
                    disabled={pingingId === srv.id}
                    className="text-[10px] font-medium px-2 py-0.5 rounded border border-slate-200/80 bg-white/50 text-slate-500 hover:bg-slate-50 transition-all disabled:opacity-50"
                  >
                    {pingingId === srv.id
                      ? '...'
                      : pingResults[srv.id] === 'ok'
                        ? 'OK'
                        : 'Ping'}
                  </button>

                  {/* Toggle */}
                  <ToggleSwitch
                    checked={isActive}
                    onChange={() => toggleMcpServer(srv.id)}
                  />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Add server */}
      <button className="flex items-center justify-center gap-2 rounded-xl border-2 border-dashed border-slate-200/80 bg-white/30 py-3 text-[12px] font-medium text-slate-400 hover:text-slate-500 hover:border-slate-300 transition-all">
        <svg
          width="14"
          height="14"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        >
          <line x1="8" y1="3" x2="8" y2="13" />
          <line x1="3" y1="8" x2="13" y2="8" />
        </svg>
        Add MCP Server
      </button>
    </div>
  );
}
