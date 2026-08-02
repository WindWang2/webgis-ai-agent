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
  const travelTime = payload.travel_time_min ?? result.travel_time_min ?? 15;
  const mode = (payload.mode ?? result.mode ?? 'walking').toString().toLowerCase();
  const facilityCount = payload.facility_count ?? result.facility_count ?? 1;
  const areaKm2 = payload.area_km2 ?? result.area_km2 ?? null;
  const summaryText = payload.summary ?? (typeof result.summary === 'string' ? result.summary : null);

  const ModeIcon = mode === 'driving' || mode === 'car' ? Car : Footprints;
  const modeLabel = mode === 'driving' || mode === 'car' ? '驾车' : '步行';

  return (
    <div className="my-2 p-3 rounded-lg border border-slate-200/80 bg-white/70 dark:border-slate-800 dark:bg-slate-900/70 backdrop-blur-sm text-sm">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5 font-semibold text-slate-800 dark:text-slate-200">
          <Clock className="h-4 w-4 text-emerald-500 shrink-0" />
          <span>等时圈网络分析 ({travelTime} 分钟)</span>
        </div>
        <span className="flex items-center gap-1 text-[12px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-300 font-medium">
          <ModeIcon className="h-3 w-3" />
          {modeLabel}模式
        </span>
      </div>

      {/* Metrics Row */}
      <div className="grid grid-cols-2 gap-2 mb-2 p-2 rounded bg-emerald-50/40 dark:bg-emerald-950/20 border border-emerald-100/50 dark:border-emerald-900/30">
        <div className="flex items-center gap-2">
          <MapPin className="h-4 w-4 text-emerald-500 shrink-0" />
          <div>
            <div className="text-[11px] text-slate-400 dark:text-slate-500">设施点数量</div>
            <div className="text-[13px] font-mono font-bold text-slate-700 dark:text-slate-200">
              {facilityCount} 个设施
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Layers className="h-4 w-4 text-emerald-500 shrink-0" />
          <div>
            <div className="text-[11px] text-slate-400 dark:text-slate-500 font-medium">覆盖面积</div>
            <div className="text-[13px] font-mono font-bold text-slate-700 dark:text-slate-200">
              {areaKm2 !== null ? `${areaKm2.toFixed(2)} km²` : `${travelTime} 分钟圈`}
            </div>
          </div>
        </div>
      </div>

      {/* Summary Note */}
      {summaryText && (
        <div className="text-[12px] text-slate-600 dark:text-slate-300 leading-relaxed mb-2 bg-slate-50/80 dark:bg-slate-800/40 p-2 rounded border border-slate-100 dark:border-slate-800">
          {summaryText}
        </div>
      )}

      {/* Action Footer */}
      <div className="flex items-center justify-between pt-1 border-t border-slate-100 dark:border-slate-800 text-[12px]">
        <span className="text-slate-400 dark:text-slate-500 font-mono">
          速度基准: {modeLabel === '驾车' ? '400m/min' : '80m/min'}
        </span>
        {layerId && onFocus && (
          <button
            type="button"
            onClick={() => onFocus(layerId)}
            className="inline-flex items-center gap-1 font-medium text-emerald-600 hover:text-emerald-700 dark:text-emerald-400 dark:hover:text-emerald-300 hover:underline transition-colors"
          >
            <Target className="h-3 w-3" />
            高亮图层
          </button>
        )}
      </div>
    </div>
  );
}

export default IsochroneResultCard;
