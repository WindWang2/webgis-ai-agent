"use client"

import { useState, useRef, useCallback } from "react"
import { Upload, X, Loader2 } from "lucide-react"
import { uploadFile, type UploadResponse } from "@/lib/api/upload"

interface UploadZoneProps {
  sessionId?: string
  onUploadSuccess: (result: UploadResponse) => void
  compact?: boolean
}

const ACCEPTED_EXTENSIONS = [
  ".geojson", ".json", ".shp", ".zip", ".kml", ".gpkg",
  ".tif", ".tiff", ".csv",
]

function getFileTypeInfo(filename: string) {
  const ext = filename.split(".").pop()?.toLowerCase() || ""
  if (["tif", "tiff"].includes(ext)) return { type: "栅格", color: "text-orange-500" }
  if (["shp", "zip"].includes(ext)) return { type: "矢量", color: "text-green-500" }
  if (["geojson", "json"].includes(ext)) return { type: "矢量", color: "text-blue-500" }
  if (["kml"].includes(ext)) return { type: "矢量", color: "text-purple-500" }
  if (["gpkg"].includes(ext)) return { type: "矢量", color: "text-teal-500" }
  if (["csv"].includes(ext)) return { type: "CSV", color: "text-amber-500" }
  return { type: "未知", color: "text-gray-500" }
}

export function UploadZone({ sessionId, onUploadSuccess, compact }: UploadZoneProps) {
  const [isDragging, setIsDragging] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFile = useCallback(async (file: File) => {
    setIsUploading(true)
    setProgress(0)
    setError(null)

    try {
      const result = await uploadFile(file, sessionId, setProgress)
      onUploadSuccess(result)
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败")
    } finally {
      setIsUploading(false)
      setProgress(0)
    }
  }, [sessionId, onUploadSuccess])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }, [handleFile])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback(() => {
    setIsDragging(false)
  }, [])

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) handleFile(file)
    // 重置 input 以允许重复选择同一文件
    e.target.value = ""
  }, [handleFile])

  if (compact) {
    return (
      <div className="flex items-center gap-2">
        <label className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-lg border border-border hover:bg-card hover:border-primary/50 transition-all group">
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED_EXTENSIONS.join(",")}
            onChange={handleInputChange}
            className="hidden"
            disabled={isUploading}
          />
          {isUploading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
          ) : (
            <Upload className="h-3.5 w-3.5 text-muted-foreground group-hover:text-primary transition-colors" />
          )}
        </label>
        {isUploading && (
          <span className="text-xs text-muted-foreground">{progress}%</span>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => !isUploading && inputRef.current?.click()}
        className={`
          flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed
          p-4 cursor-pointer transition-all duration-200
          ${isDragging
            ? "border-primary bg-primary/5 scale-[1.01]"
            : "border-border/60 hover:border-primary/40 hover:bg-card/50"
          }
          ${isUploading ? "pointer-events-none opacity-60" : ""}
        `}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_EXTENSIONS.join(",")}
          onChange={handleInputChange}
          className="hidden"
          disabled={isUploading}
        />

        {isUploading ? (
          <>
            <Loader2 className="h-6 w-6 animate-spin text-primary" />
            <span className="text-xs text-muted-foreground">上传中 {progress}%</span>
            <div className="w-full max-w-40 h-1.5 bg-muted rounded-full overflow-hidden">
              <div
                className="h-full bg-primary rounded-full transition-all duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
          </>
        ) : (
          <>
            <Upload className="h-6 w-6 text-muted-foreground" />
            <div className="text-center">
              <p className="text-xs font-medium text-foreground">
                拖放或点击上传 GIS 数据
              </p>
              <p className="text-[10px] text-muted-foreground mt-0.5">
                GeoJSON / Shapefile / KML / GeoPackage / GeoTIFF / CSV
              </p>
            </div>
          </>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-1.5 px-3 py-1.5 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800/50 rounded-lg">
          <X className="h-3 w-3 text-red-500 shrink-0" />
          <span className="text-xs text-red-600 dark:text-red-400">{error}</span>
          <button onClick={() => setError(null)} className="ml-auto">
            <X className="h-3 w-3 text-red-400 hover:text-red-600" />
          </button>
        </div>
      )}
    </div>
  )
}

// 导出辅助函数
export { getFileTypeInfo }
