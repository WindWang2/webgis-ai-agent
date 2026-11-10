import { useHudStore } from '@/lib/store/useHudStore';
import { useT } from '@/lib/i18n/useT';
import { useMemo, useEffect, useState, useRef } from 'react';

export function ExportMask() {
  const t = useT();
  const settings = useHudStore(s => s.exportSettings);
  const isDark = useHudStore(s => s.theme === 'dark');
  const [containerSize, setContainerSize] = useState({ w: 0, h: 0 });
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !el.parentElement) return;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        // Observe parent container size to avoid infinite loop when border is applied to self
        const { width, height } = entry.target.getBoundingClientRect();
        setContainerSize({ w: width, h: height });
      }
    });
    observer.observe(el.parentElement);
    return () => observer.disconnect();
  }, [settings.isExportMode]);

  const maskStyle = useMemo(() => {
    const baseStyle = { pointerEvents: 'none' as const, position: 'absolute' as const, inset: 0, zIndex: 20, borderWidth: 0 };
    if (settings.paperSize === 'screen' || containerSize.w === 0) return baseStyle;
    
    // A4 ratio: 1 : 1.414
    const targetRatio = settings.orientation === 'landscape' ? 1.414 : 1 / 1.414;
    
    // Calculate the maximum box fitting inside the container
    const containerRatio = containerSize.w / containerSize.h;
    let boxW = 0; let boxH = 0;
    if (containerRatio > targetRatio) {
      boxH = containerSize.h;
      boxW = boxH * targetRatio;
    } else {
      boxW = containerSize.w;
      boxH = boxW / targetRatio;
    }
    
    const padX = (containerSize.w - boxW) / 2;
    const padY = (containerSize.h - boxH) / 2;

    return {
      ...baseStyle,
      borderWidth: `${Math.max(0, padY)}px ${Math.max(0, padX)}px`,
      borderColor: 'rgba(0,0,0,0.6)',
      borderStyle: 'solid',
    };
  }, [settings.paperSize, settings.orientation, containerSize]);

  if (!settings.isExportMode) return null;

  return (
    <div ref={containerRef} style={maskStyle}>
       {/* PR-C：不再画本地 exportSettings.title 的预览标题 —— 标题/副标题是
           MapSpec 组件，live chrome（MapSpecChrome）已按 spec 渲染，与导出同源；
           此处再画一份会与 spec 标题重复且可能陈旧（Studio 曾与 Agent 各写一份）。 */}
       {/* Preview Watermark */}
       {settings.showWatermark && (
         <div className="absolute bottom-4 right-4 pointer-events-none opacity-50 text-sm font-mono font-bold" style={{ color: isDark ? 'var(--text-primary)' : 'var(--text-primary)' }}>
           {t('map.export.generatedBy')}
         </div>
       )}
    </div>
  );
}
