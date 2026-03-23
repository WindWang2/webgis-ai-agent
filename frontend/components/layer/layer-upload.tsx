'use client';

import { useState, useCallback, useRef } from 'react';
import { Upload, File, X, AlertCircle } from 'lucide-react';

interface LayerUploadProps {
  onUploadComplete?: (layerId: string, fileName: string) => void;
  onError?: (error: string) => void;
}

interface SupportedFormat {
  ext: string;
  name: string;
  mime: string[];
}

const SUPPORTED_FORMATS: SupportedFormat[] = [
  { ext: '.geojson', name: 'GeoJSON', mime: ['application/geo+json', 'application/json'] },
  { ext: '.json', name: 'JSON', mime: ['application/json'] },
  { ext: '.shp', name: 'Shapefile', mime: ['application/x-shp'] },
  { ext: '.kml', name: 'KML', mime: ['application/vnd.google-earth.kml+xml'] },
  { ext: '.kmz', name: 'KMZ', mime: ['application/vnd.google-earth.kmz'] },
  { ext: '.gpkg', name: 'GeoPackage', mime: ['application/geopackage+sqlite3'] },
  { ext: '.tif', name: 'GeoTIFF', mime: ['image/tiff'] },
  { ext: '.tiff', name: 'GeoTIFF', mime: ['image/tiff'] },
];

export function LayerUpload({ onUploadComplete, onError }: LayerUploadProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const validateFile = (file: File): boolean => {
    const fileName = file.name.toLowerCase();
    const isValidFormat = SUPPORTED_FORMATS.some(
      format => fileName.endsWith(format.ext)
    );

    if (!isValidFormat) {
      setError(`不支持的文件格式：${file.name}。支持格式：${SUPPORTED_FORMATS.map(f => f.ext).join(', ')}`);
      return false;
    }

    if (file.size > 100 * 1024 * 1024) { // 100MB
      setError(`文件过大：${file.name}。最大支持 100MB`);
      return false;
    }

    return true;
  };

  const uploadFile = async (file: File) => {
    if (!validateFile(file)) return;

    setUploading(true);
    setProgress(0);
    setError(null);

    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await fetch('/api/layers/upload', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.message || '上传失败');
      }

      const data = await response.json();
      setProgress(100);

      onUploadComplete?.(data.layerId, file.name);
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : '上传失败，请重试';
      setError(errorMsg);
      onError?.(errorMsg);
    } finally {
      setUploading(false);
      setTimeout(() => setProgress(0), 1000);
    }
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);

    const file = e.dataTransfer.files[0];
    if (file) {
      uploadFile(file);
    }
  }, []);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      uploadFile(file);
    }
  };

  return (
    <div className="w-full">
      <div
        className={`
          border-2 border-dashed rounded-lg p-8 text-center
          transition-colors cursor-pointer
          ${isDragging ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-gray-400'}
          ${uploading ? 'pointer-events-none opacity-50' : ''}
        `}
        onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
      >
        <Upload className="mx-auto h-12 w-12 text-gray-400 mb-4" />
        <p className="text-sm font-medium text-gray-700 mb-2">
          拖拽文件到此处，或点击选择
        </p>
        <p className="text-xs text-gray-500 mb-4">
          支持：{SUPPORTED_FORMATS.map(f => f.name).join(', ')}
        </p>
        <p className="text-xs text-gray-400">
          最大 100MB
        </p>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        className="hidden"
        onChange={handleFileSelect}
        accept={SUPPORTED_FORMATS.map(f => f.ext).join(',')}
      />

      {uploading && (
        <div className="mt-4">
          <div className="flex items-center justify-between text-sm text-gray-600 mb-1">
            <span>上传中...</span>
            <span>{progress}%</span>
          </div>
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div
              className="bg-blue-600 h-2 rounded-full transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

      {error && (
        <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg flex items-start gap-2">
          <AlertCircle className="h-5 w-5 text-red-500 flex-shrink-0 mt-0.5" />
          <p className="text-sm text-red-700">{error}</p>
          <button
            onClick={() => setError(null)}
            className="ml-auto text-red-500 hover:text-red-700"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  );
}
