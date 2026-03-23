'use client';

import { useState } from 'react';
import { LayerUpload } from '@/components/layer/layer-upload';
import { LayerList, Layer } from '@/components/layer/layer-list';
import { AnalysisPanel } from '@/components/analysis/analysis-panel';
import { MapWithLayers } from '@/components/map/map-with-layers';
import { Task } from '@/components/analysis/task-progress';
import { Layers, Map as MapIcon, Zap, Upload } from 'lucide-react';

type PanelTab = 'layers' | 'analysis' | 'upload';

export default function HomePage() {
  const [activeTab, setActiveTab] = useState<PanelTab>('layers');
  const [layers, setLayers] = useState<Layer[]>([]);
  const [selectedLayerId, setSelectedLayerId] = useState<string | undefined>();
  const [analysisResultLayerId, setAnalysisResultLayerId] = useState<string | undefined>();

  const handleUploadComplete = (layerId: string, fileName: string) => {
    const newLayer: Layer = {
      id: layerId,
      name: fileName.replace(/\.[^/.]+$/, ''),
      fileName,
      format: fileName.split('.').pop()?.toLowerCase() || 'unknown',
      size: 0,
      createdAt: new Date().toISOString(),
      bounds: [-180, -90, 180, 90],
      isVisible: true,
      opacity: 1,
    };
    setLayers(prev => [...prev, newLayer]);
    setSelectedLayerId(layerId);
  };

  const handleLayerToggle = (layerId: string, visible: boolean) => {
    setLayers(prev => prev.map(l =>
      l.id === layerId ? { ...l, isVisible: visible } : l
    ));
  };

  const handleLayerOpacityChange = (layerId: string, opacity: number) => {
    setLayers(prev => prev.map(l =>
      l.id === layerId ? { ...l, opacity } : l
    ));
  };

  const handleLayerDelete = (layerId: string) => {
    setLayers(prev => prev.filter(l => l.id !== layerId));
    if (selectedLayerId === layerId) {
      setSelectedLayerId(undefined);
    }
  };

  const handleTaskSubmit = (task: Task) => {
    console.log('Task submitted:', task);
  };

  const handleTaskComplete = (task: Task) => {
    if (task.status === 'completed' && task.resultLayerId) {
      setAnalysisResultLayerId(task.resultLayerId);

      // Add result layer to layer list
      const resultLayer: Layer = {
        id: task.resultLayerId,
        name: `分析结果 - ${task.id}`,
        fileName: `result_${task.id}.geojson`,
        format: 'geojson',
        size: 0,
        createdAt: new Date().toISOString(),
        bounds: [-180, -90, 180, 90],
        isVisible: true,
        opacity: 0.7,
      };
      setLayers(prev => [...prev, resultLayer]);
      setSelectedLayerId(task.resultLayerId);
    }
  };

  return (
    <div className="h-screen flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <h1 className="text-xl font-bold text-gray-900">WebGIS AI Agent</h1>
        <p className="text-sm text-gray-500 mt-1">智能地图分析与处理系统</p>
      </header>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Panel - Layer Management & Analysis */}
        <aside className="w-80 bg-white border-r border-gray-200 flex flex-col overflow-hidden">
          {/* Tab Navigation */}
          <div className="flex border-b border-gray-200">
            <button
              onClick={() => setActiveTab('layers')}
              className={`flex-1 py-3 text-sm font-medium transition-colors
                ${activeTab === 'layers' ? 'text-blue-600 border-b-2 border-blue-600' : 'text-gray-600 hover:text-gray-900'}
              `}
            >
              <div className="flex items-center justify-center gap-2">
                <Layers className="h-4 w-4" />
                图层
              </div>
            </button>
            <button
              onClick={() => setActiveTab('upload')}
              className={`flex-1 py-3 text-sm font-medium transition-colors
                ${activeTab === 'upload' ? 'text-blue-600 border-b-2 border-blue-600' : 'text-gray-600 hover:text-gray-900'}
              `}
            >
              <div className="flex items-center justify-center gap-2">
                <Upload className="h-4 w-4" />
                上传
              </div>
            </button>
            <button
              onClick={() => setActiveTab('analysis')}
              className={`flex-1 py-3 text-sm font-medium transition-colors
                ${activeTab === 'analysis' ? 'text-blue-600 border-b-2 border-blue-600' : 'text-gray-600 hover:text-gray-900'}
              `}
            >
              <div className="flex items-center justify-center gap-2">
                <Zap className="h-4 w-4" />
                分析
              </div>
            </button>
          </div>

          {/* Tab Content */}
          <div className="flex-1 overflow-y-auto p-4">
            {activeTab === 'layers' && (
              <LayerList
                layers={layers}
                selectedLayerId={selectedLayerId}
                onLayerToggle={handleLayerToggle}
                onLayerOpacityChange={handleLayerOpacityChange}
                onLayerDelete={handleLayerDelete}
                onLayerSelect={setSelectedLayerId}
              />
            )}

            {activeTab === 'upload' && (
              <LayerUpload
                onUploadComplete={handleUploadComplete}
              />
            )}

            {activeTab === 'analysis' && (
              <AnalysisPanel
                selectedLayerId={selectedLayerId}
                onTaskSubmit={handleTaskSubmit}
              />
            )}
          </div>
        </aside>

        {/* Center Panel - Map */}
        <main className="flex-1 min-w-0">
          <MapWithLayers
            layers={layers}
            selectedLayerId={selectedLayerId}
            analysisResultLayerId={analysisResultLayerId}
            onLayerSelect={setSelectedLayerId}
          />
        </main>

        {/* Right Panel - Results (Optional, can be expanded later) */}
        <aside className="w-72 bg-white border-l border-gray-200 overflow-y-auto">
          <div className="p-4">
            <h3 className="text-lg font-semibold text-gray-800 flex items-center gap-2 mb-4">
              <MapIcon className="h-5 w-5" />
              分析结果
            </h3>

            {analysisResultLayerId ? (
              <div className="space-y-4">
                <div className="p-3 bg-green-50 border border-green-200 rounded-lg">
                  <p className="text-sm font-medium text-green-800">分析已完成</p>
                  <p className="text-xs text-green-600 mt-1">
                    结果已添加到图层列表
                  </p>
                </div>

                <div className="text-sm text-gray-600">
                  <p className="font-medium mb-2">结果说明</p>
                  <p className="text-xs text-gray-500">
                    分析结果图层已自动加载到地图中，您可以在左侧图层列表中查看和管理。
                  </p>
                </div>
              </div>
            ) : (
              <div className="text-center py-8 text-gray-500 text-sm">
                <MapIcon className="h-12 w-12 mx-auto mb-3 opacity-30" />
                <p>暂无分析结果</p>
                <p className="text-xs mt-1">提交分析任务后查看结果</p>
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
