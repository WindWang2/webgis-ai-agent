'use client';

import { FileText, Image, MapPin } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useToastStore } from '@/components/ui/toast';
import { getUploadGeojson } from '@/lib/api/upload';
import type { GeoJSONFeatureCollection } from '@/lib/types';

// The /uploads endpoint returns more fields than UploadResponse declares,
// and the store keeps them as-is. This shape captures what AssetsTab reads.
interface AnalysisAsset {
  id: number | string;
  filename?: string;
  original_name?: string;
  name?: string;
  geometry_type?: string | null;
  type?: string;
  created_at?: string | null;
  uploaded_at?: string | null;
  file_size?: number | string | null;
  size?: number | string | null;
}

function formatDate(dateStr: string | undefined | null): string {
  if (!dateStr) return '--';
  try {
    return new Date(dateStr).toLocaleDateString('zh-CN', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return dateStr;
  }
}

function formatSize(bytes: number | string | undefined | null): string {
  if (bytes === undefined || bytes === null) return '--';
  const num = typeof bytes === 'string' ? parseInt(bytes, 10) : bytes;
  if (isNaN(num)) return String(bytes);
  if (num < 1024) return `${num} B`;
  if (num < 1024 * 1024) return `${(num / 1024).toFixed(1)} KB`;
  return `${(num / (1024 * 1024)).toFixed(1)} MB`;
}

const ASSET_COLORS = ['#16a34a', '#2563eb', '#ea580c', '#8b5cf6', '#ec4899'];

function colorFor(id: AnalysisAsset['id']): string {
  const key = String(id ?? '');
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash + key.charCodeAt(i)) | 0;
  return ASSET_COLORS[Math.abs(hash) % ASSET_COLORS.length];
}

export function AssetsTab() {
  const analysisAssets = useHudStore((s) => s.analysisAssets) as AnalysisAsset[];
  const addLayer = useHudStore((s) => s.addLayer);
  const addToast = useToastStore((s) => s.addToast);

  const handleLoadToMap = async (asset: AnalysisAsset) => {
    try {
      const geojson = await getUploadGeojson(Number(asset.id));
      if (!geojson?.features?.length) {
        addToast('该资产不包含可加载的要素', 'warning');
        return;
      }
      addLayer({
        id: `asset-${asset.id}`,
        name: asset.filename || asset.original_name || asset.name || 'Asset',
        type: 'vector',
        visible: true,
        opacity: 1,
        group: 'reference',
        source: geojson as unknown as GeoJSONFeatureCollection,
        style: { color: colorFor(asset.id) },
      });
      addToast('资产已加载到地图', 'success');
    } catch (e) {
      console.error('加载资产到地图失败:', e);
      addToast(e instanceof Error ? e.message : '加载资产失败', 'error');
    }
  };

  if (analysisAssets.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-6">
        <div className="w-10 h-10 rounded-xl bg-slate-100 flex items-center justify-center mb-2">
          <FileText size={16} className="text-slate-300" />
        </div>
        <p className="text-[11.5px] text-slate-400">暂无分析资产</p>
        <p className="text-[10px] text-slate-300 mt-0.5">
          上传数据或完成分析后将显示在此处
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="shrink-0 px-3 py-2 border-b border-slate-200/60">
        <span className="text-[9.5px] font-medium text-slate-400 uppercase tracking-wider">
          分析资产 ({analysisAssets.length})
        </span>
      </div>

      <div className="flex-1 overflow-y-auto px-2.5 py-2 space-y-2">
        {analysisAssets.map((asset) => {
          const isRaster =
            typeof asset.geometry_type === 'string'
              ? asset.geometry_type.startsWith('raster')
              : asset.type === 'raster';
          const dotColor = isRaster ? '#3b82f6' : '#16a34a';
          const Icon = isRaster ? Image : MapPin;

          return (
            <div
              key={asset.id}
              className="rounded-xl bg-white/70 border border-slate-100 p-2.5 transition-colors hover:bg-white/90"
            >
              {/* Header row */}
              <div className="flex items-start gap-2 mb-2">
                {/* Type dot */}
                <div
                  className="shrink-0 w-2.5 h-2.5 rounded-full mt-0.5"
                  style={{ backgroundColor: dotColor }}
                />
                <div className="flex-1 min-w-0">
                  {/* Filename */}
                  <div className="flex items-center gap-1.5">
                    <Icon size={11} className="text-slate-400 shrink-0" />
                    <span className="text-[11.5px] font-mono text-slate-700 truncate">
                      {asset.filename || asset.original_name || asset.name || 'unnamed'}
                    </span>
                  </div>
                  {/* Meta */}
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[9.5px] text-slate-300">
                      {formatDate(asset.created_at || asset.uploaded_at)}
                    </span>
                    <span className="text-[9.5px] text-slate-300">
                      {formatSize(asset.size || asset.file_size)}
                    </span>
                  </div>
                </div>
              </div>

              {/* Load button */}
              <button
                onClick={() => handleLoadToMap(asset)}
                className="w-full py-1.5 rounded-lg text-[10.5px] font-medium text-white transition-opacity hover:opacity-90"
                style={{ backgroundColor: dotColor }}
              >
                加载到地图
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default AssetsTab;
