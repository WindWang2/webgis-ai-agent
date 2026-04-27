"use client"

import { X, CheckCircle2 } from "lucide-react"
import type { UploadResponse } from "@/lib/api/upload"

interface UploadProgressProps {
  uploads: UploadResponse[]
  onRemove: (id: number) => void
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function getFormatIcon(format: string) {
  switch (format) {
    case "geojson": return "{ }"
    case "shapefile": return "SHP"
    case "geotiff": return "TIF"
    case "csv": return "CSV"
    case "kml": return "KML"
    case "gpkg": return "GPKG"
    default: return format.toUpperCase()
  }
}

export function UploadProgress({ uploads, onRemove }: UploadProgressProps) {
  if (uploads.length === 0) return null

  return (
    <div className="space-y-1.5">
      {uploads.map((u) => (
        <div
          key={u.id}
          className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border/50 bg-card/60 group"
        >
          {/* 格式标签 */}
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded bg-primary/10 text-[10px] font-bold text-primary">
            {getFormatIcon(u.format)}
          </div>

          {/* 文件信息 */}
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-foreground truncate">{u.original_name}</p>
            <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
              <span>{u.file_type === "raster" ? "栅格" : "矢量"}</span>
              {u.feature_count > 0 && <span>{u.feature_count.toLocaleString()} 要素</span>}
              <span>{formatSize(u.file_size)}</span>
            </div>
          </div>

          {/* 状态 */}
          <CheckCircle2 className="h-3.5 w-3.5 text-green-500 shrink-0" />

          {/* 删除 */}
          <button
            onClick={(e) => { e.stopPropagation(); onRemove(u.id) }}
            className="opacity-0 group-hover:opacity-100 p-1 hover:bg-red-100 hover:text-red-600 rounded transition-opacity shrink-0"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      ))}
    </div>
  )
}
