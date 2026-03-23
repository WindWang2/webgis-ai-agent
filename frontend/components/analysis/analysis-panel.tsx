'use client';

import { useState } from 'react';
import { Zap, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import { AnalysisType, ANALYSIS_TYPES } from './analysis-types';
import { TaskProgress, Task } from './task-progress';

interface AnalysisPanelProps {
  selectedLayerId?: string;
  onTaskSubmit?: (task: Task) => void;
}

interface AnalysisParams {
  type: string;
  layerId: string;
  bufferDistance?: number;
  bufferUnit?: 'meters' | 'kilometers' | 'degrees';
  intersectLayers?: string[];
  dissolveField?: string;
  gridSize?: number;
  classificationMethod?: 'equal_interval' | 'quantile' | 'natural_breaks';
  classificationCount?: number;
  [key: string]: unknown;
}

export function AnalysisPanel({ selectedLayerId, onTaskSubmit }: AnalysisPanelProps) {
  const [selectedType, setSelectedType] = useState<AnalysisType | null>(null);
  const [params, setParams] = useState<AnalysisParams>({
    type: '',
    layerId: selectedLayerId || '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const handleTypeSelect = (type: AnalysisType) => {
    setSelectedType(type);
    setParams(prev => ({ ...prev, type: type.id }));
    setSuccess(false);
  };

  const handleParamChange = (key: string, value: unknown) => {
    setParams(prev => ({ ...prev, [key]: value }));
  };

  const validateParams = (): string | null => {
    if (!params.layerId) {
      return '请选择要分析的图层';
    }

    switch (params.type) {
      case 'buffer':
        if (!params.bufferDistance || params.bufferDistance <= 0) {
          return '请输入有效的缓冲区距离';
        }
        break;
      case 'intersect':
      case 'union':
      case 'erase':
        if (!params.intersectLayers || params.intersectLayers.length === 0) {
          return '请选择要叠加的图层';
        }
        break;
      case 'grid':
        if (!params.gridSize || params.gridSize <= 0) {
          return '请输入有效的网格大小';
        }
        break;
      case 'classify':
        if (!params.classificationCount || params.classificationCount < 2) {
          return '分类数量必须大于等于 2';
        }
        break;
    }

    return null;
  };

  const handleSubmit = async () => {
    const validationError = validateParams();
    if (validationError) {
      setError(validationError);
      return;
    }

    setSubmitting(true);
    setError(null);
    setSuccess(false);

    try {
      const response = await fetch('/api/analysis/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.message || '提交失败');
      }

      const data = await response.json();
      setSuccess(true);
      onTaskSubmit?.(data.task);

      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交失败，请重试');
    } finally {
      setSubmitting(false);
    }
  };

  const renderParamInputs = () => {
    if (!selectedType) return null;

    switch (selectedType.id) {
      case 'buffer':
        return (
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                缓冲区距离
              </label>
              <div className="flex gap-2">
                <input
                  type="number"
                  value={params.bufferDistance || ''}
                  onChange={(e) => handleParamChange('bufferDistance', parseFloat(e.target.value))}
                  placeholder="请输入距离"
                  className="flex-1 px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                />
                <select
                  value={params.bufferUnit || 'meters'}
                  onChange={(e) => handleParamChange('bufferUnit', e.target.value)}
                  className="px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                >
                  <option value="meters">米</option>
                  <option value="kilometers">千米</option>
                  <option value="degrees">度</option>
                </select>
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                融合字段（可选）
              </label>
              <input
                type="text"
                value={params.dissolveField || ''}
                onChange={(e) => handleParamChange('dissolveField', e.target.value)}
                placeholder="按字段融合结果"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
        );

      case 'intersect':
      case 'union':
      case 'erase':
        return (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              选择叠加图层
            </label>
            <p className="text-xs text-gray-500 mb-2">
              按住 Ctrl 可多选
            </p>
            <select
              multiple
              value={params.intersectLayers || []}
              onChange={(e) => handleParamChange('intersectLayers', Array.from(e.target.selectedOptions, opt => opt.value))}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 h-32"
            >
              {/* Options will be populated from layer list */}
              <option value="sample1">示例图层 1</option>
              <option value="sample2">示例图层 2</option>
            </select>
          </div>
        );

      case 'grid':
        return (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              网格大小
            </label>
            <div className="flex gap-2">
              <input
                type="number"
                value={params.gridSize || ''}
                onChange={(e) => handleParamChange('gridSize', parseFloat(e.target.value))}
                placeholder="请输入网格大小"
                className="flex-1 px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
              />
              <select
                className="px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
              >
                <option value="meters">米</option>
                <option value="kilometers">千米</option>
              </select>
            </div>
          </div>
        );

      case 'classify':
        return (
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                分类数量
              </label>
              <input
                type="number"
                min="2"
                max="15"
                value={params.classificationCount || ''}
                onChange={(e) => handleParamChange('classificationCount', parseInt(e.target.value))}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                分类方法
              </label>
              <select
                value={params.classificationMethod || 'equal_interval'}
                onChange={(e) => handleParamChange('classificationMethod', e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
              >
                <option value="equal_interval">等间距</option>
                <option value="quantile">分位数</option>
                <option value="natural_breaks">自然断点</option>
              </select>
            </div>
          </div>
        );

      default:
        return null;
    }
  };

  return (
    <div className="w-full">
      <h3 className="text-lg font-semibold text-gray-800 flex items-center gap-2 mb-4">
        <Zap className="h-5 w-5" />
        空间分析
      </h3>

      {/* Analysis Type Selection */}
      <div className="grid grid-cols-2 gap-2 mb-4">
        {ANALYSIS_TYPES.map((type) => (
          <button
            key={type.id}
            onClick={() => handleTypeSelect(type)}
            className={`
              p-3 border rounded-lg text-left transition-all
              ${selectedType?.id === type.id
                ? 'border-blue-500 bg-blue-50'
                : 'border-gray-200 hover:border-gray-300'}
            `}
          >
            <type.icon className="h-5 w-5 mb-1" />
            <p className="text-sm font-medium">{type.name}</p>
            <p className="text-xs text-gray-500">{type.description}</p>
          </button>
        ))}
      </div>

      {/* Parameter Inputs */}
      {selectedType && (
        <div className="mb-4">
          <h4 className="text-sm font-medium text-gray-700 mb-2">参数配置</h4>
          {renderParamInputs()}
        </div>
      )}

      {/* Error/Success Messages */}
      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg flex items-start gap-2">
          <AlertCircle className="h-5 w-5 text-red-500 flex-shrink-0 mt-0.5" />
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}

      {success && (
        <div className="mb-4 p-3 bg-green-50 border border-green-200 rounded-lg flex items-center gap-2">
          <CheckCircle2 className="h-5 w-5 text-green-500" />
          <p className="text-sm text-green-700">任务提交成功！</p>
        </div>
      )}

      {/* Submit Button */}
      <button
        onClick={handleSubmit}
        disabled={!selectedType || submitting}
        className={`
          w-full py-2.5 px-4 rounded-lg font-medium transition-colors
          flex items-center justify-center gap-2
          ${!selectedType || submitting
            ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
            : 'bg-blue-600 text-white hover:bg-blue-700'}
        `}
      >
        {submitting ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            提交中...
          </>
        ) : (
          '提交分析任务'
        )}
      </button>

      {/* Task Progress */}
      <TaskProgress />
    </div>
  );
}
