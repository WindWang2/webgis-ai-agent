'use client';

import { Footprints, Car, Target, Clock, MapPin, Layers } from 'lucide-react';

export interface IsochroneResultPayload {
  travel_time_min?: number;
  mode?: string;
  facility_count?: number;
  area_km2?: number;
  max_dist_m?: number;
  summary?: string;
  [key: string]: unknown;
}

interface Props {
  result: {
    result?: IsochroneResultPayload;
    metadata?: IsochroneResultPayload;
    summary?: string;
    travel_time_min?: number;
    mode?: string;
    facility_count?: number;
    area_km2?: number;
    [key: string]: unknown;
  } | null | undefined;
  layerId?: string;
  onFocus?: (layerId: string) => void;
}

export function IsochroneResultCard({ result, layerId, onFocus }: Props) {
  if (!result) return null;

  const payload = (result.result as IsochroneResultPayload) ?? (result.metadata as IsochroneResultPayload) ?? result;
  // #692 真实性：不再用 ?? 15 / ?? 1 虚构指标——缺数据显示"未知"或不渲染该行
  const travelTime = payload.travel_time_min ?? result.travel_time_min ?? null;
  const mode = (payload.mode ?? result.mode ?? 'walking').toString().toLowerCase();
  const facilityCount = payload.facility_count ?? result.facility_count ?? null;
  const areaKm2 = payload.area_km2 ?? result.area_km2 ?? null;
  const summaryText = payload.summary ?? (typeof result.summary === 'string' ? result.summary : null);

  const ModeIcon = mode === 'driving' || mode === 'car' ? Car : Footprints;
  const modeLabel = mode === 'driving' || mode === 'car' ? '驾车' : '步行';

  return (
    /* C：去掉 backdrop-blur-md，把 bg-white/80 dark:bg-slate-900/80 半透明对
       收敛为单一语义 token —— 结果卡浮在聊天气泡上，半透明+blur 只增加合成
       成本且让底下的正文透出来。 */
    <div className="my-2 p-3.5 rounded-md border border-edge-subtle bg-surface-raised text-body shadow-raised transition-all">
      {/* Header */}
      <div className="flex items-center justify-between mb-2.5">
        <div className="flex items-center gap-1.5 font-semibold text-ink">
          <Clock className="h-4 w-4 text-status-success shrink-0" />
          <span>等时圈网络分析{travelTime !== null ? ` (${travelTime} 分钟)` : ''}</span>
        </div>
        <span className="flex items-center gap-1 text-meta px-2 py-0.5 rounded-pill bg-status-success-soft text-status-success font-medium">
          <ModeIcon className="h-3.5 w-3.5" />
          {modeLabel}模式
        </span>
      </div>

      {/* Metrics Row */}
      <div className="grid grid-cols-2 gap-2 mb-2.5 p-2.5 rounded-md bg-status-success-soft border border-status-success-border">
        <div className="flex items-center gap-2">
          <MapPin className="h-4 w-4 text-status-success shrink-0" />
          <div>
            <div className="text-meta text-ink-muted font-medium">设施点数量</div>
            <div className="text-body font-mono font-bold text-ink">
              {facilityCount !== null ? `${facilityCount} 个设施` : '设施数未知'}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Layers className="h-4 w-4 text-status-success shrink-0" />
          <div>
            <div className="text-meta text-ink-muted font-medium">覆盖面积</div>
            <div className="text-body font-mono font-bold text-ink">
              {areaKm2 !== null ? `${areaKm2.toFixed(2)} km²` : (travelTime !== null ? `${travelTime} 分钟圈` : '范围未知')}
            </div>
          </div>
        </div>
      </div>

      {/* Summary Note */}
      {summaryText && (
        <div className="text-body text-ink-secondary leading-relaxed mb-2.5 bg-surface-sunken p-2.5 rounded-md border border-edge-subtle">
          {summaryText}
        </div>
      )}

      {/* Action Footer */}
      <div className="flex items-center justify-between pt-1.5 border-t border-edge-subtle text-meta">
        <span className="text-ink-muted font-mono">
          速度基准: {modeLabel === '驾车' ? '400m/min' : '80m/min'}
        </span>
        {layerId && onFocus && (
          <button
            type="button"
            onClick={() => onFocus(layerId)}
            className="inline-flex items-center gap-1 font-medium text-status-success hover:underline transition-colors"
          >
            <Target className="h-3.5 w-3.5" />
            高亮图层
          </button>
        )}
      </div>
    </div>
  );
}

export default IsochroneResultCard;
