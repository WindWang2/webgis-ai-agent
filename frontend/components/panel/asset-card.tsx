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
  Calendar
} from "lucide-react"
import { motion } from "framer-motion"
import { API_BASE } from '@/lib/api/config';

interface AssetCardProps {
  asset: any
  onLoad: (asset: any) => void
  onDelete: (id: number) => void
  onRename: (id: number, newName: string) => void
}

export function AssetCard({ asset, onLoad, onDelete, onRename }: AssetCardProps) {
  const [isEditing, setIsEditing] = useState(false)
  const [tempName, setTempName] = useState(asset.original_name)

  const handleSaveRename = () => {
    onRename(asset.id, tempName)
    setIsEditing(false)
  }

  return (
    <motion.div 
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="group relative bg-white/[0.02] border border-white/[0.06] rounded-xl p-3 hover:bg-white/[0.04] transition-all"
    >
      <div className="flex items-start gap-3">
        {/* Icon / Thumbnail Placeholder */}
        <div className="h-10 w-10 rounded-lg bg-hud-cyan/10 flex items-center justify-center flex-shrink-0 border border-hud-cyan/20">
          <Layers className="h-5 w-5 text-hud-cyan/60" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2 mb-1">
            {isEditing ? (
              <div className="flex items-center gap-1 flex-1">
                <input
                  type="text"
                  value={tempName}
                  onChange={(e) => setTempName(e.target.value)}
                  className="w-full text-[11px] bg-ds-black border border-hud-cyan/40 rounded px-1.5 py-0.5 text-white focus:outline-none focus:border-hud-cyan"
                  autoFocus
                  onKeyDown={(e) => e.key === "Enter" && handleSaveRename()}
                />
                <button onClick={handleSaveRename} className="text-hud-cyan hover:scale-110 transition-transform"><Check size={14} /></button>
                <button onClick={() => setIsEditing(false)} className="text-white/30 hover:text-white/60"><X size={14} /></button>
              </div>
            ) : (
              <h4 
                className="text-[11px] font-semibold text-white/80 truncate cursor-pointer hover:text-hud-cyan transition-colors"
                onDoubleClick={() => setIsEditing(true)}
              >
                {asset.original_name}
              </h4>
            )}
          </div>

          <div className="flex items-center gap-3 text-[9px] text-white/30">
            <span className="flex items-center gap-1">
              <Calendar className="h-2.5 w-2.5" />
              {new Date(asset.upload_time || Date.now()).toLocaleDateString()}
            </span>
            <span className="flex items-center gap-1">
              <FileText className="h-2.5 w-2.5" />
              {(asset.file_size / 1024 / 1024).toFixed(2)} MB
            </span>
          </div>

          {/* Action Buttons */}
          <div className="flex items-center gap-1.5 mt-3 opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              onClick={() => onLoad(asset)}
              className="px-2 py-1 rounded bg-hud-cyan/10 text-hud-cyan text-[9px] font-bold uppercase tracking-wider hover:bg-hud-cyan/20 transition-all flex items-center gap-1"
            >
              <MapIcon size={10} /> 加载到地图
            </button>
            
            <div className="flex-1" />

            <button
              onClick={() => setIsEditing(true)}
              className="p-1.5 rounded hover:bg-white/5 text-white/30 hover:text-white/60 transition-colors"
              title="重命名"
            >
              <Edit size={12} />
            </button>
            <button
              onClick={() => window.open(`${API_BASE}/api/v1/layers/data/${asset.id}?download=true`)}
              className="p-1.5 rounded hover:bg-white/5 text-white/30 hover:text-white/60 transition-colors"
              title="下载 TIFF"
            >
              <Download size={12} />
            </button>
            <button
              onClick={() => onDelete(asset.id)}
              className="p-1.5 rounded hover:bg-red-500/10 text-red-500/40 hover:text-red-500 transition-colors"
              title="删除资产"
            >
              <Trash2 size={12} />
            </button>
          </div>
        </div>
      </div>

      {/* Subtle analysis type tag */}
      <div className="absolute top-2 right-2 text-[8px] font-bold text-hud-cyan/40 uppercase tracking-widest pointer-events-none">
        NDVI Result
      </div>
    </motion.div>
  )
}
