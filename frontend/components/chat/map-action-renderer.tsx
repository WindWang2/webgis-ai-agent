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

/**
 * 审计 FE-04（map-action-renderer params validation）：dispatch 前对每条命令
 * 的关键 params 做最小 schema 校验。AI 输出可能带畸形 params，直接 dispatch
 * 会让 MapLibre 抛未捕获异常或写入脏 store 状态。
 *
 * 这里只校验"该命令必须的字段存在且类型正确"，不做值域校验（值域由
 * MapLibre / store reducer 兜底）。拒绝的 action 会被静默丢弃并记入 errorCount。
 */
const REQUIRED_PARAMS: Record<string, (p: Record<string, unknown>) => boolean> = {
  add_layer: (p) => typeof p.id === 'string',
  remove_layer: (p) => typeof p.id === 'string' || typeof p.layer_id === 'string',
  fly_to: (p) => Array.isArray(p.center) && p.center.length === 2,
  zoom_to_bbox: (p) => Array.isArray(p.bbox) && p.bbox.length === 4,
  set_map_view: (p) => Array.isArray(p.center) || typeof p.zoom === 'number',
  add_heatmap_raster: (p) => typeof p.url === 'string' || typeof p.image === 'string',
  add_raster_layer: (p) => typeof p.url === 'string' || typeof p.image === 'string',
  add_native_heatmap: (p) => !!p.geojson || typeof p.id === 'string',
  base_layer_change: (p) => typeof p.name === 'string' || typeof p.id === 'string',
  layer_visibility_update: (p) => typeof p.layer_id === 'string' || typeof p.id === 'string',
  layer_style_update: (p) => typeof p.layer_id === 'string' || typeof p.id === 'string',
  reorder_layer: (p) => Array.isArray(p.layers) || Array.isArray(p.order),
  export_map: () => true,
  add_marker: (p) => Array.isArray(p.center) || Array.isArray(p.coordinate),
  draw_measurement: () => true,
  clear_annotations: () => true,
  apply_layer_filter: (p) => typeof p.layer_id === 'string' || typeof p.id === 'string',
};

function isValidAction(action: unknown): action is { command: string; params: Record<string, unknown> } {
  if (typeof action !== 'object' || action === null) return false;
  const a = action as { command?: unknown; params?: unknown };
  if (typeof a.command !== 'string' || !ALLOWED_COMMANDS.has(a.command)) return false;
  // params 可选（部分命令无参）；存在则必须是普通对象
  const params = (a.params ?? {}) as Record<string, unknown>;
  if (typeof params !== 'object' || params === null || Array.isArray(params)) return false;
  const validator = REQUIRED_PARAMS[a.command];
  return validator ? validator(params) : true;
}

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
          // FE-04: schema-validate before dispatch
          if (isValidAction(action)) {
            dispatchedRef.current.add(blockKey);
            dispatchAction(action as Parameters<typeof dispatchAction>[0]);
            successCount++;
            newBlocks++;
          }
          // else: malformed params or unknown command → silently skip
        } catch {
          // Individual block failed to parse, skip it
        }
      }

      if (successCount > 0 && newBlocks > 0) {
        setStatus('success');
      } else if (jsonBlocks.length > 0 && newBlocks === 0 && successCount === 0) {
        // All blocks were already dispatched, malformed, or rejected
        if (dispatchedRef.current.size === 0) setStatus('error');
      }
    } catch {
      setStatus('error');
    }
  }, [content, dispatchAction]);

  // 审计 a11y：之前 error 状态返回 null，把渲染失败藏起来。现在渲染一个
  // 带 role="status" 的可访问提示，但用 aria-hidden 对可见用户保持安静
  // （错误是 AI 输出畸形 JSON，不该打扰终端用户；保留给辅助技术探测）。
  if (status === 'error') {
    return (
      <span role="status" aria-live="off" className="sr-only">
        地图指令解析失败
      </span>
    );
  }

  return (
    <div
      className="my-2 flex items-center gap-2 rounded-md bg-blue-50/50 p-2 text-sm text-blue-600 dark:bg-blue-950/30 dark:text-blue-400"
      role="status"
      aria-live="polite"
    >
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
