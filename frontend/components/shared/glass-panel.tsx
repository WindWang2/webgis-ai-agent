'use client';

import React from 'react';

interface GlassPanelProps {
  className?: string;
  children: React.ReactNode;
}

export default function GlassPanel({ className = '', children }: GlassPanelProps) {
  return (
    <div
      className={`bg-[rgba(252,253,254,0.88)] backdrop-blur-[24px] border border-white/90 shadow-agent-md rounded-lg ${className}`}
    >
      {children}
    </div>
  );
}
