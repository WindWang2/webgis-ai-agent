'use client'

import { useMemo, useState } from 'react'

/**
 * POI 信息悬浮窗（点击交互 v2 重设计）。
 *
 * 设计约束：**纯 DOM**——不挂 MapLibre 图层、不做 moveLayer/z-order、不触
 * 发相机动画、不依赖地图投影（react-map-gl Popup 需要 project(lngLat)，地
 * 图渲染状态异常时它自身就是故障面）。本组件只用点击事件的屏幕坐标定位，
 * 结构上不可能弄坏画布。
 */

export interface PoiPanelFeature {
  layer?: { id?: string }
  properties?: Record<string, unknown>
}

interface PoiInfoPanelProps {
  /** 点击位置（相对地图容器的屏幕像素） */
  x: number
  y: number
  features: PoiPanelFeature[]
  layerIds: Set<string>
  layersMap: Record<string, { id?: string; name?: string } | undefined>
  onClose: () => void
}

const MAX_ROWS = 8

/** 解析要素显示名：name 类属性优先，否则首个属性值。 */
export function featureDisplayName(feature: PoiPanelFeature, fallback: string): string {
  const props = (feature.properties || {}) as Record<string, unknown>
  for (const key of ['name', 'NAME', 'Name', '名称']) {
    const v = props[key]
    if (typeof v === 'string' && v.trim()) return v
  }
  const first = Object.values(props)[0]
  if (typeof first === 'string' && first.trim()) return first
  return fallback
}

/** 最长前缀匹配父图层 id（与 map-panel 的 resolveParentLayerId 同语义）。 */
function parentLayerName(
  feature: PoiPanelFeature,
  layerIds: Set<string>,
  layersMap: Record<string, { id?: string; name?: string } | undefined>,
): { id?: string; name?: string } {
  const sub = feature.layer?.id
  if (!sub) return {}
  const sorted = Array.from(layerIds).sort((a, b) => b.length - a.length)
  const parent = sorted.find((id) => sub === id || sub.startsWith(id + '__'))
  if (!parent) return { id: sub }
  return { id: parent, name: layersMap[parent]?.name }
}

export function PoiInfoPanel({
  x,
  y,
  features,
  layerIds,
  layersMap,
  onClose,
}: PoiInfoPanelProps) {
  const [picked, setPicked] = useState<number>(features.length === 1 ? 0 : -1)
  const [dismissed, setDismissed] = useState(false)

  const entries = useMemo(
    () =>
      features.map((f, i) => {
        const meta = parentLayerName(f, layerIds, layersMap)
        return {
          idx: i,
          layerName: meta.name || meta.id || `要素 ${i + 1}`,
          title: featureDisplayName(f, `要素 ${i + 1}`),
          props: (f.properties || {}) as Record<string, unknown>,
        }
      }),
    [features, layerIds, layersMap],
  )

  if (dismissed || entries.length === 0) return null

  const above = y > 220
  const style: React.CSSProperties = {
    position: 'absolute',
    left: x,
    top: above ? y - 14 : y + 14,
    transform: above ? 'translate(-50%, -100%)' : 'translate(-50%, 0)',
    zIndex: 30,
  }

  const stop = (e: React.SyntheticEvent) => e.stopPropagation()
  const close = () => {
    setDismissed(true)
    onClose()
  }

  const current = picked >= 0 ? entries[picked] : null

  return (
    <div
      style={style}
      className="w-64 max-w-[80vw] overflow-hidden rounded-md border border-map-chrome-border bg-surface-panel shadow-lg"
      onPointerDown={stop}
      onClick={stop}
      onDoubleClick={stop}
    >
      <div className="flex items-center justify-between border-b border-map-chrome-border px-2 py-1">
        <div className="truncate font-sans text-meta font-semibold text-map-chrome-ink" title={current ? current.layerName : `同一点 ${entries.length} 个要素`}>
          {current ? current.layerName : `选择要素（${entries.length}）`}
        </div>
        <button
          type="button"
          aria-label="关闭"
          className="ml-1 shrink-0 rounded-xs px-1 text-map-chrome-ink-muted hover:bg-surface-hover hover:text-map-chrome-ink"
          onClick={close}
        >
          ×
        </button>
      </div>

      {current ? (
        <div className="p-2 font-sans text-meta">
          <div className="mb-1 truncate font-semibold text-map-chrome-ink" title={current.title}>
            {current.title}
          </div>
          <div className="max-h-48 space-y-0.5 overflow-y-auto">
            {Object.entries(current.props).slice(0, MAX_ROWS).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3">
                <span className="shrink-0 font-mono text-map-chrome-ink-muted">{k}:</span>
                <span className="break-all text-right font-mono text-map-chrome-ink">{String(v)}</span>
              </div>
            ))}
            {Object.keys(current.props).length > MAX_ROWS && (
              <div className="text-micro italic text-map-chrome-ink-muted">
                ...以及其他 {Object.keys(current.props).length - MAX_ROWS} 个属性
              </div>
            )}
            {Object.keys(current.props).length === 0 && (
              <div className="text-micro italic text-map-chrome-ink-muted">（无属性）</div>
            )}
          </div>
          {entries.length > 1 && (
            <button
              type="button"
              className="mt-1 w-full rounded-xs px-1 py-0.5 text-left text-micro text-map-chrome-ink-muted hover:bg-surface-hover"
              onClick={() => setPicked(-1)}
            >
              ← 返回要素列表
            </button>
          )}
        </div>
      ) : (
        <div className="p-1 font-sans text-meta">
          {entries.map((e) => (
            <button
              key={e.idx}
              type="button"
              className="block w-full rounded-xs px-1 py-0.5 text-left hover:bg-surface-hover"
              onClick={() => setPicked(e.idx)}
            >
              <span className="block truncate font-semibold text-map-chrome-ink" title={e.title}>
                {e.title}
              </span>
              <span className="block truncate font-mono text-micro text-map-chrome-ink-muted">
                {e.layerName}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
