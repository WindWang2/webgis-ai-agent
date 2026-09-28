'use client';

import React, { useEffect, useRef, useState } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { STitle } from '@/components/shared/section-title';
import { getSkills } from '@/lib/api/skills';
import { apiFetch, isApiError, describeApiError } from '@/lib/api/transport';
import { useT } from '@/lib/i18n/useT';

type UploadState =
  | { status: 'idle' }
  | { status: 'uploading' }
  | { status: 'success'; filename: string }
  | { status: 'error'; message: string };

/**
 * 技能分类表：value 是 store 语义值（demo 目录 / 后端目录共用，categoryOrder
 * 的 indexOf 排序与测试钉住的 `category: '工作流'` 都按值比对 —— 不是展示文案，
 * 不键化）；labelKey 是渲染时的消息键。
 */
const SKILL_CATEGORIES: Array<{ value: string; labelKey: string }> = [
  { value: '数据获取', labelKey: 'settings.skills.categories.dataAccess' },
  { value: '遥感分析', labelKey: 'settings.skills.categories.remoteSensing' },
  { value: '空间分析', labelKey: 'settings.skills.categories.spatial' },
  { value: '网络分析', labelKey: 'settings.skills.categories.network' },
  { value: '地形分析', labelKey: 'settings.skills.categories.terrain' },
  { value: '制图', labelKey: 'settings.skills.categories.cartography' },
  { value: '输出', labelKey: 'settings.skills.categories.output' },
  { value: '工作流', labelKey: 'settings.skills.categories.workflow' },
  { value: 'Other', labelKey: 'settings.skills.categories.other' },
];

const categoryOrder = SKILL_CATEGORIES.map((c) => c.value);
const categoryLabelKeys = new Map(SKILL_CATEGORIES.map((c) => [c.value, c.labelKey]));

/**
 * Skills Hub（#551 修复）。
 *
 * 此前的开关（skills[].enabled）没有任何消费方：ChatRequest 无 skills 字段，
 * 后端工具分发也不过滤技能列表 —— 关掉开关对 agent 行为零影响，是假控件。
 * 后端也没有"按技能名启用/禁用"的能力（仅 ChatRequest.skill_name 单技能激活，
 * 且属于 b11 工具分发改造范围）。按"宁可移除不可造假"原则：
 *   - 移除 enabled 开关与 enabled/calls 假字段（toggleSkill 一并删除）；
 *   - 本面板改为只读展示后端真实技能目录（/chat/skills）；
 *   - "Upload Custom Skill" 此前无 onClick —— 现在接到真实后端能力
 *     POST /api/v1/config/skills/upload（admin 专属，multipart file，
 *     写入 app/skills 并热加载）。
 */
export function SkillsHub() {
  const t = useT();
  const skills = useHudStore((s) => s.skills);
  const setSkills = useHudStore((s) => s.setSkills);

  const [upload, setUpload] = useState<UploadState>({ status: 'idle' });
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadSkills = (opts?: { forceRefresh?: boolean; signal?: AbortSignal }) => {
    getSkills(opts)
      .then((skillsList) => {
        if (!skillsList.length && !opts?.forceRefresh) return;
        setSkills(
          skillsList.map((sk) => ({
            id: sk.name,
            name: sk.name,
            desc: sk.description,
            category: '工作流',
          }))
        );
      })
      .catch((err: unknown) => {
        // AbortError on unmount is expected; log real failures only.
        if (isApiError(err) || (err instanceof Error && err.name !== 'AbortError')) {
          console.warn('SkillsHub: failed to load skills', err);
        }
      });
  };

  useEffect(() => {
    const controller = new AbortController();
    loadSkills({ signal: controller.signal });
    return () => controller.abort();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleUpload = async (file: File) => {
    setUpload({ status: 'uploading' });
    const form = new FormData();
    form.append('file', file, file.name);
    try {
      const data = await apiFetch<{ status: string; filename: string }>(
        '/api/v1/config/skills/upload',
        {
          method: 'POST',
          rawBody: form,
          label: 'Skill upload error',
        }
      );
      setUpload({ status: 'success', filename: data.filename ?? file.name });
      // 上传热加载后立即刷新目录（forceRefresh 绕过 5s 缓存）。
      loadSkills({ forceRefresh: true });
    } catch (err) {
      setUpload({
        status: 'error',
        message: isApiError(err) && err.status === 403
          ? t('settings.skills.uploadForbidden')
          : describeApiError(err, t('settings.skills.uploadFailed')),
      });
    }
  };

  const pickFromUploadState = upload.status !== 'idle'
    ? `${upload.status === 'uploading' ? t('settings.skills.uploading') : upload.status === 'success' ? 'Uploaded' : 'Failed'}`
    : 'Upload Custom Skill';

  /* Group skills by category */
  const grouped = skills.reduce<Record<string, typeof skills>>((acc, sk) => {
    const cat = sk.category || 'Other';
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(sk);
    return acc;
  }, {});

  const sortedCategories = Object.keys(grouped).sort((a, b) => {
    const ia = categoryOrder.indexOf(a);
    const ib = categoryOrder.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });

  const categoryLabel = (category: string): string => {
    const key = categoryLabelKeys.get(category);
    return key ? t(key) : category;
  };

  return (
    <div className="flex flex-col gap-5">
      <STitle title="Skills Hub" sub={t('settings.skills.subtitle')} />

      {sortedCategories.map((category) => (
        <div key={category}>
          <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold mb-2">
            {categoryLabel(category)}
          </div>
          <div className="flex flex-col gap-1.5">
            {grouped[category].map((sk) => (
              <div
                key={sk.id}
                className="flex items-center gap-3 rounded-md border border-edge-subtle bg-surface-raised px-3 py-2.5 transition-all"
              >
                {/* Name + description（只读目录 —— 状态开关已移除，#551） */}
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className="text-body font-medium text-ink truncate">
                    {sk.name}
                  </span>
                </div>
                <div className="text-body text-ink-muted truncate flex-1">
                  {sk.desc}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}

      {/* Upload custom skill — 真实上传：POST /api/v1/config/skills/upload（admin） */}
      <div>
        <input
          ref={fileInputRef}
          type="file"
          accept=".py,.md"
          className="hidden"
          data-testid="skill-file-input"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) void handleUpload(file);
          }}
        />
        <button
          type="button"
          disabled={upload.status === 'uploading'}
          onClick={() => fileInputRef.current?.click()}
          className="flex w-full items-center justify-center gap-2 rounded-md border-2 border-dashed border-edge-subtle bg-surface-raised py-3 text-body font-medium text-ink-muted transition-all hover:border-edge-strong hover:text-ink-secondary disabled:opacity-50"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <line x1="8" y1="3" x2="8" y2="13" />
            <line x1="3" y1="8" x2="13" y2="8" />
          </svg>
          {pickFromUploadState}
        </button>
        {upload.status === 'success' && (
          <div className="mt-2 text-body font-medium text-status-success">
            {t('settings.skills.uploaded', { name: upload.filename })}
          </div>
        )}
        {upload.status === 'error' && (
          <div className="mt-2 text-body font-medium text-status-critical">
            {upload.message}
          </div>
        )}
      </div>
    </div>
  );
}