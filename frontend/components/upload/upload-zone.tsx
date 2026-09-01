"use client"

import { useState, useRef, useCallback } from "react"
import { Upload, X, Loader2 } from "lucide-react"
import { uploadFile, type UploadResponse } from "@/lib/api/upload"
import { useToastStore } from "@/components/ui/toast"

interface UploadZoneProps {
  sessionId?: string
  ownerToken?: string | null
  onUploadSuccess: (result: UploadResponse) => void
  compact?: boolean
}

const ACCEPTED_EXTENSIONS = [
  ".geojson", ".json", ".shp", ".zip", ".kml", ".gpkg",
  ".tif", ".tiff", ".csv",
]

function getFileTypeInfo(filename: string) {
  const ext = filename.split(".").pop()?.toLowerCase() || ""
  if (["tif", "tiff"].includes(ext)) return { type: "栅格", color: "text-status-warning" }
  if (["shp", "zip"].includes(ext)) return { type: "矢量", color: "text-status-success" }
  if (["geojson", "json"].includes(ext)) return { type: "矢量", color: "text-status-info" }
  if (["kml"].includes(ext)) return { type: "矢量", color: "text-status-accent" }
  if (["gpkg"].includes(ext)) return { type: "矢量", color: "text-status-accent" }
  if (["csv"].includes(ext)) return { type: "CSV", color: "text-status-warning" }
  return { type: "未知", color: "text-ink-muted" }
}

export function UploadZone({ sessionId, ownerToken, onUploadSuccess, compact }: UploadZoneProps) {
  const addToast = useToastStore((s) => s.addToast)
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
      const result = await uploadFile(file, sessionId, setProgress, {
        ownerToken: ownerToken ?? undefined,
      })
      onUploadSuccess(result)
      addToast(`${result.original_name} 上传成功`, "success")
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败")
    } finally {
      setIsUploading(false)
      setProgress(0)
    }
  }, [sessionId, ownerToken, onUploadSuccess, addToast])

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
        <label className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-lg border border-edge-subtle hover:bg-surface-raised hover:border-status-accent-border transition-all group">
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED_EXTENSIONS.join(",")}
            onChange={handleInputChange}
            className="hidden"
            disabled={isUploading}
          />
          {isUploading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-status-accent" />
          ) : (
            <Upload className="h-3.5 w-3.5 text-ink-muted group-hover:text-status-accent transition-colors" />
          )}
        </label>
        {isUploading && (
          <span className="text-caption text-ink-muted">{progress}%</span>
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
            ? "border-status-accent bg-status-accent-soft scale-[1.01]"
            : "border-edge-subtle hover:border-status-accent-border hover:bg-surface-hover"
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
            <Loader2 className="h-6 w-6 animate-spin text-status-accent" />
            <span className="text-caption text-ink-muted">上传中 {progress}%</span>
            <div className="w-full max-w-40 h-1.5 bg-surface-sunken rounded-full overflow-hidden">
              <div
                className="h-full bg-status-accent rounded-full transition-all duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
          </>
        ) : (
          <>
            <Upload className="h-6 w-6 text-ink-muted" />
            <div className="text-center">
              <p className="text-body font-medium text-ink">
                拖放或点击上传 GIS 数据
              </p>
              <p className="text-caption text-ink-muted mt-0.5">
                GeoJSON / Shapefile / KML / GeoPackage / GeoTIFF / CSV
              </p>
            </div>
          </>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-1.5 px-3 py-1.5 bg-status-critical-soft border border-status-critical-border rounded-lg" role="alert">
          <X className="h-3.5 w-3.5 text-status-critical shrink-0" />
          <span className="text-caption text-status-critical font-medium">{error}</span>
          <button onClick={() => setError(null)} aria-label="关闭错误提示" className="ml-auto cursor-pointer p-0.5 text-status-critical hover:opacity-80 transition-opacity">
            <X className="h-3 w-3" />
          </button>
        </div>
      )}
    </div>
  )
}

// 导出辅助函数
export { getFileTypeInfo }
