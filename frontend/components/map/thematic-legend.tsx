'use client';

import React from 'react';
import { Info } from 'lucide-react';

interface LegendProps {
  metadata: {
    thematic_type: string;
    field: string;
    breaks: number[];
    palette: string;
  };
}

const COLOR_PALETTES: Record<string, string[]> = {
  "YlOrRd": ["#ffffb2", "#fed976", "#feb24c", "#fd8d3c", "#f03b20", "#bd0026"],
  "Blues": ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
  "Greens": ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
  "Reds": ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
  "Viridis": ["#440154", "#3b528b", "#21908c", "#5dc963", "#fde725"],
  "Magma": ["#000004", "#3b0f70", "#8c2981", "#de4968", "#feb078", "#fcfdbf"],
};

export function ThematicLegend({ metadata }: LegendProps) {
  const { field, breaks, palette } = metadata;
  const colors = COLOR_PALETTES[palette] || COLOR_PALETTES["YlOrRd"];

  // Helper to format numbers nicely
  const formatNum = (n: number) => {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
    return n.toFixed(1);
  };

  return (
    <div className="bg-card/90 backdrop-blur-md border border-border p-4 rounded-xl shadow-2xl min-w-[180px] animate-in slide-in-from-right-4 duration-500">
      <div className="flex items-center gap-2 mb-3 border-b border-border pb-2">
        <div className="p-1 bg-primary/10 rounded-md">
          <Info className="h-3.5 w-3.5 text-primary" />
        </div>
        <div className="flex flex-col">
          <span className="text-[10px] uppercase font-bold tracking-widest text-muted-foreground/80">图例说明</span>
          <span className="text-xs font-semibold text-foreground truncate max-w-[140px]" title={field}>
            字段: {field}
          </span>
        </div>
      </div>

      <div className="space-y-2">
        {breaks.slice(0, -1).map((val, idx) => {
          const nextVal = breaks[idx + 1];
          // Determine color index (CartographyService uses normalized position, 
          // but we can approximate with colors[idx] if len(colors) == len(breaks)-1)
          const colorIdx = Math.min(idx, colors.length - 1);
          
          return (
            <div key={idx} className="flex items-center gap-3 group transition-all">
              <div 
                className="w-3.5 h-3.5 rounded-sm shadow-sm ring-1 ring-black/10 group-hover:scale-110 transition-transform" 
                style={{ backgroundColor: colors[colorIdx] }} 
              />
              <span className="text-[11px] font-medium text-muted-foreground group-hover:text-foreground transition-colors">
                {formatNum(val)} — {formatNum(nextVal)}
              </span>
            </div>
          );
        })}
      </div>
      
      <div className="mt-4 pt-2 border-t border-border/40 text-[9px] text-muted-foreground/60 italic text-center">
        数据驱动专题渲染
      </div>
    </div>
  );
}
