'use client';
import { useEffect, useState, useRef } from 'react';
import { useMapAction } from '@/lib/contexts/map-action-context';
import { MapIcon, CheckCircle2 } from 'lucide-react';

const ALLOWED_COMMANDS = new Set([
  'add_layer', 'fly_to', 'zoom_to_bbox', 'set_map_view',
  'add_heatmap_raster', 'add_native_heatmap',
  'base_layer_change', 'layer_visibility_update', 'layer_style_update',
  'remove_layer', 'reorder_layer',
  'export_map', 'add_raster_layer',
  'add_marker', 'draw_measurement', 'clear_annotations',
  'apply_layer_filter',
]);

interface MapActionRendererProps {
  content: string;
}

/**
 * 审计 FE-02/FE-03：之前此组件在每个 streaming token 上重复 dispatch 所有
 * JSON 块（useEffect dep 是 [content, dispatchAction]，而 content 随每个
 * token 更新）。现在用 useRef<Set<string>> 跟踪已 dispatch 的块，跳过
 * 重复。同时修复 bare JSON regex：用括号匹配替代非贪婪 `}` 避免截断嵌套
 * params。
 */
export function MapActionRenderer({ content }: MapActionRendererProps) {
  const { dispatchAction } = useMapAction();
  const [status, setStatus] = useState<'parsing' | 'success' | 'error'>('parsing');
  // FE-02: 跟踪已 dispatch 的 JSON 块，防重复
  const dispatchedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!content || content === 'undefined' || content.trim() === '') {
      setStatus('error');
      return;
    }

    try {
      // Find all JSON blocks (either in ```json ... ``` or balanced { ... })
      const jsonBlocks: string[] = [];

      // 1. Code-fenced blocks: ```json ... ```
      const fencedRegex = /```(?:json)?\s*([\s\S]*?)```/g;
      let match;
      while ((match = fencedRegex.exec(content)) !== null) {
        if (match[1]) jsonBlocks.push(match[1].trim());
      }

      // 2. Bare JSON blocks: use balanced brace matching (FE-03 fix)
      // Only try if no fenced blocks found (fenced is the preferred path)
      if (jsonBlocks.length === 0) {
        let i = 0;
        while (i < content.length) {
          if (content[i] === '{') {
            // Balanced brace matching
            let depth = 0;
            let inString = false;
            let escape = false;
            let end = -1;
            for (let j = i; j < content.length; j++) {
              const c = content[j];
              if (escape) { escape = false; continue; }
              if (c === '\\' && inString) { escape = true; continue; }
              if (c === '"') { inString = !inString; continue; }
              if (inString) continue;
              if (c === '{') depth++;
              else if (c === '}') {
                depth--;
                if (depth === 0) { end = j; break; }
              }
            }
            if (end > 0) {
              jsonBlocks.push(content.substring(i, end + 1));
              i = end + 1;
            } else {
              break; // Unbalanced - still streaming
            }
          } else {
            i++;
          }
        }
      }

      if (jsonBlocks.length === 0) {
        if (!content.includes('{')) setStatus('error');
        return;
      }

      let successCount = 0;
      let newBlocks = 0;
      for (const block of jsonBlocks) {
        // FE-02: skip already-dispatched blocks
        const blockKey = block;
        if (dispatchedRef.current.has(blockKey)) continue;

        try {
          const action = JSON.parse(block);
          if (action && action.command && ALLOWED_COMMANDS.has(action.command)) {
            dispatchedRef.current.add(blockKey);
            dispatchAction(action);
            successCount++;
            newBlocks++;
          }
        } catch {
          // Individual block failed, skip it
        }
      }

      if (successCount > 0 && newBlocks > 0) {
        setStatus('success');
      } else if (jsonBlocks.length > 0 && newBlocks === 0 && successCount === 0) {
        // All blocks were already dispatched or failed
        if (dispatchedRef.current.size === 0) setStatus('error');
      }
    } catch {
      setStatus('error');
    }
  }, [content, dispatchAction]);

  if (status === 'error') return null;

  return (
    <div className="my-2 flex items-center gap-2 rounded-md bg-blue-50/50 p-2 text-sm text-blue-600 dark:bg-blue-950/30 dark:text-blue-400">
      {status === 'parsing' ? (
        <MapIcon className="h-4 w-4 animate-pulse" />
      ) : (
        <CheckCircle2 className="h-4 w-4" />
      )}
      <span>{status === 'parsing' ? '正在连接地图终端...' : '地图指令已同步'}</span>
    </div>
  );
}

export default MapActionRenderer;
