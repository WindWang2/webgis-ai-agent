'use client';

import React from 'react';

export interface GlassPanelProps extends React.HTMLAttributes<HTMLDivElement> {
  className?: string;
  children?: React.ReactNode;
}

export function GlassPanel({ className = '', children, ...props }: GlassPanelProps) {
  return (
    <div
      className={`bg-surface-raised/90 dark:bg-surface-overlay/85 backdrop-blur-md border border-edge-subtle shadow-sm rounded-lg ${className}`}
      {...props}
    >
      {children}
    </div>
  );
}

export default GlassPanel;
