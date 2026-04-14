'use client';
import { useEffect, useState } from 'react';
import { useMapAction } from '@/lib/contexts/map-action-context';
import { MapIcon, CheckCircle2 } from 'lucide-react';

interface MapActionRendererProps {
  content: string;
}

export function MapActionRenderer({ content }: MapActionRendererProps) {
  const { dispatchAction } = useMapAction();
  const [status, setStatus] = useState<'parsing' | 'success' | 'error'>('parsing');

  useEffect(() => {
    if (!content || content === 'undefined' || content.trim() === '') {
      setStatus('error');
      return;
    }

    try {
      // Find all JSON blocks (either in ```json ... ``` or just { ... })
      const jsonBlocks: string[] = [];
      const regex = /```(?:json)?\s*([\s\S]*?)```|(\{[\s\S]*?\})/g;
      let match;

      while ((match = regex.exec(content)) !== null) {
        // match[1] is the content of ```json ... ```
        // match[2] is the content of { ... }
        const raw = match[1] || match[2];
        if (raw) jsonBlocks.push(raw.trim());
      }

      if (jsonBlocks.length === 0) {
        // Only set error if it doesn't look like JSON is starting
        if (!content.includes('{')) setStatus('error');
        return;
      }

      let successCount = 0;
      jsonBlocks.forEach(block => {
        try {
          const action = JSON.parse(block);
          if (action && action.command) {
            dispatchAction(action);
            successCount++;
          }
        } catch (e) {
          // Individual block failed, skip it
        }
      });

      if (successCount > 0) {
        setStatus('success');
      } else {
        setStatus('error');
      }
    } catch (e) {
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