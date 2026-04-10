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
      // Try to extract JSON from markdown code blocks or find the first { ... } block
      let jsonStr = content.trim();
      
      const codeBlockMatch = jsonStr.match(/```(?:json)?\s*([\s\S]*?)```/);
      if (codeBlockMatch) {
        jsonStr = codeBlockMatch[1].trim();
      } else {
        const jsonMatch = jsonStr.match(/(\{[\s\S]*\})/);
        if (jsonMatch) {
          jsonStr = jsonMatch[1].trim();
        }
      }

      const action = JSON.parse(jsonStr);
      if (action && action.command) {
        dispatchAction(action);
        setStatus('success');
      } else {
        setStatus('error');
      }
    } catch (e) {
      // For streaming, we might have incomplete JSON, so we stay in 'parsing' state
      // unless we are sure it's not JSON
      if (content.includes('{') && !content.includes('}')) {
        setStatus('parsing');
      } else {
        // Only set error if it doesn't look like it's still being built
        if (content.length > 100 && !content.includes('{')) {
          setStatus('error');
        }
      }
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
      <span>{status === 'parsing' ? '正在解析地图指令...' : '已更新地图图层'}</span>
    </div>
  );
}

export default MapActionRenderer;