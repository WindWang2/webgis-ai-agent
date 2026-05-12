'use client';

import { useState } from 'react';
import { ChevronDown, ChevronRight, Brain } from 'lucide-react';

interface CollapsibleThinkProps {
  content: string;
  isDark: boolean;
  accentColor: string;
}

export function CollapsibleThink({ content, isDark, accentColor }: CollapsibleThinkProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  if (!content) return null;

  return (
    <div className="mb-2">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center gap-1.5 px-2 py-1 rounded-md transition-colors hover:bg-black/5"
        style={{ color: isDark ? '#94a3b8' : '#64748b' }}
      >
        {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Brain size={14} style={{ color: accentColor }} />
        <span className="text-[11px] font-medium uppercase tracking-wider">思考过程</span>
      </button>

      {isExpanded && (
        <div 
          className="mt-1 px-3 py-2 rounded-lg text-[11.5px] leading-relaxed border-l-2 italic"
          style={{ 
            backgroundColor: isDark ? 'rgba(30,41,59,0.4)' : 'rgba(241,245,249,0.6)',
            borderColor: accentColor,
            color: isDark ? '#cbd5e1' : '#475569'
          }}
        >
          <div className="whitespace-pre-wrap">{content}</div>
        </div>
      )}
    </div>
  );
}
