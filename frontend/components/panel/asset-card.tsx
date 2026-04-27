"use client"
import React, { useState } from "react"
import {
  FileText,
  Trash2,
  Edit,
  Download,
  Check,
  X,
  Map as MapIcon,
  Layers,
  Calendar,
  Image as ImageIcon,
  FileBarChart,
} from "lucide-react"
import { motion } from "framer-motion"
import { API_BASE } from '@/lib/api/config';

interface AssetCardProps {
  asset: any
  onLoad: (asset: any) => void
  onDelete: (id: number) => void
  onRename: (id: number, newName: string) => void
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// eslint-disable-next-line jsx-a11y/alt-text
function getAssetTypeInfo(asset: any): { icon: React.ReactNode; label: string; color: string } {
  const name = (asset.original_name || '').toLowerCase();
  const format = (asset.format || '').toLowerCase();

  if (name.includes('ndvi') || name.includes('ndwi') || name.includes('ndbi')) {
    return { icon: <ImageIcon className="h-4 w-4" />, label: name.toUpperCase().slice(0, name.indexOf('_') > -1 ? name.indexOf('_') : 4), color: 'text-emerald-400' };
  }
  if (name.includes('dem') || name.includes('dtm') || name.includes('dsm')) {
    return { icon: <FileBarChart className="h-4 w-4" />, label: '高程分析', color: 'text-amber-400' };
  }
  if (name.includes('class') || name.includes('分类')) {
    return { icon: <Layers className="h-4 w-4" />, label: '分类结果', color: 'text-purple-400' };
  }
  if (format.includes('tif') || format.includes('tiff') || format.includes('geotiff')) {
    return { icon: <ImageIcon className="h-4 w-4" />, label: '栅格数据', color: 'text-orange-400' };
  }
  if (format.includes('shp') || format.includes('geojson')) {
    return { icon: <Layers className="h-4 w-4" />, label: '矢量数据', color: 'text-hud-cyan' };
  }
  return { icon: <FileText className="h-4 w-4" />, label: '分析成果', color: 'text-hud-cyan' };
}

export function AssetCard({ asset, onLoad, onDelete, onRename }: AssetCardProps) {
  const [isEditing, setIsEditing] = useState(false)
  const [tempName, setTempName] = useState(asset.original_name)

  const typeInfo = getAssetTypeInfo(asset);

  const handleSaveRename = () => {
    onRename(asset.id, tempName)
    setIsEditing(false)
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="group relative bg-white/[0.02] border border-white/[0.06] rounded-xl overflow-hidden hover:bg-white/[0.04] hover:border-white/[0.1] transition-all"
    >
      {/* Gradient left border */}
      <div className="absolute top-0 left-0 w-[3px] h-full bg-gradient-to-b from-hud-cyan/40 via-hud-cyan/20 to-transparent" />

      <div className="flex items-start gap-3 p-3 pl-4">
        {/* Icon */}
        <div className={`
          h-9 w-9 rounded-lg flex items-center justify-center flex-shrink-0
          bg-white/[0.04] border border-white/[0.06]
          ${typeInfo.color}
        `}>
          {typeInfo.icon}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2 mb-1">
            {isEditing ? (
              <div className="flex items-center gap-1 flex-1">
                <input
                  type="text"
                  value={tempName}
                  onChange={(e) => setTempName(e.target.value)}
                  className="w-full text-[11px] bg-white/[0.06] border border-hud-cyan/30 rounded px-1.5 py-0.5 text-white/90 focus:outline-none focus:border-hud-cyan/60 transition-colors"
                  autoFocus
                  onKeyDown={(e) => e.key === "Enter" && handleSaveRename()}
                />
                <button onClick={handleSaveRename} className="text-hud-cyan hover:scale-110 transition-transform"><Check size={14} /></button>
                <button onClick={() => setIsEditing(false)} className="text-white/20 hover:text-white/50"><X size={14} /></button>
              </div>
            ) : (
              <h4
                className="text-[11px] font-medium text-white/70 truncate cursor-pointer hover:text-hud-cyan transition-colors"
                onDoubleClick={() => { setTempName(asset.original_name); setIsEditing(true); }}
                title={asset.original_name}
              >
                {asset.original_name}
              </h4>
            )}
          </div>

          <div className="flex items-center gap-3 text-[9px] text-white/25">
            <span className="flex items-center gap-1">
              <Calendar className="h-2.5 w-2.5" />
              {new Date(asset.upload_time || Date.now()).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })}
            </span>
            <span className="flex items-center gap-1">
              <FileText className="h-2.5 w-2.5" />
              {formatFileSize(asset.file_size || 0)}
            </span>
          </div>

          {/* Action Buttons */}
          <div className="flex items-center gap-1 mt-2.5 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
            <button
              onClick={() => onLoad(asset)}
              className="px-2 py-1 rounded-md bg-hud-cyan/[0.08] text-hud-cyan/80 text-[9px] font-semibold uppercase tracking-wider hover:bg-hud-cyan/15 hover:text-hud-cyan transition-all flex items-center gap-1"
            >
              <MapIcon size={10} /> 加载
            </button>

            <div className="flex-1" />

            <button
              onClick={() => { setTempName(asset.original_name); setIsEditing(true); }}
              className="p-1.5 rounded-md hover:bg-white/[0.04] text-white/20 hover:text-white/50 transition-all"
              title="重命名"
            >
              <Edit size={11} />
            </button>
            <button
              onClick={() => window.open(`${API_BASE}/api/v1/layers/data/${asset.id}?download=true`)}
              className="p-1.5 rounded-md hover:bg-white/[0.04] text-white/20 hover:text-white/50 transition-all"
              title="下载"
            >
              <Download size={11} />
            </button>
            <button
              onClick={() => onDelete(asset.id)}
              className="p-1.5 rounded-md hover:bg-red-500/10 text-white/15 hover:text-red-400 transition-all"
              title="删除"
            >
              <Trash2 size={11} />
            </button>
          </div>
        </div>

        {/* Type tag */}
        <span className={`
          text-[8px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded-full
          bg-white/[0.03] ${typeInfo.color} border border-white/[0.06]
          whitespace-nowrap flex-shrink-0
        `}>
          {typeInfo.label}
        </span>
      </div>
    </motion.div>
  )
}
